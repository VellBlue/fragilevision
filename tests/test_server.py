from __future__ import annotations

from io import BytesIO
import json
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from fragilevision.server import App, Server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(Path(self.temp.name))
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, path):
        with urlopen(self.base + path, timeout=3) as response:
            return response.status, json.loads(response.read())

    def post(self, path, body, token=None):
        request = Request(self.base + path, method="POST", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json",
                                   "X-FragileVision-Token": token or ""})
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def delete(self, path, token=None):
        request = Request(self.base + path, method="DELETE",
                          headers={"X-FragileVision-Token": token or ""})
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_bootstrap_and_csrf(self):
        status, bootstrap = self.get("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(bootstrap["state"]["projects"], [])
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/projects", {"name": "Demo"})
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        status, project = self.post("/api/projects", {"name": "Demo"}, bootstrap["token"])
        self.assertEqual(status, 201)
        self.assertEqual(project["slug"], "demo")

        with patch("fragilevision.server.choose_directory", return_value="/tmp/example-dataset"):
            status, selected = self.post("/api/system/choose-directory", {"purpose": "dataset"}, bootstrap["token"])
        self.assertEqual(status, 200)
        self.assertEqual(selected["directory"], "/tmp/example-dataset")

        with patch("fragilevision.server.discover_models", return_value=["local-vlm"]):
            status, catalogue = self.post("/api/providers/discover", {
                "kind": "ollama", "endpoint": "http://127.0.0.1:11434",
            }, bootstrap["token"])
        self.assertEqual(status, 200)
        self.assertEqual(catalogue["models"], ["local-vlm"])

        status, provider = self.post("/api/providers", {
            "name": "Synthetic Prompt Stressor", "kind": "simulator",
            "model": "ignored", "endpoint": "https://8.8.8.8",
        }, bootstrap["token"])
        self.assertEqual(status, 201)
        saved = self.app.db.row("select * from providers where id=?", (provider["id"],))
        self.assertEqual(saved["is_demo"], 1)
        self.assertEqual(saved["endpoint"], "http://127.0.0.1")
        sample = Path(self.temp.name) / "sample.png"
        sample.write_bytes(b"synthetic-image-bytes")
        image_id = self.app.db.execute("""insert into images
            (project_id,sha256,filename,stored_path,source_group,mime,created_at)
            values(?,?,?,?,?,?,?)""", (project["id"], "a" * 64, "sample.png", str(sample),
            "test-scene", "image/png", self.app.db.now()))
        self.assertGreater(image_id, 0)
        status, tested = self.post(f"/api/providers/{provider['id']}/test",
                                   {"project_id": project["id"]}, bootstrap["token"])
        self.assertEqual(status, 200)
        self.assertTrue(tested["ok"])

        status, project_detail = self.post(f"/api/projects/{project['id']}/questions", {
            "label": "Visibilità", "key": "visible", "text": "L’oggetto è visibile?", "language": "it",
        }, bootstrap["token"])
        self.assertEqual(status, 201)
        question_id = project_detail["questions"][0]["id"]
        status, provider_b = self.post("/api/providers", {
            "name": "Synthetic Prompt Stressor B", "kind": "simulator",
            "model": "ignored", "endpoint": "https://8.8.8.8",
        }, bootstrap["token"])
        self.assertEqual(status, 201)
        with patch.object(self.app, "start_batch") as started:
            status, batch = self.post("/api/arena/runs", {
                "project_id": project["id"], "provider_ids": [provider["id"], provider_b["id"]],
                "question_ids": [question_id], "name": "Arena test", "repetitions": 2,
                "temperature": 0, "seed": 9,
            }, bootstrap["token"])
        self.assertEqual(status, 202)
        self.assertEqual(len(batch["run_ids"]), 2)
        started.assert_called_once_with(batch["run_ids"])
        saved_runs = [self.app.run_detail(run_id) for run_id in batch["run_ids"]]
        self.assertEqual({run["config"]["seed"] for run in saved_runs}, {9})
        self.assertEqual({run["config"]["repetitions"] for run in saved_runs}, {2})
        self.app.db.execute("update runs set status='cancelled' where project_id=? and status='queued'", (project["id"],))

        with patch.object(self.app, "arena", return_value={"models": [{"run_id": 1}]}) as compared:
            status, arena = self.get("/api/arena?run_ids=1,2,1")
        self.assertEqual(status, 200)
        self.assertEqual(arena["models"][0]["run_id"], 1)
        compared.assert_called_once_with([1, 2])

        run_id = self.app.db.execute("""insert into runs
            (project_id,provider_id,name,status,config_json,created_at)
            values(?,?,?,'completed','{}',?)""", (project["id"], provider["id"], "Da eliminare", self.app.db.now()))
        status, deleted = self.delete(f"/api/runs/{run_id}", bootstrap["token"])
        self.assertEqual(status, 200)
        self.assertEqual(deleted["deleted_id"], run_id)
        self.assertIsNone(self.app.db.row("select id from runs where id=?", (run_id,)))

        status, trashed_image = self.post(f"/api/images/{image_id}/trash", {}, bootstrap["token"])
        self.assertEqual(status, 200)
        self.assertEqual(trashed_image["trashed_id"], image_id)
        self.assertEqual(self.get(f"/api/projects/{project['id']}")[1]["images"], [])
        status, trash = self.get("/api/trash")
        self.assertEqual([item["id"] for item in trash["images"]], [image_id])
        self.post(f"/api/images/{image_id}/restore", {}, bootstrap["token"])
        self.assertEqual(len(self.get(f"/api/projects/{project['id']}")[1]["images"]), 1)

        self.post(f"/api/projects/{project['id']}/trash", {}, bootstrap["token"])
        self.assertEqual(self.get("/api/state")[1]["projects"], [])
        self.assertEqual([item["id"] for item in self.get("/api/trash")[1]["projects"]], [project["id"]])
        self.post(f"/api/projects/{project['id']}/restore", {}, bootstrap["token"])
        self.assertEqual([item["id"] for item in self.get("/api/state")[1]["projects"]], [project["id"]])

    def test_a_foreign_host_header_is_rejected(self):
        """DNS rebinding: a page resolving its domain to 127.0.0.1 must not become same-origin."""
        import http.client

        def fetch(host):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
            connection.putrequest("GET", "/api/bootstrap", skip_host=True, skip_accept_encoding=True)
            if host:
                connection.putheader("Host", host)
            connection.endheaders()
            response = connection.getresponse()
            payload = response.read()
            connection.close()
            return response.status, payload

        status, payload = fetch(f"127.0.0.1:{self.server.server_port}")
        self.assertEqual(status, 200)
        self.assertIn(b"token", payload)
        for hostile in ("evil.example.com", f"evil.example.com:{self.server.server_port}", ""):
            status, payload = fetch(hostile)
            self.assertEqual(status, 403, hostile)
            self.assertNotIn(b'"token"', payload)

    def test_static_traversal_is_rejected(self):
        with self.assertRaises(HTTPError) as denied:
            urlopen(self.base + "/../db.py", timeout=3)
        self.assertEqual(denied.exception.code, 404)
        denied.exception.close()

    def test_worker_records_local_input_provenance(self):
        now = self.app.db.now()
        project_id = self.app.db.execute(
            "insert into projects(name,slug,description,created_at) values('Input','input','',?)", (now,))
        provider_id = self.app.db.execute("""insert into providers
            (name,kind,endpoint,model,is_demo,created_at) values('Demo','ollama','http://127.0.0.1','demo',1,?)""", (now,))
        source = Path(self.temp.name) / "small.jpg"
        payload = b"small-local-model-input"
        source.write_bytes(payload)
        image_id = self.app.db.execute("""insert into images
            (project_id,sha256,filename,stored_path,mime,width,height,created_at)
            values(?,?,?,?,?,?,?,?)""", (project_id, hashlib.sha256(payload).hexdigest(), "small.jpg",
            str(source), "image/jpeg", 32, 32, now))
        question_id = self.app.db.execute("""insert into questions
            (project_id,key,label,description,created_at) values(?,'visible','Visible','',?)""", (project_id, now))
        self.app.db.execute("""insert into variants
            (question_id,name,language,text,mutation_type,canonical,created_at)
            values(?,'Canonica','it','È visibile?','canonical',1,?)""", (question_id, now))
        run_id = self.app.db.execute("""insert into runs
            (project_id,provider_id,name,status,config_json,created_at)
            values(?,?,?,'queued',?,?)""", (project_id, provider_id, "Provenienza",
            json.dumps({"question_ids":[question_id]}), now))

        self.app._run_worker(run_id)

        response = self.app.db.row("select * from responses where run_id=?", (run_id,))
        self.assertEqual(self.app.run_detail(run_id)["status"], "completed")
        self.assertEqual(response["image_id"], image_id)
        self.assertEqual(response["input_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(response["input_preprocess"], "original")


    def build_run(self, images=2, repetitions=1):
        """A minimal demo-provider run: images the size of a stub JPEG header."""
        now = self.app.db.now()
        project_id = self.app.db.execute(
            "insert into projects(name,slug,description,created_at) values('Ripresa','ripresa','',?)", (now,))
        provider_id = self.app.db.execute("""insert into providers
            (name,kind,endpoint,model,is_demo,created_at) values('Demo','ollama','http://127.0.0.1','demo',1,?)""", (now,))
        image_ids = []
        for index in range(images):
            payload = f"local-image-{index}".encode()
            source = Path(self.temp.name) / f"case-{index}.jpg"
            source.write_bytes(payload)
            image_ids.append(self.app.db.execute("""insert into images
                (project_id,sha256,filename,stored_path,mime,width,height,created_at)
                values(?,?,?,?,?,?,?,?)""", (project_id, hashlib.sha256(payload).hexdigest(),
                source.name, str(source), "image/jpeg", 32, 32, now)))
        question_id = self.app.db.execute("""insert into questions
            (project_id,key,label,description,created_at) values(?,'visible','Visible','',?)""", (project_id, now))
        variant_id = self.app.db.execute("""insert into variants
            (question_id,name,language,text,mutation_type,canonical,created_at)
            values(?,'Canonica','it','È visibile?','canonical',1,?)""", (question_id, now))
        run_id = self.app.db.execute("""insert into runs
            (project_id,provider_id,name,status,config_json,created_at)
            values(?,?,?,'queued',?,?)""", (project_id, provider_id, "Ripresa",
            json.dumps({"question_ids":[question_id],"repetitions":repetitions}), now))
        return run_id, variant_id, image_ids

    def store_response(self, run_id, image_id, variant_id, *, error=None):
        self.app.db.execute("""insert or replace into responses
            (run_id,image_id,variant_id,repetition,answer,raw,format_valid,parser,prompt_text,error,created_at)
            values(?,?,?,0,'yes','CHECKPOINT',1,'json','p',?,?)""",
            (run_id, image_id, variant_id, error, self.app.db.now()))

    def test_a_resumed_run_never_repeats_a_settled_response(self):
        run_id, variant_id, image_ids = self.build_run(images=2)
        self.store_response(run_id, image_ids[0], variant_id)
        self.app.db.execute("update runs set status='paused' where id=?", (run_id,))

        run = self.app.run_detail(run_id)
        pending, total = self.app.pending_units(run)
        self.assertEqual(total, 2)
        self.assertEqual(len(pending), 1)
        self.assertEqual(int(pending[0][2]["id"]), image_ids[1])

        self.app._run_worker(run_id)

        kept = self.app.db.row("select raw from responses where run_id=? and image_id=?", (run_id, image_ids[0]))
        self.assertEqual(kept["raw"], "CHECKPOINT")  # the checkpoint was not paid for twice
        detail = self.app.run_detail(run_id)
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["completed"], 2)
        self.assertIsNone(detail["error"])

    def test_a_failed_call_is_retried_while_an_unreadable_answer_is_not(self):
        """An unparseable answer is a result; a call that never landed is not."""
        run_id, variant_id, image_ids = self.build_run(images=2)
        self.store_response(run_id, image_ids[0], variant_id, error="ConnectionError: rifiutata")
        self.store_response(run_id, image_ids[1], variant_id)

        pending, total = self.app.pending_units(self.app.run_detail(run_id))
        self.assertEqual(total, 2)
        self.assertEqual([int(unit[2]["id"]) for unit in pending], [image_ids[0]])

    def test_resume_refuses_a_run_that_is_already_going(self):
        run_id, _, _ = self.build_run(images=1)
        self.app.db.execute("update runs set status='running' where id=?", (run_id,))
        with self.assertRaises(ValueError):
            self.app.resume_run(run_id)

    def test_resume_closes_a_run_with_nothing_left_to_do(self):
        run_id, variant_id, image_ids = self.build_run(images=1)
        self.store_response(run_id, image_ids[0], variant_id)
        self.app.db.execute("update runs set status='failed' where id=?", (run_id,))
        result = self.app.resume_run(run_id)
        self.assertEqual(result["resumed"], 0)
        self.assertEqual(self.app.run_detail(run_id)["status"], "completed")

    def test_a_restart_pauses_an_interrupted_run_instead_of_failing_it(self):
        run_id, _, _ = self.build_run(images=1)
        self.app.db.execute("update runs set status='running' where id=?", (run_id,))
        restarted = App(Path(self.temp.name))
        detail = restarted.run_detail(run_id)
        self.assertEqual(detail["status"], "paused")
        self.assertIn("Riprendi", detail["error"])

    def test_pause_is_refused_on_a_run_that_is_not_going(self):
        run_id, _, _ = self.build_run(images=1)
        self.app.db.execute("update runs set status='completed' where id=?", (run_id,))
        with self.assertRaises(HTTPError) as caught:
            self.post(f"/api/runs/{run_id}/pause", {}, token=self.app.token)
        self.assertEqual(caught.exception.code, 400)

    def test_runtime_accumulates_across_a_pause(self):
        run_id, _, _ = self.build_run(images=1)
        self.app._run_worker(run_id)
        first = self.app.run_detail(run_id)["runtime_seconds"]
        self.assertGreater(first, 0)
        self.app.db.execute("update runs set status='paused' where id=?", (run_id,))
        self.app._run_worker(run_id)
        self.assertGreater(self.app.run_detail(run_id)["runtime_seconds"], first)


    def test_a_run_can_be_confined_to_one_side_of_the_split(self):
        """A split nothing executes against is decoration."""
        run_id, _, image_ids = self.build_run(images=4)
        for position, image_id in enumerate(image_ids):
            self.app.db.execute("update images set split=? where id=?",
                                ("test" if position < 2 else "train", image_id))
        self.app.db.execute("update runs set config_json=? where id=?",
                            (json.dumps({"question_ids": [1], "repetitions": 1, "split": "test"}), run_id))

        pending, total = self.app.pending_units(self.app.run_detail(run_id))
        self.assertEqual(total, 2)
        self.assertEqual({int(unit[2]["id"]) for unit in pending}, set(image_ids[:2]))

    def test_a_run_on_an_empty_split_says_so_instead_of_reporting_nothing(self):
        run_id, _, _ = self.build_run(images=2)
        self.app.db.execute("update runs set config_json=? where id=?",
                            (json.dumps({"question_ids": [1], "repetitions": 1, "split": "test"}), run_id))
        self.app._run_worker(run_id)
        detail = self.app.run_detail(run_id)
        self.assertEqual(detail["status"], "failed")
        self.assertIn("suddivisione", detail["error"])


    def build_panel(self, images=6):
        """A project with images and one question, ready for several reviewers."""
        now = self.app.db.now()
        project_id = self.app.db.execute(
            "insert into projects(name,slug,description,created_at) values('Panel','panel','',?)", (now,))
        image_ids = []
        for index in range(images):
            payload = f"panel-{index}".encode()
            source = Path(self.temp.name) / f"panel-{index}.jpg"
            source.write_bytes(payload)
            image_ids.append(self.app.db.execute("""insert into images
                (project_id,sha256,filename,stored_path,mime,width,height,created_at)
                values(?,?,?,?,?,?,?,?)""", (project_id, hashlib.sha256(payload).hexdigest(),
                source.name, str(source), "image/jpeg", 32, 32, now)))
        question_id = self.app.db.execute("""insert into questions
            (project_id,key,label,description,created_at) values(?,'luce','Luce','',?)""", (project_id, now))
        self.app.db.execute("""insert into variants
            (question_id,name,language,text,mutation_type,canonical,created_at)
            values(?,'Canonica','it','Si vede una luce?','canonical',1,?)""", (question_id, now))
        return project_id, question_id, image_ids

    def test_a_second_reviewer_turns_one_opinion_into_a_consensus(self):
        project_id, question_id, image_ids = self.build_panel()
        first = self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                      annotator="Simone", value="yes")
        self.assertEqual(first["consensus"]["agreement"], "single")

        second = self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                       annotator="anna", value="yes")
        self.assertEqual(second["consensus"]["agreement"], "unanimous")
        self.assertEqual(second["consensus"]["label_count"], 2)
        # Ground truth one person wrote alone and ground truth two people reached
        # independently are different evidence, so they must not hash alike.
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_an_even_split_is_excluded_from_the_evaluation_until_a_person_rules(self):
        project_id, question_id, image_ids = self.build_panel()
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="Simone", value="yes")
        conflicted = self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                           annotator="anna", value="no")
        self.assertEqual(conflicted["consensus"]["agreement"], "conflict")
        self.assertEqual(conflicted["consensus"]["value"], "uncertain")
        contested = self.app.contested_cases(project_id)
        self.assertEqual(contested["unresolved"], 1)

        ruled = self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                      annotator="Simone", value="no", is_adjudication=True)
        self.assertEqual((ruled["consensus"]["agreement"], ruled["consensus"]["value"]), ("adjudicated", "no"))
        self.assertEqual(ruled["consensus"]["adjudicated_by"], "Simone")
        self.assertEqual(self.app.contested_cases(project_id)["unresolved"], 0)

    def test_a_case_nobody_disputes_cannot_be_overruled(self):
        _, question_id, image_ids = self.build_panel()
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="Simone", value="yes")
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="anna", value="yes")
        with self.assertRaises(ValueError):
            self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                  annotator="Carla", value="no", is_adjudication=True)

    def test_withdrawing_a_judgement_rewrites_the_consensus(self):
        _, question_id, image_ids = self.build_panel()
        for reviewer, value in (("Simone", "yes"), ("anna", "no"), ("Carla", "yes")):
            self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                  annotator=reviewer, value=value)
        self.assertEqual(self.app.db.row("select agreement from annotations where image_id=?",
                                         (image_ids[0],))["agreement"], "majority")
        removed = self.app.remove_label(image_id=image_ids[0], question_id=question_id, annotator="Carla")
        self.assertEqual(removed["consensus"]["agreement"], "conflict")
        self.app.remove_label(image_id=image_ids[0], question_id=question_id, annotator="anna")
        self.app.remove_label(image_id=image_ids[0], question_id=question_id, annotator="Simone")
        # The last judgement leaving takes the derived ground truth with it.
        self.assertIsNone(self.app.db.row("select value from annotations where image_id=?", (image_ids[0],)))
        with self.assertRaises(ValueError):
            self.app.remove_label(image_id=image_ids[0], question_id=question_id, annotator="Simone")

    def test_reviewer_names_are_matched_without_regard_to_case(self):
        """"Simone" and "simone" are one person, not a two-person panel."""
        _, question_id, image_ids = self.build_panel()
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="Simone", value="yes")
        revised = self.app.record_label(image_id=image_ids[0], question_id=question_id,
                                        annotator="simone", value="no")
        self.assertEqual((revised["consensus"]["agreement"], revised["consensus"]["value"]), ("single", "no"))

    def test_the_board_hides_nothing_it_should_not_and_finds_the_reviewer_own_work(self):
        project_id, question_id, image_ids = self.build_panel()
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="Simone", value="yes")
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="anna", value="yes")
        self.app.record_label(image_id=image_ids[1], question_id=question_id, annotator="Simone", value="no")
        board = self.app.annotation_board(project_id, question_id, "anna")
        self.assertEqual(board["mine_done"], 1)
        self.assertEqual(board["annotations"][0]["mine"]["annotator"], "anna")
        self.assertEqual(board["annotations"][0]["others"], 1)
        self.assertIsNone(board["annotations"][1]["mine"])
        self.assertEqual(board["consensus"]["verified"], 1)
        self.assertEqual({item["annotator"] for item in board["annotators"]}, {"Simone", "anna"})

    def test_a_run_reports_how_much_of_its_ground_truth_was_checked(self):
        run_id, variant_id, image_ids = self.build_run(images=4)
        question_id = self.app.db.row("select question_id from variants where id=?", (variant_id,))["question_id"]
        for index, image_id in enumerate(image_ids):
            self.app.record_label(image_id=image_id, question_id=question_id,
                                  annotator="Simone", value="yes" if index % 2 else "no")
            if index < 2:
                self.app.record_label(image_id=image_id, question_id=question_id,
                                      annotator="anna", value="yes" if index % 2 else "no")
            self.store_response(run_id, image_id, variant_id)
        summary = self.app.metrics(run_id)["summary"]
        self.assertEqual((summary["annotated_cases"], summary["verified_cases"]), (4, 2))
        self.assertAlmostEqual(summary["verified_share"], 0.5)
        self.assertEqual(summary["single_annotator_cases"], 2)
        reliability = next(item for item in self.app.metrics(run_id)["evidence_gate"]["checks"]
                           if item["key"] == "reliability")
        self.assertTrue(reliability["passed"])

    def test_the_replay_bundle_carries_every_individual_judgement(self):
        run_id, variant_id, image_ids = self.build_run(images=2)
        question_id = self.app.db.row("select question_id from variants where id=?", (variant_id,))["question_id"]
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="Simone", value="yes")
        self.app.record_label(image_id=image_ids[0], question_id=question_id, annotator="anna", value="no")
        self.store_response(run_id, image_ids[0], variant_id)
        self.app.db.execute("update runs set status='completed' where id=?", (run_id,))
        archive = zipfile.ZipFile(BytesIO(self.app.bundle(run_id)))
        manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(len(manifest["annotation_labels"]), 2)
        self.assertEqual({item["annotator"] for item in manifest["annotation_labels"]}, {"Simone", "anna"})
        self.assertEqual(manifest["agreement"]["annotator_count"], 2)
        self.assertIn("Krippendorff", archive.read("report.html").decode())
        self.assertIn("| Metric | Value |", archive.read("report.md").decode())

    def test_the_markdown_report_route_reaches_the_api(self):
        run_id, variant_id, image_ids = self.build_run(images=1)
        self.store_response(run_id, image_ids[0], variant_id)
        self.app.db.execute("update runs set status='completed' where id=?", (run_id,))
        request = Request(f"{self.base}/api/runs/{run_id}/report.md")
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/markdown", response.headers.get("Content-Type", ""))
            body = response.read().decode()
        self.assertIn("# FragileVision Claim Card", body)

    def test_the_markdown_report_route_404s_on_an_unknown_run(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base}/api/runs/999999/report.md", timeout=3)
        self.assertEqual(caught.exception.code, 404)


    def test_a_completed_run_samples_memory_once_and_the_dashboard_reports_it(self):
        """Only Ollama exposes /api/ps, and only for the exact model tag running."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class FakeOllama(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.dumps({"message": {"content": '{"answer":"yes"}'},
                                   "prompt_eval_count": 500, "eval_count": 8}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                body = json.dumps({"models": [{"name": "fake:test", "size": 5_000_000_000,
                                               "size_vram": 4_900_000_000}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *args):
                pass

        fake = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        self.addCleanup(fake.server_close)
        self.addCleanup(fake.shutdown)
        endpoint = f"http://127.0.0.1:{fake.server_port}"

        run_id, variant_id, image_ids = self.build_run(images=3)
        question_id = self.app.db.row("select question_id from variants where id=?", (variant_id,))["question_id"]
        self.app.db.execute("update providers set kind='ollama',endpoint=?,model='fake:test',is_demo=0 where id="
                            "(select provider_id from runs where id=?)", (endpoint, run_id))

        self.app._run_worker(run_id)

        detail = self.app.run_detail(run_id)
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["memory_bytes"], 5_000_000_000)
        self.assertEqual(detail["memory_vram_bytes"], 4_900_000_000)

        report = self.app.performance()
        provider_id = detail["provider_id"]
        model = next(item for item in report["models"] if item["provider_id"] == provider_id)
        self.assertEqual(model["responses_total"], 3)
        self.assertEqual(model["memory_display"], "4.7 GB")

        probe = self.app.probe_memory(provider_id)
        self.assertTrue(probe["available"])
        self.assertEqual(probe["bytes"], 5_000_000_000)

    def test_an_unreachable_provider_gets_no_memory_reading_and_an_honest_reason(self):
        run_id, _, _ = self.build_run(images=1)
        provider_id = self.app.db.row("select provider_id from runs where id=?", (run_id,))["provider_id"]
        self.app.db.execute("update providers set kind='ollama',endpoint='http://127.0.0.1:1',is_demo=0 where id=?",
                            (provider_id,))
        result = self.app.probe_memory(provider_id)
        self.assertFalse(result["available"])
        self.assertIn("raggiungibile", result["reason"])

    def test_an_active_runs_eta_comes_from_its_own_models_recent_calls(self):
        run_id, variant_id, image_ids = self.build_run(images=2)
        provider_id = self.app.db.row("select provider_id from runs where id=?", (run_id,))["provider_id"]
        for image_id in image_ids:
            self.app.db.execute("""insert into responses
                (run_id,image_id,variant_id,repetition,answer,raw,format_valid,parser,latency_ms,prompt_text,created_at)
                values(?,?,?,0,'yes','{}',1,'json',1000,'p',?)""", (run_id, image_id, variant_id, self.app.db.now()))
        second_run = self.app.db.execute("""insert into runs(project_id,provider_id,name,status,config_json,total,
            completed,created_at) values((select project_id from runs where id=?),?,?,'running',?,10,4,?)""",
            (run_id, provider_id, "Attiva", json.dumps({"question_ids": [1]}), self.app.db.now()))

        state = self.app.state()
        active = next(row for row in state["runs"] if row["id"] == second_run)
        self.assertAlmostEqual(active["eta_seconds"], 6.0)


    def test_rename_validates_length_and_is_visible_everywhere(self):
        run_id, _, _ = self.build_run(images=1)
        renamed = self.app.rename_run(run_id, "  Nome nuovo  ")
        self.assertEqual(renamed["name"], "Nome nuovo")
        with self.assertRaises(ValueError):
            self.app.rename_run(run_id, "   ")
        with self.assertRaises(ValueError):
            self.app.rename_run(run_id, "x" * 161)
        with self.assertRaises(ValueError):
            self.app.rename_run(999999, "Qualcosa")

    def test_duplicate_copies_the_configuration_and_starts_immediately(self):
        run_id, variant_id, image_ids = self.build_run(images=2)
        self.store_response(run_id, image_ids[0], variant_id)
        self.app.db.execute("update runs set status='completed' where id=?", (run_id,))
        original = self.app.run_detail(run_id)

        copy = self.app.duplicate_run(run_id)

        self.assertNotEqual(copy["id"], run_id)
        self.assertEqual(copy["config"], original["config"])
        self.assertEqual(copy["provider_id"], original["provider_id"])
        self.assertIn("copia", copy["name"])
        # It must not sit idle as a fake 'queued': resume_run() refuses to touch
        # a run already in that status, so an unstarted duplicate would be stuck.
        self.assertIn(copy["status"], {"queued", "running", "completed"})
        self.assertEqual(self.app.duplicate_run(run_id, name="Nome scelto")["name"], "Nome scelto")

    def test_duplicating_a_run_whose_project_is_trashed_is_refused(self):
        run_id, _, _ = self.build_run(images=1)
        project_id = self.app.run_detail(run_id)["project_id"]
        self.app.db.execute("update projects set deleted_at=? where id=?", (self.app.db.now(), project_id))
        with self.assertRaises(ValueError):
            self.app.duplicate_run(run_id)

    def test_archiving_is_refused_while_a_run_is_active_and_disappears_from_the_live_state(self):
        run_id, _, _ = self.build_run(images=1)
        self.app.db.execute("update runs set status='running' where id=?", (run_id,))
        with self.assertRaises(ValueError):
            self.app.archive_run(run_id)

        self.app.db.execute("update runs set status='completed' where id=?", (run_id,))
        self.app.archive_run(run_id)
        self.assertNotIn(run_id, [row["id"] for row in self.app.state()["runs"]])
        self.assertIn(run_id, [row["id"] for row in self.app.list_runs(archived=True)["runs"]])
        self.assertNotIn(run_id, [row["id"] for row in self.app.list_runs()["runs"]])

        with self.assertRaises(ValueError):
            self.app.unarchive_run(999999)
        self.app.unarchive_run(run_id)
        self.assertIn(run_id, [row["id"] for row in self.app.list_runs()["runs"]])

    def test_archiving_twice_does_not_move_the_timestamp(self):
        run_id, _, _ = self.build_run(images=1)
        self.app.db.execute("update runs set status='completed' where id=?", (run_id,))
        self.app.archive_run(run_id)
        first = self.app.db.row("select archived_at from runs where id=?", (run_id,))["archived_at"]
        self.app.archive_run(run_id)
        second = self.app.db.row("select archived_at from runs where id=?", (run_id,))["archived_at"]
        self.assertEqual(first, second)

    def test_list_runs_filters_by_project_status_provider_and_search(self):
        run_a, variant_id, _ = self.build_run(images=1)
        project_a = self.app.run_detail(run_a)["project_id"]
        self.app.rename_run(run_a, "Notte al Festino")
        self.app.db.execute("update runs set status='failed' where id=?", (run_a,))

        now = self.app.db.now()
        project_b = self.app.db.execute(
            "insert into projects(name,slug,description,created_at) values('Altro','altro','',?)", (now,))
        provider_id = self.app.run_detail(run_a)["provider_id"]
        run_b = self.app.db.execute("""insert into runs(project_id,provider_id,name,status,config_json,created_at)
            values(?,?,?,'completed',?,?)""", (project_b, provider_id, "Giorno feriale",
            json.dumps({"question_ids": [1]}), now))

        by_status = self.app.list_runs(status="failed")["runs"]
        self.assertEqual([row["id"] for row in by_status], [run_a])

        by_search = self.app.list_runs(search="festino")["runs"]
        self.assertEqual([row["id"] for row in by_search], [run_a])

        by_project = self.app.list_runs(project_id=project_b)["runs"]
        self.assertEqual({row["id"] for row in by_project}, {run_b})

    def test_csv_export_matches_the_same_filters_and_opens_as_utf8(self):
        run_id, _, _ = self.build_run(images=1)
        self.app.rename_run(run_id, "Esportami")
        csv_bytes = self.app.export_runs_csv(search="esportami")
        text = csv_bytes.decode("utf-8-sig")
        import codecs
        self.assertTrue(csv_bytes.startswith(codecs.BOM_UTF8))
        rows = text.splitlines()
        self.assertEqual(rows[0].split(",")[:2], ["id", "nome"])
        self.assertIn("Esportami", rows[1])

    def test_resuming_an_archived_run_is_refused_until_it_is_restored(self):
        run_id, variant_id, image_ids = self.build_run(images=2)
        self.store_response(run_id, image_ids[0], variant_id)
        self.app.db.execute("update runs set status='paused' where id=?", (run_id,))
        self.app.archive_run(run_id)
        with self.assertRaises(ValueError):
            self.app.resume_run(run_id)
        self.app.unarchive_run(run_id)
        result = self.app.resume_run(run_id)
        self.assertEqual(result["resumed"], 1)

    def test_free_memory_refuses_demo_and_openai_but_reaches_ollama(self):
        run_id, _, _ = self.build_run(images=1)
        provider_id = self.app.db.row("select provider_id from runs where id=?", (run_id,))["provider_id"]

        demo = self.app.free_memory(provider_id)
        self.assertFalse(demo["ok"])
        self.assertIn("simulatore", demo["reason"].lower())

        self.app.db.execute("update providers set is_demo=0,kind='ollama',endpoint='http://127.0.0.1:1' where id=?",
                            (provider_id,))
        unreachable = self.app.free_memory(provider_id)
        self.assertFalse(unreachable["ok"])
        self.assertIn("raggiungibile", unreachable["reason"])

        self.app.db.execute("update providers set kind='openai' where id=?", (provider_id,))
        openai = self.app.free_memory(provider_id)
        self.assertFalse(openai["ok"])
        self.assertIn("OpenAI", openai["reason"])

        with self.assertRaises(ValueError):
            self.app.free_memory(999999)

    def test_auto_configure_registers_only_vision_capable_ollama_models(self):
        with patch("fragilevision.server.discover_ollama_vision_models", return_value=["qwen3-vl:8b"]) as probe:
            registered = self.app.auto_configure_providers()
        self.assertEqual(registered, 1)
        probe.assert_called_once_with("http://127.0.0.1:11434", timeout=2)
        saved = self.app.db.row("select kind,model,endpoint,is_demo from providers")
        self.assertEqual((saved["kind"], saved["model"], saved["is_demo"]), ("ollama", "qwen3-vl:8b", 0))

    def test_auto_configure_never_touches_an_already_configured_install(self):
        self.app.db.execute("""insert into providers(name,kind,endpoint,model,is_demo,created_at)
            values('Manuale','ollama','http://127.0.0.1:11434','x',0,?)""", (self.app.db.now(),))
        with patch("fragilevision.server.discover_ollama_vision_models") as probe:
            registered = self.app.auto_configure_providers()
        self.assertEqual(registered, 0)
        probe.assert_not_called()

    def test_auto_configure_is_silent_when_nothing_is_reachable(self):
        with patch("fragilevision.server.discover_ollama_vision_models", side_effect=OSError("refused")):
            registered = self.app.auto_configure_providers()
        self.assertEqual(registered, 0)
        self.assertIsNone(self.app.db.row("select id from providers"))

    def test_a_provider_with_no_runs_can_be_deleted(self):
        now = self.app.db.now()
        provider_id = self.app.db.execute("""insert into providers
            (name,kind,endpoint,model,is_demo,created_at) values('Sganciabile','ollama',
            'http://127.0.0.1:11434','x',0,?)""", (now,))
        status, deleted = self.delete(f"/api/providers/{provider_id}", token=self.app.token)
        self.assertEqual(status, 200)
        self.assertEqual(deleted["deleted_id"], provider_id)
        self.assertIsNone(self.app.db.row("select id from providers where id=?", (provider_id,)))

    def test_a_provider_with_runs_is_refused_not_cascaded(self):
        """Deleting the provider would silently take every run's history with
        it if it were allowed to cascade; the runs must go first, explicitly."""
        run_id, _, _ = self.build_run(images=1)
        provider_id = self.app.db.row("select provider_id from runs where id=?", (run_id,))["provider_id"]
        with self.assertRaises(HTTPError) as caught:
            self.delete(f"/api/providers/{provider_id}", token=self.app.token)
        self.assertEqual(caught.exception.code, 409)
        self.assertIsNotNone(self.app.db.row("select id from providers where id=?", (provider_id,)))

    def test_deleting_an_unknown_provider_is_a_404(self):
        with self.assertRaises(HTTPError) as caught:
            self.delete("/api/providers/999999", token=self.app.token)
        self.assertEqual(caught.exception.code, 404)

    def test_the_rename_route_reaches_the_api(self):
        run_id, _, _ = self.build_run(images=1)
        status, body = self.post(f"/api/runs/{run_id}/rename", {"name": "Via HTTP"}, token=self.app.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "Via HTTP")


if __name__ == "__main__":
    unittest.main()
