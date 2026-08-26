"""Loopback-only web application and evaluation runner."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO, StringIO
import csv
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from typing import Any
from urllib.parse import parse_qs, urlparse
import zipfile

from . import __version__
from .agreement import calculate_agreement, consensus_summary, normalize_annotator, resolve_labels
from .core import (analyze_image_features, evaluation_fingerprint, feature_extractor_version,
                   import_directory, prepare_model_image, slugify)
from .dataset import audit_dataset, build_split
from .db import Database
from .diagnostics import calculate_failure_diagnostics
from .metrics import calculate_arena, calculate_metrics
from .performance import calculate_performance, estimate_eta_seconds, format_bytes
from .providers import (build_prompt, call_provider, discover_models, discover_ollama_vision_models,
                        generate_stress_variants, parse_verdict, sample_memory, unload_memory,
                        validate_private_endpoint)
from .report import build_eval_yaml, build_report, build_report_markdown


MAX_JSON_BODY = 2 * 1024 * 1024
MAX_SERVED_IMAGE = 80 * 1024 * 1024
PICKER_LOCK = threading.Lock()


def choose_directory(purpose: str) -> str | None:
    """Open the operating system's native directory picker without uploading files."""
    prompt = {"dataset": "Scegli la cartella del dataset",
              "model": "Scegli la cartella del modello"}.get(purpose)
    if not prompt:
        raise ValueError("Tipo di selezione non valido")
    if sys.platform == "darwin":
        script = f'POSIX path of (choose folder with prompt "{prompt}")'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                                timeout=300, check=False)
        if result.returncode:
            if "-128" in result.stderr or "canceled" in result.stderr.lower():
                return None
            raise RuntimeError("Il selettore cartelle di macOS non è disponibile")
        selected = result.stdout.strip()
    else:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw()
            try:
                selected = filedialog.askdirectory(title=prompt, mustexist=True)
            finally:
                root.destroy()
        except Exception as error:
            raise RuntimeError("Il selettore cartelle di sistema non è disponibile") from error
    if not selected:
        return None
    directory = Path(selected).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("La cartella selezionata non è accessibile")
    return str(directory)


def run_config_from_body(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize the reproducible settings shared by single and Arena runs."""
    question_ids = sorted({int(value) for value in body.get("question_ids") or []})
    if not question_ids:
        raise ValueError("Seleziona almeno una domanda")
    split = str(body.get("split", "") or "")
    if split not in {"", "train", "test"}:
        raise ValueError("La suddivisione può essere soltanto train, test o tutto il dataset")
    return {
        "question_ids": question_ids,
        "variant_ids": sorted({int(value) for value in body.get("variant_ids") or []}),
        "split": split,
        "repetitions": max(1, min(20, int(body.get("repetitions", 1)))),
        "temperature": max(0.0, min(2.0, float(body.get("temperature", 0)))),
        "seed": int(body.get("seed", 0)),
        "max_tokens": max(8, min(1024, int(body.get("max_tokens", 96)))),
        "timeout": max(10, min(900, int(body.get("timeout", 180)))),
    }


def resolve_variant_ids(db: Database, project_id: int, config: dict[str, Any]) -> list[int]:
    """Freeze which variants a run actually used.

    Storing an empty list to mean "all of them" makes two runs look compatible
    across a change to the question set: the Arena would then pair runs that
    never answered the same prompts.
    """
    placeholders = ",".join("?" for _ in config["question_ids"])
    available = db.rows(
        f"""select v.id from variants v join questions q on q.id=v.question_id
        where q.project_id=? and q.id in ({placeholders}) order by v.id""",
        (project_id, *config["question_ids"]))
    ids = [int(row["id"]) for row in available]
    requested = set(config.get("variant_ids") or [])
    selected = [value for value in ids if value in requested] if requested else ids
    if not selected:
        raise ValueError("Le domande selezionate non hanno varianti da eseguire")
    return selected


RUN_LIST_LIMIT = 300
RUN_STATUSES = {"queued", "running", "paused", "completed", "failed", "cancelled"}


def run_filters_from_query(query: dict[str, list[str]]) -> dict[str, Any]:
    """Parse the Runs ledger's filter controls from a query string.

    Malformed values are dropped rather than rejected: an invalid id or status
    typed into the URL should show an unfiltered list, not a 400.
    """
    def one(key: str) -> str:
        return (query.get(key) or [""])[0].strip()
    project_raw, status_raw, provider_raw = one("project_id"), one("status"), one("provider_id")
    return {
        "project_id": int(project_raw) if project_raw.isdigit() else None,
        "status": status_raw if status_raw in RUN_STATUSES else None,
        "provider_id": int(provider_raw) if provider_raw.isdigit() else None,
        "archived": one("archived") == "1",
        "search": one("q")[:200],
    }


class App:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "fragilevision.sqlite3")
        self.token = secrets.token_urlsafe(32)
        self._threads: dict[int, threading.Thread] = {}
        # Interrupted, not failed: every stored response is a checkpoint, so the
        # run can pick up where it stopped instead of paying for the work twice.
        self.db.execute("update runs set status='paused',error=? where status in ('queued','running')",
                        ("Interrotta dal riavvio dell’applicazione. Le risposte già ottenute sono conservate: usa Riprendi.",))

    def auto_configure_providers(self) -> int:
        """First-run convenience: register whatever Ollama already has pulled.

        Restricted to Ollama, whose default port (11434) is a genuine,
        near-universal convention — unlike an MLX or OpenAI-compatible
        server, which has no standard port at all. Guessing at other local
        ports on a fresh install would risk silently registering an
        unrelated service that happens to be listening there for some other
        reason. Runs only while no provider has been configured yet, so it
        can never override or duplicate something the user set up already.
        """
        if self.db.row("select id from providers limit 1"):
            return 0
        try:
            models = discover_ollama_vision_models("http://127.0.0.1:11434", timeout=2)
        except Exception:
            return 0
        now = self.db.now()
        registered = 0
        for model in models[:20]:
            try:
                self.db.execute("""insert into providers(name,kind,endpoint,model,is_demo,created_at)
                    values(?,?,?,?,0,?)""", (f"Ollama · {model}"[:100], "ollama",
                    "http://127.0.0.1:11434", model, now))
                registered += 1
            except sqlite3.IntegrityError:
                continue
        return registered

    def run_detail(self, run_id: int) -> dict[str, Any] | None:
        run = self.db.row("""select r.*,p.name provider_name,p.kind provider_kind,p.endpoint provider_endpoint,
            p.model provider_model,p.is_demo provider_is_demo,pr.name project_name from runs r
            join providers p on p.id=r.provider_id join projects pr on pr.id=r.project_id where r.id=?""", (run_id,))
        if run:
            run["config"] = json.loads(run.pop("config_json"))
        return run

    def state(self) -> dict[str, Any]:
        """The dashboard's poll payload, with a live ETA on every active run.

        The estimate comes from that model's own recent settled calls, bounded
        to the last 200 so a busy provider does not turn every one-second poll
        into a full table scan.
        """
        state = self.db.state()
        latencies_by_provider: dict[int, list[float]] = {}
        for run in state["runs"]:
            if run["status"] not in {"queued", "running"}:
                continue
            provider_id = int(run["provider_id"])
            if provider_id not in latencies_by_provider:
                latencies_by_provider[provider_id] = [float(row["latency_ms"]) for row in self.db.rows(
                    """select r.latency_ms from responses r join runs ru on ru.id=r.run_id
                    where ru.provider_id=? and r.latency_ms is not null and r.parser!='error'
                    order by r.id desc limit 200""", (provider_id,))]
            pending = max(0, int(run["total"] or 0) - int(run["completed"] or 0))
            run["eta_seconds"] = estimate_eta_seconds(latencies_by_provider[provider_id], pending)
        return state

    def performance(self) -> dict[str, Any]:
        """Cost, reliability and memory per configured model, across every project."""
        providers = self.db.rows("select * from providers order by name")
        responses = self.db.rows("""select r.latency_ms,r.prompt_tokens,r.completion_tokens,r.parser,
            r.format_valid,r.created_at,ru.provider_id from responses r join runs ru on ru.id=r.run_id""")
        runs = self.db.rows("""select id,provider_id,status,completed,total,runtime_seconds,created_at,
            memory_bytes,memory_vram_bytes,memory_sampled_at from runs""")
        return calculate_performance(providers, responses, runs)

    def probe_memory(self, provider_id: int) -> dict[str, Any]:
        """An on-demand /api/ps read, for a model nobody has run yet this session."""
        provider = self.db.row("select * from providers where id=?", (provider_id,))
        if not provider:
            raise ValueError("Provider inesistente")
        reading = sample_memory(provider)
        if not reading:
            reason = ("Il simulatore sintetico non alloca memoria reale." if provider["is_demo"]
                      else "Memoria non osservabile per questo protocollo: non esiste un’API standard per leggerla."
                      if provider["kind"] != "ollama" else
                      "Il modello non risulta caricato su Ollama in questo momento, o il server non è raggiungibile.")
            return {"available": False, "reason": reason}
        latest_run = self.db.row("select id from runs where provider_id=? order by created_at desc limit 1",
                                 (provider_id,))
        if latest_run:
            self.db.execute("update runs set memory_bytes=?,memory_vram_bytes=?,memory_sampled_at=? where id=?",
                            (reading["bytes"], reading.get("vram_bytes"), reading["sampled_at"], latest_run["id"]))
        return {"available": True, "bytes": reading["bytes"], "vram_bytes": reading.get("vram_bytes"),
                "display": format_bytes(reading["bytes"]), "vram_display": format_bytes(reading.get("vram_bytes")),
                "sampled_at": reading["sampled_at"]}

    def free_memory(self, provider_id: int) -> dict[str, Any]:
        """Unload this model from Ollama now, instead of waiting out keep_alive."""
        provider = self.db.row("select * from providers where id=?", (provider_id,))
        if not provider:
            raise ValueError("Provider inesistente")
        if bool(provider["is_demo"]):
            return {"ok": False, "reason": "Il simulatore sintetico non alloca memoria reale."}
        if provider["kind"] != "ollama":
            return {"ok": False, "reason": "Lo scarico immediato esiste solo per Ollama: gli endpoint "
                                          "OpenAI-compatibili non hanno un’API equivalente."}
        freed = unload_memory(provider)
        if freed:
            # The cached reading describes memory that no longer holds the model.
            self.db.execute("update runs set memory_bytes=null,memory_vram_bytes=null,memory_sampled_at=null "
                            "where provider_id=? and memory_bytes is not null", (provider_id,))
        return {"ok": freed, "reason": None if freed else
                "Impossibile contattare Ollama: verifica che il server locale sia raggiungibile."}

    # ------------------------------------------------------------------
    # Run management: rename, duplicate, archive, filter, export
    # ------------------------------------------------------------------

    def list_runs(self, *, project_id: int | None = None, status: str | None = None,
                  provider_id: int | None = None, archived: bool = False,
                  search: str = "") -> dict[str, Any]:
        clauses = ["pr.deleted_at is null", "r.archived_at is not null" if archived else "r.archived_at is null"]
        parameters: list[Any] = []
        if project_id:
            clauses.append("r.project_id=?"); parameters.append(project_id)
        if status:
            clauses.append("r.status=?"); parameters.append(status)
        if provider_id:
            clauses.append("r.provider_id=?"); parameters.append(provider_id)
        if search.strip():
            clauses.append("lower(r.name) like ?"); parameters.append(f"%{search.strip().lower()}%")
        rows = self.db.rows(f"""select r.*, p.name provider_name, p.model provider_model,
            p.is_demo provider_is_demo, pr.name project_name
            from runs r join providers p on p.id=r.provider_id join projects pr on pr.id=r.project_id
            where {" and ".join(clauses)} order by r.created_at desc limit {RUN_LIST_LIMIT}""", tuple(parameters))
        for row in rows:
            row["config"] = json.loads(row.pop("config_json"))
            row["archived"] = row["archived_at"] is not None
        return {"runs": rows, "limit": RUN_LIST_LIMIT, "truncated": len(rows) == RUN_LIST_LIMIT}

    def export_runs_csv(self, **filters: Any) -> bytes:
        rows = self.list_runs(**filters)["runs"]

        def stamp(value: Any) -> str:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S") if value else ""

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "nome", "progetto", "provider", "modello", "demo", "stato", "archiviata",
                         "completate", "totale", "creata", "avviata", "conclusa", "durata_s", "errore"])
        for row in rows:
            writer.writerow([row["id"], row["name"], row["project_name"], row["provider_name"],
                             row["provider_model"], "si" if row["provider_is_demo"] else "no", row["status"],
                             "si" if row["archived"] else "no", row["completed"], row["total"],
                             stamp(row["created_at"]), stamp(row["started_at"]), stamp(row["finished_at"]),
                             round(float(row["runtime_seconds"] or 0), 1), row["error"] or ""])
        # A BOM is what makes Excel on macOS open a UTF-8 CSV without mangling
        # accented Italian labels; every other reader ignores it.
        return buffer.getvalue().encode("utf-8-sig")

    def rename_run(self, run_id: int, name: str) -> dict[str, Any]:
        cleaned = name.strip()
        if not (1 <= len(cleaned) <= 160):
            raise ValueError("Il nome deve contenere da 1 a 160 caratteri")
        if not self.db.row("select id from runs where id=?", (run_id,)):
            raise ValueError("Esecuzione inesistente")
        self.db.execute("update runs set name=? where id=?", (cleaned, run_id))
        return self.run_detail(run_id)

    def duplicate_run(self, run_id: int, name: str = "") -> dict[str, Any]:
        """Rerun a past experiment with its exact configuration.

        The copy is started immediately rather than left idle: an idle 'queued'
        row would be indistinguishable from one already being worked by a
        thread, and resume_run() refuses to touch a run in that state.
        """
        original = self.run_detail(run_id)
        if not original:
            raise ValueError("Esecuzione inesistente")
        if not self.db.row("select id from projects where id=? and deleted_at is null",
                           (original["project_id"],)):
            raise ValueError("Il progetto di questa esecuzione è nel Cestino: ripristinalo prima di duplicare")
        new_name = (name.strip() or f"{original['name']} (copia)")[:160]
        new_id = self.db.execute("""insert into runs(project_id,provider_id,name,status,config_json,created_at)
            values(?,?,?,'queued',?,?)""", (original["project_id"], original["provider_id"], new_name,
            json.dumps(original["config"], separators=(",", ":")), self.db.now()))
        self.start_run(new_id)
        return self.run_detail(new_id)

    def archive_run(self, run_id: int) -> dict[str, Any]:
        run = self.db.row("select id,status,archived_at from runs where id=?", (run_id,))
        if not run:
            raise ValueError("Esecuzione inesistente")
        if run["status"] in {"queued", "running"}:
            raise ValueError("Ferma l’esecuzione prima di archiviarla")
        if run["archived_at"] is None:
            self.db.execute("update runs set archived_at=? where id=?", (self.db.now(), run_id))
        return {"ok": True, "archived_id": run_id}

    def unarchive_run(self, run_id: int) -> dict[str, Any]:
        if not self.db.row("select id from runs where id=? and archived_at is not null", (run_id,)):
            raise ValueError("Esecuzione non archiviata")
        self.db.execute("update runs set archived_at=null where id=?", (run_id,))
        return {"ok": True, "restored_id": run_id}

    def response_rows(self, run_id: int) -> list[dict[str, Any]]:
        return self.db.rows("""select r.*,v.name variant_name,v.language,v.mutation_type,v.canonical,
            q.id question_id,q.key question_key,a.value ground_truth,
            a.agreement ground_truth_agreement,a.label_count ground_truth_labels,i.filename,i.source_group
            from responses r join variants v on v.id=r.variant_id
            join questions q on q.id=v.question_id join images i on i.id=r.image_id
            left join annotations a on a.image_id=r.image_id and a.question_id=q.id
            where r.run_id=? order by q.id,v.id,r.image_id,r.repetition""", (run_id,))

    def metrics(self, run_id: int) -> dict[str, Any]:
        if not self.run_detail(run_id):
            raise ValueError("Esecuzione inesistente")
        return calculate_metrics(self.response_rows(run_id))

    def arena(self, run_ids: list[int]) -> dict[str, Any]:
        entries = []
        for run_id in run_ids:
            run = self.run_detail(run_id)
            if not run:
                raise ValueError(f"Esecuzione {run_id} inesistente")
            entries.append({"run": run, "rows": self.response_rows(run_id)})
        return calculate_arena(entries)

    def image_features(self, image_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Cached visual signals and perceptual hashes for a set of images."""
        if not image_ids:
            return {}
        # Cache reads, extraction and writes in one request must always refer to
        # the same decoder/version pair.
        extractor_version = feature_extractor_version()
        images: list[dict[str, Any]] = []
        for start in range(0, len(image_ids), 500):
            batch = image_ids[start:start + 500]
            images += self.db.rows(
                f"select id,stored_path,width,height from images where id in ({','.join('?' for _ in batch)})",
                tuple(batch))
        features: dict[int, dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        for image in images:
            image_id = int(image["id"])
            cached = self.db.row("select * from image_features where image_id=? and extractor_version=?",
                                 (image_id, extractor_version))
            if cached and cached["brightness"] is not None:
                features[image_id] = {**cached, "width": image["width"], "height": image["height"]}
            else:
                pending.append(image)
        if pending:
            # Each extraction spends its time inside the sips subprocess, so threads
            # genuinely overlap: ~100 ms per image serially is half a minute on a
            # 300-image dataset, and the whole request used to block for it.
            with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
                measured = list(pool.map(lambda image: analyze_image_features(Path(image["stored_path"])), pending))
            for image, values in zip(pending, measured):
                image_id = int(image["id"])
                # A failed extraction is never cached. A transient sips timeout must
                # not leave this image without visual signals for every future run.
                if values["brightness"] is not None:
                    self.db.execute("""insert or replace into image_features
                        (image_id,extractor_version,brightness,contrast,edge_density,saturation,phash,analyzed_at)
                        values(?,?,?,?,?,?,?,?)""", (image_id, extractor_version, values["brightness"],
                        values["contrast"], values["edge_density"], values["saturation"],
                        values["phash"], self.db.now()))
                features[image_id] = {**values, "width": image["width"], "height": image["height"]}
        return features

    def diagnostics(self, run_id: int) -> dict[str, Any]:
        if not self.run_detail(run_id):
            raise ValueError("Esecuzione inesistente")
        rows = self.response_rows(run_id)
        image_ids = sorted({int(row["image_id"]) for row in rows})
        if not image_ids:
            return calculate_failure_diagnostics([], {})
        return calculate_failure_diagnostics(rows, self.image_features(image_ids))

    # ------------------------------------------------------------------
    # Human ground truth
    # ------------------------------------------------------------------

    def _case_project(self, image_id: int, question_id: int) -> int:
        row = self.db.row("""select i.project_id from images i join questions q on q.project_id=i.project_id
            where i.id=? and q.id=? and i.deleted_at is null""", (image_id, question_id))
        if not row:
            raise ValueError("Immagine e domanda non appartengono allo stesso progetto")
        return int(row["project_id"])

    def _resolve_case(self, connection: sqlite3.Connection, image_id: int, question_id: int) -> dict[str, Any] | None:
        """Rewrite the single ground-truth row from every judgement on this case.

        The consensus is derived, never edited by hand: the labels are the record
        and this row is a projection of them, so a reviewer changing their mind
        can never leave a stale verdict behind in the evaluation.
        """
        labels = [dict(row) for row in connection.execute(
            "select annotator,value,note,is_adjudication,updated_at from annotation_labels "
            "where image_id=? and question_id=?", (image_id, question_id))]
        resolved = resolve_labels(labels)
        if not resolved:
            connection.execute("delete from annotations where image_id=? and question_id=?",
                               (image_id, question_id))
            return None
        connection.execute("""insert into annotations
            (image_id,question_id,value,note,annotator,updated_at,agreement,label_count,distinct_values,adjudicated_by)
            values(?,?,?,?,?,?,?,?,?,?) on conflict(image_id,question_id) do update set
            value=excluded.value,note=excluded.note,annotator=excluded.annotator,updated_at=excluded.updated_at,
            agreement=excluded.agreement,label_count=excluded.label_count,
            distinct_values=excluded.distinct_values,adjudicated_by=excluded.adjudicated_by""",
            (image_id, question_id, resolved["value"], resolved["note"][:2000], resolved["annotator"][:100],
             self.db.now(), resolved["agreement"], resolved["label_count"], resolved["distinct_values"],
             resolved["adjudicated_by"]))
        return resolved

    def record_label(self, *, image_id: int, question_id: int, annotator: str, value: str,
                     note: str = "", is_adjudication: bool = False) -> dict[str, Any]:
        if value not in {"yes", "no", "uncertain", "exclude"}:
            raise ValueError("Annotazione non valida")
        project_id = self._case_project(image_id, question_id)
        reviewer = normalize_annotator(annotator)
        with self.db.connect() as connection:
            if is_adjudication:
                # Adjudicating a case nobody disputes would let one reviewer
                # overrule a panel that never disagreed in the first place.
                state = connection.execute("select agreement from annotations where image_id=? and question_id=?",
                                           (image_id, question_id)).fetchone()
                if not state or state["agreement"] not in {"conflict", "majority", "adjudicated"}:
                    raise ValueError("Si arbitra soltanto un caso conteso")
            connection.execute("""insert into annotation_labels
                (image_id,question_id,annotator,value,note,is_adjudication,updated_at)
                values(?,?,?,?,?,?,?) on conflict(image_id,question_id,annotator,is_adjudication)
                do update set value=excluded.value,note=excluded.note,updated_at=excluded.updated_at""",
                (image_id, question_id, reviewer, value, str(note)[:2000], int(is_adjudication), self.db.now()))
            resolved = self._resolve_case(connection, image_id, question_id)
        return {"ok": True, "project_id": project_id, "annotator": reviewer,
                "consensus": resolved, "fingerprint": evaluation_fingerprint(self.db, project_id)}

    def remove_label(self, *, image_id: int, question_id: int, annotator: str,
                     is_adjudication: bool = False) -> dict[str, Any]:
        project_id = self._case_project(image_id, question_id)
        reviewer = normalize_annotator(annotator)
        with self.db.connect() as connection:
            cursor = connection.execute("""delete from annotation_labels where image_id=? and question_id=?
                and annotator=? and is_adjudication=?""", (image_id, question_id, reviewer, int(is_adjudication)))
            if not cursor.rowcount:
                raise ValueError("Nessun giudizio da ritirare per questo revisore")
            resolved = self._resolve_case(connection, image_id, question_id)
        return {"ok": True, "annotator": reviewer, "consensus": resolved,
                "fingerprint": evaluation_fingerprint(self.db, project_id)}

    def annotation_board(self, project_id: int, question_id: int, annotator: str = "") -> dict[str, Any]:
        """Every image for one question, with the consensus and each reviewer's own call."""
        if not self.db.row("select id from questions where id=? and project_id=?", (question_id, project_id)):
            raise ValueError("Domanda inesistente")
        reviewer = normalize_annotator(annotator) if annotator.strip() else ""
        images = self.db.rows("""select i.id image_id,i.filename,i.source_group,i.split,
            a.value,a.note,a.updated_at,a.agreement,a.label_count,a.distinct_values,a.adjudicated_by
            from images i left join annotations a on a.image_id=i.id and a.question_id=?
            where i.project_id=? and i.deleted_at is null order by i.id""", (question_id, project_id))
        by_case: dict[int, list[dict[str, Any]]] = {}
        for label in self.db.labels(project_id, question_id):
            by_case.setdefault(int(label["image_id"]), []).append(label)
        for image in images:
            labels = by_case.get(int(image["image_id"]), [])
            image["labels"] = [{"annotator": row["annotator"], "value": row["value"], "note": row["note"],
                                "is_adjudication": bool(row["is_adjudication"]), "updated_at": row["updated_at"]}
                               for row in labels]
            mine = next((row for row in image["labels"]
                         if not row["is_adjudication"] and row["annotator"].lower() == reviewer.lower()), None)
            image["mine"] = mine
            image["others"] = sum(1 for row in image["labels"] if not row["is_adjudication"] and row is not mine)
        return {
            "question_id": question_id, "annotator": reviewer,
            "annotations": images,
            "annotators": self.db.annotators(project_id),
            "consensus": consensus_summary(str(image["agreement"] or "single")
                                           for image in images if image["value"]),
            "mine_done": sum(1 for image in images if image["mine"]),
        }

    def agreement(self, project_id: int) -> dict[str, Any]:
        if not self.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
            raise ValueError("Progetto inesistente")
        questions = self.db.rows("select id,key,label from questions where project_id=? order by id", (project_id,))
        states = [str(row["agreement"] or "single") for row in self.db.rows(
            """select a.agreement from annotations a join images i on i.id=a.image_id
            where i.project_id=? and i.deleted_at is null""", (project_id,))]
        report = calculate_agreement(self.db.labels(project_id), questions, states)
        report["fingerprint"] = evaluation_fingerprint(self.db, project_id)
        return report

    def contested_cases(self, project_id: int, question_id: int | None = None,
                        limit: int = 200) -> dict[str, Any]:
        """Cases where the panel did not speak with one voice, worst first."""
        clause = " and a.question_id=?" if question_id else ""
        parameters: tuple[Any, ...] = (project_id, question_id) if question_id else (project_id,)
        rows = self.db.rows(f"""select a.image_id,a.question_id,a.value,a.agreement,a.label_count,
            a.distinct_values,a.adjudicated_by,i.filename,q.label question_label,q.key question_key
            from annotations a join images i on i.id=a.image_id join questions q on q.id=a.question_id
            where i.project_id=? and i.deleted_at is null and a.agreement in ('conflict','majority','adjudicated')
            {clause} order by case a.agreement when 'conflict' then 0 when 'majority' then 1 else 2 end,
            a.distinct_values desc,a.image_id""", parameters)
        by_case: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for label in self.db.labels(project_id, question_id):
            by_case.setdefault((int(label["image_id"]), int(label["question_id"])), []).append(label)
        cases = []
        for row in rows[:limit]:
            labels = by_case.get((int(row["image_id"]), int(row["question_id"])), [])
            cases.append({**row, "labels": [
                {"annotator": item["annotator"], "value": item["value"], "note": item["note"],
                 "is_adjudication": bool(item["is_adjudication"]), "updated_at": item["updated_at"]}
                for item in labels]})
        return {"cases": cases, "total": len(rows),
                "unresolved": sum(1 for row in rows if row["agreement"] == "conflict")}

    def project_images(self, project_id: int) -> list[dict[str, Any]]:
        return self.db.rows("""select id,sha256,filename,stored_path,source_group,mime,width,height,split
            from images where project_id=? and deleted_at is null order by id""", (project_id,))

    def audit(self, project_id: int, *, verify_checksums: bool = False) -> dict[str, Any]:
        if not self.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
            raise ValueError("Progetto inesistente")
        images = self.project_images(project_id)
        questions = self.db.rows("select id,key,label from questions where project_id=? order by id", (project_id,))
        annotations = self.db.rows("""select a.question_id,a.value from annotations a
            join images i on i.id=a.image_id where i.project_id=? and i.deleted_at is null""", (project_id,))
        features = self.image_features([int(image["id"]) for image in images])
        hashes = {image_id: str(values.get("phash") or "") for image_id, values in features.items()}
        report = audit_dataset(images, questions, annotations, hashes, verify_checksums=verify_checksums)
        report["fingerprint"] = evaluation_fingerprint(self.db, project_id)
        return report

    def assign_split(self, project_id: int, *, seed: int, test_ratio: float) -> dict[str, Any]:
        images = self.project_images(project_id)
        if not images:
            raise ValueError("Importa almeno un’immagine")
        if self.db.row("select id from runs where project_id=? and status in ('queued','running') limit 1", (project_id,)):
            raise ValueError("Ferma prima le esecuzioni attive del progetto")
        features = self.image_features([int(image["id"]) for image in images])
        hashes = {image_id: str(values.get("phash") or "") for image_id, values in features.items()}
        from .dataset import find_near_duplicates
        duplicates = find_near_duplicates(images, hashes)
        result = build_split(images, duplicates["pairs"], seed=seed, test_ratio=test_ratio)
        assignment = result.pop("assignment")
        with self.db.connect() as connection:
            for image_id, side in assignment.items():
                connection.execute("update images set split=? where id=?", (side, image_id))
        result["fingerprint"] = evaluation_fingerprint(self.db, project_id)
        result["near_duplicate_pairs"] = duplicates["pair_count"]
        return result

    def clear_split(self, project_id: int) -> dict[str, Any]:
        if not self.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
            raise ValueError("Progetto inesistente")
        self.db.execute("update images set split='' where project_id=?", (project_id,))
        return {"cleared": True, "fingerprint": evaluation_fingerprint(self.db, project_id)}

    def pending_units(self, run: dict[str, Any]) -> tuple[list[tuple[int, dict[str, Any], dict[str, Any]]], int]:
        """The grid still to execute, in stable order, with the full grid size.

        A response that reached the model is a checkpoint and is never repeated,
        even when it was unreadable — an unparseable answer is a result, not a
        gap. Rows recording a failed call are retried, which is what lets a run
        survive the local model server going down halfway through.
        """
        config = run["config"]
        question_ids = [int(value) for value in config.get("question_ids") or []]
        if not question_ids:
            raise ValueError("Nessuna domanda selezionata")
        repetitions = max(1, min(20, int(config.get("repetitions", 1))))
        placeholders = ",".join("?" for _ in question_ids)
        split = str(config.get("split") or "")
        if split:
            images = self.db.rows("""select * from images where project_id=? and deleted_at is null
                and split=? order by id""", (run["project_id"], split))
            if not images:
                raise ValueError(f"Nessuna immagine assegnata a “{split}”: assegna prima la suddivisione")
        else:
            images = self.db.rows("select * from images where project_id=? and deleted_at is null order by id",
                                  (run["project_id"],))
        variants = self.db.rows(
            f"""select v.*,q.key question_key from variants v join questions q on q.id=v.question_id
            where q.project_id=? and q.id in ({placeholders}) order by q.id,v.id""",
            (run["project_id"], *question_ids))
        selected = {int(value) for value in config.get("variant_ids") or []}
        if selected:
            variants = [variant for variant in variants if int(variant["id"]) in selected]
        grid = [(repetition, variant, image)
                for repetition in range(repetitions) for variant in variants for image in images]
        settled = {(int(row["image_id"]), int(row["variant_id"]), int(row["repetition"]))
                   for row in self.db.rows(
                       "select image_id,variant_id,repetition from responses where run_id=? and error is null",
                       (int(run["id"]),))}
        pending = [unit for unit in grid
                   if (int(unit[2]["id"]), int(unit[1]["id"]), unit[0]) not in settled]
        return pending, len(grid)

    def resume_run(self, run_id: int) -> dict[str, Any]:
        run = self.run_detail(run_id)
        if not run:
            raise ValueError("Esecuzione inesistente")
        thread = self._threads.get(run_id)
        if run["status"] in {"queued", "running"} or (thread and thread.is_alive()):
            raise ValueError("L’esecuzione è già in corso")
        if run.get("archived_at") is not None:
            # Resuming work the ledger can't see would leave it running invisibly:
            # unarchive first, so it comes back into the live list it belongs in.
            raise ValueError("Ripristina prima l’esecuzione dall’archivio")
        pending, total = self.pending_units(run)
        if not pending:
            self.db.execute("""update runs set status='completed',completed=?,total=?,finished_at=?,error=null
                where id=?""", (total, total, self.db.now(), run_id))
            return {"resumed": 0, "total": total, "status": "completed"}
        self.db.execute("update runs set status='queued',error=null,finished_at=null where id=?", (run_id,))
        self.start_run(run_id)
        return {"resumed": len(pending), "total": total, "status": "queued"}

    def start_run(self, run_id: int) -> None:
        thread = threading.Thread(target=self._run_worker, args=(run_id,), name=f"fv-run-{run_id}", daemon=True)
        self._threads[run_id] = thread
        thread.start()

    def start_batch(self, run_ids: list[int]) -> None:
        """Run an Arena batch sequentially to avoid GPU/VRAM contention."""
        def worker() -> None:
            for position, run_id in enumerate(run_ids):
                current = self.db.row("select status from runs where id=?", (run_id,))
                if not current or current["status"] != "queued":
                    continue
                self._run_worker(run_id)
                after = self.db.row("select status from runs where id=?", (run_id,))
                if after and after["status"] in {"paused", "cancelled"}:
                    # Stopping one model stops the batch. The others stay pausable
                    # rather than queued forever with nothing left to run them.
                    for later_id in run_ids[position + 1:]:
                        self.db.execute("update runs set status='paused',error=? where id=? and status='queued'",
                                        ("Arena interrotta prima di questa esecuzione: usa Riprendi.", later_id))
                    return

        thread = threading.Thread(target=worker, name=f"fv-arena-{'-'.join(map(str, run_ids))}", daemon=True)
        for run_id in run_ids:
            self._threads[run_id] = thread
        thread.start()

    def _run_worker(self, run_id: int) -> None:
        started = time.monotonic()
        last_error: str | None = None
        try:
            run = self.run_detail(run_id)
            if not run:
                return
            config = run["config"]
            pending, total = self.pending_units(run)
            completed = total - len(pending)
            self.db.execute("""update runs set status='running',started_at=coalesce(started_at,?),
                total=?,completed=?,error=null,finished_at=null where id=?""",
                (self.db.now(), total, completed, run_id))
            provider = self.db.row("select * from providers where id=?", (run["provider_id"],))
            if not provider:
                raise ValueError("Provider inesistente")
            memory_sampled = False
            for repetition, variant, image in pending:
                current = self.db.row("select status from runs where id=?", (run_id,))
                if not current or current["status"] in {"cancelled", "paused"}:
                    return
                prompt = build_prompt(variant["text"])
                try:
                    prepared = prepare_model_image(image, self.data_dir / "cache" / "model-inputs")
                    image_bytes = prepared["bytes"]
                    if not image_bytes or len(image_bytes) > MAX_SERVED_IMAGE:
                        raise ValueError("Immagine non disponibile o troppo grande")
                    result = call_provider(
                        provider, image_bytes, prepared["mime"], prompt,
                        temperature=float(config.get("temperature", 0)),
                        seed=int(config.get("seed", 0)) + repetition,
                        max_tokens=int(config.get("max_tokens", 96)),
                        timeout=int(config.get("timeout", 180)),
                    )
                    parsed = parse_verdict(result.raw)
                    values = (run_id, image["id"], variant["id"], repetition, parsed.answer,
                              result.raw[:50_000], int(parsed.format_valid), parsed.parser,
                              result.latency_ms, result.prompt_tokens, result.completion_tokens,
                              prompt, None, prepared["sha256"], prepared["mime"],
                              prepared["width"], prepared["height"], prepared["preprocess"], self.db.now())
                    settled = True
                except Exception as error:  # one bad image never destroys a long run
                    last_error = f"{type(error).__name__}: {error}"[:500]
                    values = (run_id, image["id"], variant["id"], repetition, "invalid", "", 0,
                              "error", None, None, None, prompt, str(error)[:1000],
                              None, None, None, None, "failed", self.db.now())
                    settled = False
                self.db.execute("""insert or replace into responses
                    (run_id,image_id,variant_id,repetition,answer,raw,format_valid,parser,latency_ms,
                     prompt_tokens,completion_tokens,prompt_text,error,input_sha256,input_mime,
                     input_width,input_height,input_preprocess,created_at)
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                if settled:
                    # Only a stored verdict advances progress, so a run against an
                    # unreachable model reports the truth instead of a full bar.
                    completed += 1
                    self.db.execute("update runs set completed=? where id=?", (completed, run_id))
                    if not memory_sampled:
                        # Sampled once per worker call, right after the model has
                        # definitely finished loading, not before the first call
                        # when it may still be cold.
                        memory_sampled = True
                        reading = sample_memory(provider)
                        if reading:
                            self.db.execute("""update runs set memory_bytes=?,memory_vram_bytes=?,
                                memory_sampled_at=? where id=? and memory_bytes is null""",
                                (reading["bytes"], reading.get("vram_bytes"), reading["sampled_at"], run_id))
            remaining = total - completed
            if remaining and not completed:
                raise RuntimeError(last_error or "Nessuna risposta ottenuta dal modello")
            note = (f"{remaining} risposte non ottenute: usa Riprendi quando il modello locale è di nuovo "
                    f"raggiungibile. Ultimo errore — {last_error}") if remaining else None
            self.db.execute("update runs set status='completed',completed=?,finished_at=?,error=? where id=?",
                            (completed, self.db.now(), note, run_id))
        except Exception as error:
            self.db.execute("update runs set status='failed',error=?,finished_at=? where id=?",
                            (f"{type(error).__name__}: {error}"[:2000], self.db.now(), run_id))
        finally:
            self.db.execute("update runs set runtime_seconds=runtime_seconds+? where id=?",
                            (max(0.0, time.monotonic() - started), run_id))
            self._threads.pop(run_id, None)

    def bundle(self, run_id: int) -> bytes:
        run = self.run_detail(run_id)
        if not run:
            raise ValueError("Esecuzione inesistente")
        metrics = self.metrics(run_id)
        fingerprint = evaluation_fingerprint(self.db, int(run["project_id"]))
        project = self.db.project_detail(int(run["project_id"])) or {}
        for image in project.get("images", []):
            image.pop("stored_path", None)
        annotations = self.db.rows("""select i.sha256,q.key,a.value,a.note,a.annotator,a.updated_at,
            a.agreement,a.label_count,a.distinct_values,a.adjudicated_by
            from annotations a join images i on i.id=a.image_id join questions q on q.id=a.question_id
            where i.project_id=? order by i.sha256,q.key""", (run["project_id"],))
        # Ground truth ships with the panel that produced it: a reviewer can check
        # whether a claim rests on one person's habits without the pixels.
        labels = self.db.rows("""select i.sha256,q.key,l.annotator,l.value,l.note,l.is_adjudication,l.updated_at
            from annotation_labels l join images i on i.id=l.image_id join questions q on q.id=l.question_id
            where i.project_id=? order by i.sha256,q.key,l.annotator""", (run["project_id"],))
        agreement = self.agreement(int(run["project_id"]))
        responses = self.response_rows(run_id)
        manifest = {"schema": "fragilevision/replay@2", "version": __version__, "fingerprint": fingerprint,
                    "run": {key: value for key, value in run.items() if key != "provider_endpoint"},
                    "project": project, "annotations": annotations, "annotation_labels": labels,
                    "agreement": agreement, "responses": responses, "metrics": metrics}
        output = BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("eval.yaml", build_eval_yaml(run, fingerprint))
            archive.writestr("report.html", build_report(run, metrics, fingerprint, agreement))
            archive.writestr("report.md", build_report_markdown(run, metrics, fingerprint, agreement))
            archive.writestr("README.txt",
                "FragileVision Replay Bundle\n\nNo image pixels are included. The manifest contains hashes, exact prompts, "
                "ground truth, every individual reviewer judgement, the inter-annotator agreement report, "
                "raw model outputs, parser decisions and all reported metrics.\n")
        return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = f"FragileVision/{__version__}"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[fragilevision] {self.address_string()} {fmt % args}")

    def _headers(self, status: int, content_type: str, length: int, *, download: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "private, max-age=300")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str, *, download: str | None = None) -> None:
        self._headers(status, content_type, len(body), download=download)
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length non valido") from error
        if length <= 0 or length > MAX_JSON_BODY:
            raise ValueError("Corpo JSON vuoto o troppo grande")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/json":
            raise ValueError("Content-Type deve essere application/json")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Il corpo deve essere un oggetto JSON")
        return value

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-FragileVision-Token", ""), self.app.token)

    def _local_host(self) -> bool:
        """Refuse requests that did not ask for the loopback address by name.

        Binding to 127.0.0.1 is not enough on its own: a page whose own domain
        resolves to 127.0.0.1 becomes same-origin, and same-origin defeats both
        CORS and the mutation token. Checking Host closes DNS rebinding.
        """
        header = self.headers.get("Host", "")
        if not header:
            return False
        hostname, separator, port = header.rpartition(":")
        if not separator:
            hostname, port = port, ""
        if hostname.strip("[]").lower() not in {"127.0.0.1", "localhost", "::1"}:
            return False
        expected = str(self.server.server_address[1])
        return port == expected or (not port and expected == "80")

    def _guard(self) -> bool:
        if self._local_host():
            return True
        self._error(403, "Host non consentito: raggiungi FragileVision su 127.0.0.1")
        return False

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        try:
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path == "/api/bootstrap":
                return self._json(200, {"token": self.app.token, "version": __version__, "state": self.app.state()})
            if path == "/api/state":
                return self._json(200, self.app.state())
            if path == "/api/performance":
                return self._json(200, self.app.performance())
            if path == "/api/runs":
                return self._json(200, self.app.list_runs(**run_filters_from_query(query)))
            if path == "/api/runs/export":
                csv_bytes = self.app.export_runs_csv(**run_filters_from_query(query))
                return self._send(200, csv_bytes, "text/csv; charset=utf-8",
                                  download="fragilevision-esecuzioni.csv")
            if path == "/api/trash":
                return self._json(200, self.app.db.trash())
            if path == "/api/arena":
                raw_ids = (query.get("run_ids") or [""])[0]
                run_ids = list(dict.fromkeys(int(value) for value in raw_ids.split(",") if value.strip()))
                return self._json(200, self.app.arena(run_ids))
            if match := re.fullmatch(r"/api/projects/(\d+)", path):
                project = self.app.db.project_detail(int(match.group(1)))
                return self._json(200, project) if project else self._error(404, "Progetto inesistente")
            if match := re.fullmatch(r"/api/projects/(\d+)/audit", path):
                deep = (query.get("deep") or ["0"])[0] == "1"
                return self._json(200, self.app.audit(int(match.group(1)), verify_checksums=deep))
            if match := re.fullmatch(r"/api/projects/(\d+)/fingerprint", path):
                project_id = int(match.group(1))
                if not self.app.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
                    return self._error(404, "Progetto inesistente")
                return self._json(200, {"fingerprint": evaluation_fingerprint(self.app.db, project_id)})
            if match := re.fullmatch(r"/api/projects/(\d+)/annotations", path):
                question_id = int((query.get("question_id") or ["0"])[0])
                annotator = (query.get("annotator") or [""])[0]
                return self._json(200, self.app.annotation_board(int(match.group(1)), question_id, annotator))
            if match := re.fullmatch(r"/api/projects/(\d+)/agreement", path):
                return self._json(200, self.app.agreement(int(match.group(1))))
            if match := re.fullmatch(r"/api/projects/(\d+)/contested", path):
                raw_question = (query.get("question_id") or [""])[0]
                return self._json(200, self.app.contested_cases(
                    int(match.group(1)), int(raw_question) if raw_question.strip() else None))
            if match := re.fullmatch(r"/api/runs/(\d+)", path):
                run = self.app.run_detail(int(match.group(1)))
                return self._json(200, run) if run else self._error(404, "Esecuzione inesistente")
            if match := re.fullmatch(r"/api/runs/(\d+)/metrics", path):
                return self._json(200, self.app.metrics(int(match.group(1))))
            if match := re.fullmatch(r"/api/runs/(\d+)/diagnostics", path):
                return self._json(200, self.app.diagnostics(int(match.group(1))))
            if match := re.fullmatch(r"/api/runs/(\d+)/report", path):
                run_id = int(match.group(1)); run = self.app.run_detail(run_id)
                if not run:
                    return self._error(404, "Esecuzione inesistente")
                report = build_report(run, self.app.metrics(run_id),
                                      evaluation_fingerprint(self.app.db, run["project_id"]),
                                      self.app.agreement(int(run["project_id"])))
                return self._send(200, report.encode(), "text/html; charset=utf-8")
            if match := re.fullmatch(r"/api/runs/(\d+)/report\.md", path):
                run_id = int(match.group(1)); run = self.app.run_detail(run_id)
                if not run:
                    return self._error(404, "Esecuzione inesistente")
                report = build_report_markdown(run, self.app.metrics(run_id),
                                               evaluation_fingerprint(self.app.db, run["project_id"]),
                                               self.app.agreement(int(run["project_id"])))
                return self._send(200, report.encode(), "text/markdown; charset=utf-8",
                                  download=f"fragilevision-run-{run_id}.md")
            if match := re.fullmatch(r"/api/runs/(\d+)/bundle", path):
                run_id = int(match.group(1))
                return self._send(200, self.app.bundle(run_id), "application/zip", download=f"fragilevision-run-{run_id}.zip")
            if match := re.fullmatch(r"/media/(\d+)", path):
                image = self.app.db.row("select * from images where id=?", (int(match.group(1)),))
                if not image:
                    return self._error(404, "Immagine inesistente")
                source = Path(image["stored_path"])
                if not source.is_file() or source.stat().st_size > MAX_SERVED_IMAGE:
                    return self._error(404, "Immagine non disponibile")
                return self._send(200, source.read_bytes(), image["mime"])
            return self._static(path)
        except (ValueError, json.JSONDecodeError) as error:
            self._error(400, str(error))
        except Exception as error:
            traceback.print_exc()
            self._error(500, "Errore interno; controlla il terminale locale")

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if not self._authorized():
            return self._error(403, "Token locale non valido")
        try:
            path = urlparse(self.path).path
            if match := re.fullmatch(r"/api/runs/(\d+)", path):
                run_id = int(match.group(1))
                run = self.app.db.row("select id,status from runs where id=?", (run_id,))
                if not run:
                    return self._error(404, "Esecuzione inesistente")
                if run["status"] in {"queued", "running"}:
                    return self._error(409, "Ferma l’esecuzione prima di eliminarla")
                self.app.db.execute("delete from runs where id=?", (run_id,))
                return self._json(200, {"ok": True, "deleted_id": run_id})
            if match := re.fullmatch(r"/api/providers/(\d+)", path):
                provider_id = int(match.group(1))
                if not self.app.db.row("select id from providers where id=?", (provider_id,)):
                    return self._error(404, "Provider inesistente")
                if self.app.db.row("select id from runs where provider_id=? limit 1", (provider_id,)):
                    # No cascade: deleting the provider would silently take every
                    # run's history with it. Deleting the runs first is explicit.
                    return self._error(409, "Elimina prima le esecuzioni collegate a questo provider")
                self.app.db.execute("delete from providers where id=?", (provider_id,))
                return self._json(200, {"ok": True, "deleted_id": provider_id})
            return self._error(404, "Rotta inesistente")
        except Exception:
            traceback.print_exc()
            self._error(500, "Errore interno; controlla il terminale locale")

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if not self._authorized():
            return self._error(403, "Token locale non valido")
        try:
            parsed = urlparse(self.path); path = parsed.path
            body = self._body()
            if path == "/api/system/choose-directory":
                if not PICKER_LOCK.acquire(blocking=False):
                    raise ValueError("Un selettore cartelle è già aperto")
                try:
                    selected = choose_directory(str(body.get("purpose", "")))
                finally:
                    PICKER_LOCK.release()
                return self._json(200, {"directory": selected, "cancelled": selected is None})
            if path == "/api/projects":
                name = str(body.get("name", "")).strip()
                if not (2 <= len(name) <= 100):
                    raise ValueError("Il nome deve contenere da 2 a 100 caratteri")
                base, slug, suffix = slugify(name), slugify(name), 2
                while self.app.db.row("select id from projects where slug=?", (slug,)):
                    slug, suffix = f"{base}-{suffix}", suffix + 1
                project_id = self.app.db.execute("insert into projects(name,slug,description,created_at) values(?,?,?,?)",
                    (name, slug, str(body.get("description", ""))[:1000], self.app.db.now()))
                return self._json(201, self.app.db.project_detail(project_id))
            if match := re.fullmatch(r"/api/images/(\d+)/trash", path):
                image_id = int(match.group(1))
                image = self.app.db.row("""select i.id,i.project_id from images i join projects p on p.id=i.project_id
                    where i.id=? and i.deleted_at is null and p.deleted_at is null""", (image_id,))
                if not image:
                    raise ValueError("Immagine inesistente o già nel Cestino")
                if self.app.db.row("select id from runs where project_id=? and status in ('queued','running') limit 1", (image["project_id"],)):
                    raise ValueError("Ferma prima le esecuzioni attive del progetto")
                self.app.db.execute("update images set deleted_at=? where id=?", (self.app.db.now(), image_id))
                return self._json(200, {"ok": True, "trashed_id": image_id})
            if match := re.fullmatch(r"/api/images/(\d+)/restore", path):
                image_id = int(match.group(1))
                image = self.app.db.row("""select i.id from images i join projects p on p.id=i.project_id
                    where i.id=? and i.deleted_at is not null and p.deleted_at is null""", (image_id,))
                if not image:
                    raise ValueError("Immagine non ripristinabile; ripristina prima il progetto")
                self.app.db.execute("update images set deleted_at=null where id=?", (image_id,))
                return self._json(200, {"ok": True, "restored_id": image_id})
            if match := re.fullmatch(r"/api/projects/(\d+)/trash-images", path):
                project_id = int(match.group(1))
                if not self.app.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
                    raise ValueError("Progetto inesistente")
                if self.app.db.row("select id from runs where project_id=? and status in ('queued','running') limit 1", (project_id,)):
                    raise ValueError("Ferma prima le esecuzioni attive del progetto")
                self.app.db.execute("update images set deleted_at=? where project_id=? and deleted_at is null",
                                    (self.app.db.now(), project_id))
                return self._json(200, {"ok": True})
            if match := re.fullmatch(r"/api/projects/(\d+)/trash", path):
                project_id = int(match.group(1))
                if not self.app.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
                    raise ValueError("Progetto inesistente o già nel Cestino")
                if self.app.db.row("select id from runs where project_id=? and status in ('queued','running') limit 1", (project_id,)):
                    raise ValueError("Ferma prima le esecuzioni attive del progetto")
                self.app.db.execute("update projects set deleted_at=? where id=?", (self.app.db.now(), project_id))
                return self._json(200, {"ok": True, "trashed_id": project_id})
            if match := re.fullmatch(r"/api/projects/(\d+)/restore", path):
                project_id = int(match.group(1))
                if not self.app.db.row("select id from projects where id=? and deleted_at is not null", (project_id,)):
                    raise ValueError("Progetto non presente nel Cestino")
                self.app.db.execute("update projects set deleted_at=null where id=?", (project_id,))
                return self._json(200, {"ok": True, "restored_id": project_id})
            if match := re.fullmatch(r"/api/projects/(\d+)/split", path):
                project_id = int(match.group(1))
                if bool(body.get("clear")):
                    return self._json(200, self.app.clear_split(project_id))
                return self._json(200, self.app.assign_split(project_id,
                    seed=int(body.get("seed", 0)),
                    test_ratio=float(body.get("test_ratio", 0.3))))
            if match := re.fullmatch(r"/api/projects/(\d+)/import", path):
                result = import_directory(self.app.db, self.app.data_dir, int(match.group(1)),
                    str(body.get("directory", "")), str(body.get("source_group", "")), bool(body.get("recursive", True)))
                return self._json(200, result)
            if match := re.fullmatch(r"/api/projects/(\d+)/questions", path):
                project_id = int(match.group(1)); key = slugify(str(body.get("key") or body.get("label") or ""))
                label, text = str(body.get("label", "")).strip(), str(body.get("text", "")).strip()
                if not label or not text or len(text) > 2000:
                    raise ValueError("Etichetta e domanda canonica sono obbligatorie")
                with self.app.db.connect() as connection:
                    cursor = connection.execute("insert into questions(project_id,key,label,description,created_at) values(?,?,?,?,?)",
                        (project_id, key, label[:120], str(body.get("description", ""))[:1000], self.app.db.now()))
                    question_id = int(cursor.lastrowid)
                    connection.execute("""insert into variants(question_id,name,language,text,mutation_type,canonical,created_at)
                        values(?,?,?,?,?,?,?)""", (question_id, "Canonica", str(body.get("language", "it"))[:12], text,
                        "canonical", 1, self.app.db.now()))
                return self._json(201, self.app.db.project_detail(project_id))
            if match := re.fullmatch(r"/api/questions/(\d+)/variants", path):
                question_id = int(match.group(1)); question = self.app.db.row("select * from questions where id=?", (question_id,))
                if not question:
                    raise ValueError("Domanda inesistente")
                name, text = str(body.get("name", "")).strip(), str(body.get("text", "")).strip()
                if not name or not text or len(text) > 2000:
                    raise ValueError("Nome e testo della variante sono obbligatori")
                variant_id = self.app.db.execute("""insert into variants
                    (question_id,name,language,text,mutation_type,canonical,created_at) values(?,?,?,?,?,0,?)""",
                    (question_id, name[:120], str(body.get("language", "it"))[:12], text,
                     str(body.get("mutation_type", "manual"))[:40], self.app.db.now()))
                return self._json(201, {"id": variant_id})
            if match := re.fullmatch(r"/api/questions/(\d+)/edit", path):
                question_id=int(match.group(1)); question=self.app.db.row("select * from questions where id=?",(question_id,))
                if not question: raise ValueError("Domanda inesistente")
                label,text=str(body.get("label","")).strip(),str(body.get("text","")).strip()
                key=slugify(str(body.get("key") or label)); language=str(body.get("language") or "it").strip()[:12]
                if not label or not text or len(text)>2000: raise ValueError("Etichetta e testo canonico sono obbligatori")
                with self.app.db.connect() as connection:
                    connection.execute("update questions set key=?,label=?,description=? where id=?",
                        (key,label[:120],str(body.get("description", ""))[:1000],question_id))
                    connection.execute("update variants set text=?,language=? where question_id=? and canonical=1",
                        (text,language,question_id))
                return self._json(200,self.app.db.project_detail(int(question["project_id"])))
            if match := re.fullmatch(r"/api/questions/(\d+)/generate-variants", path):
                question_id = int(match.group(1))
                question = self.app.db.row("select * from questions where id=?", (question_id,))
                provider = self.app.db.row("select * from providers where id=?", (int(body.get("provider_id", 0)),))
                canonical = self.app.db.row("select text,language from variants where question_id=? and canonical=1", (question_id,))
                if not question or not canonical: raise ValueError("Domanda canonica inesistente")
                if not provider: raise ValueError("Modello locale inesistente")
                axes = [str(value) for value in body.get("axes") or []]
                variants, result = generate_stress_variants(provider, canonical["text"], axes,
                    str(body.get("language") or canonical["language"] or "it"))
                return self._json(200, {"variants":variants,"provider_id":provider["id"],
                    "latency_ms":result.latency_ms,"local_only":True})
            if path == "/api/annotations":
                return self._json(200, self.app.record_label(
                    image_id=int(body.get("image_id", 0)), question_id=int(body.get("question_id", 0)),
                    annotator=str(body.get("annotator", "human")), value=str(body.get("value", "")),
                    note=str(body.get("note", "")), is_adjudication=bool(body.get("is_adjudication"))))
            if path == "/api/annotations/withdraw":
                return self._json(200, self.app.remove_label(
                    image_id=int(body.get("image_id", 0)), question_id=int(body.get("question_id", 0)),
                    annotator=str(body.get("annotator", "human")),
                    is_adjudication=bool(body.get("is_adjudication"))))
            if path == "/api/providers":
                name, requested_kind = str(body.get("name", "")).strip(), str(body.get("kind", ""))
                is_demo = requested_kind == "simulator"
                kind = "ollama" if is_demo else requested_kind
                endpoint = "http://127.0.0.1" if is_demo else validate_private_endpoint(str(body.get("endpoint", "")))
                model = "fragilevision/synthetic-stressor-v1" if is_demo else str(body.get("model", "")).strip()
                if not name or kind not in {"ollama", "openai"} or not model or len(model) > 1000:
                    raise ValueError("Configurazione provider incompleta")
                provider_id = self.app.db.execute("insert into providers(name,kind,endpoint,model,is_demo,created_at) values(?,?,?,?,?,?)",
                    (name[:100], kind, endpoint, model, int(is_demo), self.app.db.now()))
                return self._json(201, {"id": provider_id})
            if path == "/api/providers/discover":
                models = discover_models(str(body.get("kind", "")), str(body.get("endpoint", "")))
                return self._json(200, {"models": models})
            if match := re.fullmatch(r"/api/providers/(\d+)/memory", path):
                return self._json(200, self.app.probe_memory(int(match.group(1))))
            if match := re.fullmatch(r"/api/providers/(\d+)/unload", path):
                return self._json(200, self.app.free_memory(int(match.group(1))))
            if match := re.fullmatch(r"/api/providers/(\d+)/test", path):
                provider = self.app.db.row("select * from providers where id=?", (int(match.group(1)),))
                if not provider:
                    raise ValueError("Provider inesistente")
                image = self.app.db.row("select * from images where project_id=? and deleted_at is null order by id limit 1",
                                        (int(body.get("project_id", 0)),))
                if not image:
                    raise ValueError("Importa almeno un’immagine per testare il provider")
                prepared = prepare_model_image(image, self.app.data_dir / "cache" / "model-inputs")
                result = call_provider(provider, prepared["bytes"], prepared["mime"],
                    build_prompt("Is the supplied file a visible image?"), temperature=0, seed=0, max_tokens=96, timeout=180)
                parsed = parse_verdict(result.raw)
                return self._json(200, {"ok": parsed.answer in {"yes", "no", "uncertain"},
                    "answer": parsed.answer, "format_valid": parsed.format_valid,
                    "latency_ms": result.latency_ms, "raw_preview": result.raw[:500],
                    "input_preprocess": prepared["preprocess"]})
            if path == "/api/arena/runs":
                project_id = int(body.get("project_id", 0))
                provider_ids = list(dict.fromkeys(int(value) for value in body.get("provider_ids") or []))
                if not 2 <= len(provider_ids) <= 8:
                    raise ValueError("Seleziona da 2 a 8 modelli")
                if not self.app.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
                    raise ValueError("Progetto inesistente")
                if not self.app.db.row("select id from images where project_id=? and deleted_at is null limit 1", (project_id,)):
                    raise ValueError("Importa almeno un’immagine")
                providers = self.app.db.rows(
                    f"select * from providers where id in ({','.join('?' for _ in provider_ids)})", tuple(provider_ids))
                if len(providers) != len(provider_ids):
                    raise ValueError("Uno o più modelli non esistono")
                if len({bool(provider["is_demo"]) for provider in providers}) != 1:
                    raise ValueError("Non inserire modelli DEMO e reali nella stessa Arena")
                provider_by_id = {int(provider["id"]): provider for provider in providers}
                config = run_config_from_body(body)
                valid_questions = self.app.db.rows(
                    f"select id from questions where project_id=? and id in ({','.join('?' for _ in config['question_ids'])})",
                    (project_id, *config["question_ids"]),
                )
                if len(valid_questions) != len(config["question_ids"]):
                    raise ValueError("Una o più domande non appartengono al progetto")
                config["variant_ids"] = resolve_variant_ids(self.app.db, project_id, config)
                base_name = str(body.get("name", "")).strip() or "Model Arena"
                created_at = self.app.db.now()
                run_ids = []
                with self.app.db.connect() as connection:
                    for offset, provider_id in enumerate(provider_ids):
                        provider = provider_by_id[provider_id]
                        run_name = f"{base_name[:105]} · {provider['name']}"
                        cursor = connection.execute("""insert into runs
                            (project_id,provider_id,name,status,config_json,created_at)
                            values(?,?,?,'queued',?,?)""", (project_id, provider_id, run_name[:160],
                            json.dumps(config, separators=(",", ":")), created_at + offset / 1000))
                        run_ids.append(int(cursor.lastrowid))
                self.app.start_batch(run_ids)
                return self._json(202, {"run_ids": run_ids, "runs": [self.app.run_detail(run_id) for run_id in run_ids],
                    "execution": "sequential"})
            if path == "/api/runs":
                project_id, provider_id = int(body.get("project_id", 0)), int(body.get("provider_id", 0))
                if not self.app.db.row("select id from projects where id=? and deleted_at is null", (project_id,)):
                    raise ValueError("Progetto inesistente")
                if not self.app.db.row("select id from providers where id=?", (provider_id,)):
                    raise ValueError("Provider inesistente")
                config = run_config_from_body(body)
                valid_questions = self.app.db.rows(
                    f"select id from questions where project_id=? and id in ({','.join('?' for _ in config['question_ids'])})",
                    (project_id, *config["question_ids"]))
                if len(valid_questions) != len(config["question_ids"]):
                    raise ValueError("Una o più domande non appartengono al progetto")
                config["variant_ids"] = resolve_variant_ids(self.app.db, project_id, config)
                name = str(body.get("name", "")).strip() or f"Run {self.app.db.now():.0f}"
                run_id = self.app.db.execute("""insert into runs(project_id,provider_id,name,status,config_json,created_at)
                    values(?,?,?,'queued',?,?)""", (project_id, provider_id, name[:160],
                    json.dumps(config, separators=(",", ":")), self.app.db.now()))
                self.app.start_run(run_id)
                return self._json(202, self.app.run_detail(run_id))
            if match := re.fullmatch(r"/api/runs/(\d+)/pause", path):
                run_id = int(match.group(1))
                run = self.app.db.row("select id,status from runs where id=?", (run_id,))
                if not run:
                    raise ValueError("Esecuzione inesistente")
                if run["status"] not in {"queued", "running"}:
                    raise ValueError("Si può mettere in pausa soltanto un’esecuzione in corso")
                self.app.db.execute("""update runs set status='paused',error=? where id=?
                    and status in ('queued','running')""",
                    ("Messa in pausa. Le risposte già ottenute sono conservate: usa Riprendi.", run_id))
                return self._json(200, {"ok": True, "status": "paused"})
            if match := re.fullmatch(r"/api/runs/(\d+)/resume", path):
                return self._json(202, self.app.resume_run(int(match.group(1))))
            if match := re.fullmatch(r"/api/runs/(\d+)/cancel", path):
                self.app.db.execute("update runs set status='cancelled',finished_at=? where id=? and status in ('queued','running')",
                                    (self.app.db.now(), int(match.group(1))))
                return self._json(200, {"ok": True})
            if match := re.fullmatch(r"/api/runs/(\d+)/rename", path):
                return self._json(200, self.app.rename_run(int(match.group(1)), str(body.get("name", ""))))
            if match := re.fullmatch(r"/api/runs/(\d+)/duplicate", path):
                return self._json(201, self.app.duplicate_run(int(match.group(1)), str(body.get("name", ""))))
            if match := re.fullmatch(r"/api/runs/(\d+)/archive", path):
                return self._json(200, self.app.archive_run(int(match.group(1))))
            if match := re.fullmatch(r"/api/runs/(\d+)/unarchive", path):
                return self._json(200, self.app.unarchive_run(int(match.group(1))))
            return self._error(404, "Rotta inesistente")
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self._error(400, str(error))
        except sqlite3.IntegrityError:
            self._error(400, "Nome, chiave o relazione già in uso")
        except Exception as error:
            traceback.print_exc()
            self._error(500, "Errore interno; controlla il terminale locale")

    def _static(self, request_path: str) -> None:
        static_root = Path(__file__).with_name("static").resolve()
        name = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (static_root / name).resolve()
        if static_root not in target.parents or not target.is_file():
            return self._error(404, "Risorsa inesistente")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App):
        self.app = app
        super().__init__(address, Handler)


def serve(data_dir: Path, host: str = "127.0.0.1", port: int = 7331) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Per sicurezza l'interfaccia può ascoltare soltanto sul loopback")
    app = App(data_dir)
    server = Server((host, port), app)
    # Off the startup path: the HTTP server must bind its port immediately,
    # never wait on a network probe that might hang if something answers
    # slowly on the loopback address. Runs only for the real application —
    # constructing an App directly (as every test does) never triggers it.
    threading.Thread(target=app.auto_configure_providers, daemon=True, name="fv-autoconfig").start()
    print(f"FragileVision {__version__} · http://{host}:{port}")
    print(f"Data: {app.data_dir}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
