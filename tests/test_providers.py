from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fragilevision.providers import (_ollama_output, _openai_base, _openai_chat_url, call_provider,
                                     discover_models, discover_ollama_vision_models,
                                     generate_stress_variants, parse_verdict, unload_memory,
                                     validate_private_endpoint)


class ProviderTests(unittest.TestCase):
    def test_strict_json(self):
        parsed = parse_verdict('{"answer":"yes"}')
        self.assertEqual(parsed.answer, "yes")
        self.assertTrue(parsed.format_valid)
        self.assertFalse(parse_verdict('{"answer":"yes","reason":"extra"}').format_valid)

    def test_thinking_and_fallback_are_recorded(self):
        parsed = parse_verdict('<think>long</think> La risposta è sì.')
        self.assertEqual(parsed.answer, "yes")
        self.assertFalse(parsed.format_valid)
        self.assertEqual(parsed.parser, "word")

    def test_truncated_reasoning_is_not_mined_for_a_verdict(self):
        """An unterminated <think> means the answer never arrived."""
        parsed = parse_verdict("<think>It is dark so there is no clear evidence, maybe yes")
        self.assertEqual(parsed.answer, "invalid")
        self.assertEqual(parsed.parser, "truncated")

    def test_italian_reflexive_si_is_not_an_affirmation(self):
        """"non si vede" is a NO; only the accented "sì" affirms."""
        self.assertEqual(parse_verdict("Non si vede alcuna luce accesa.").answer, "invalid")
        self.assertEqual(parse_verdict("Nell’immagine si vede una finestra.").answer, "invalid")
        self.assertEqual(parse_verdict("sì").answer, "yes")
        self.assertEqual(parse_verdict('{"answer":"si"}').answer, "yes")

    def test_prose_naming_two_verdicts_decides_nothing(self):
        parsed = parse_verdict("There is no doubt that the answer is yes.")
        self.assertEqual(parsed.answer, "invalid")
        self.assertEqual(parsed.parser, "ambiguous")
        self.assertEqual(parse_verdict("It is uncertain, so no.").answer, "invalid")

    def test_non_dict_json_does_not_raise(self):
        self.assertEqual(parse_verdict("123").answer, "invalid")

    def test_ollama_retries_without_the_thinking_switch(self):
        """Small vision models without a thinking mode must stay usable."""
        seen = []

        def fake(url, payload, timeout):
            seen.append(dict(payload))
            if "think" in payload:
                raise HTTPError(url, 400, "does not support thinking", {}, None)
            return {"message": {"content": '{"answer":"no"}'}, "prompt_eval_count": 5, "eval_count": 3}

        provider = {"kind": "ollama", "model": "llava:7b", "endpoint": "http://127.0.0.1:11434", "is_demo": 0}
        with patch("fragilevision.providers._request_json", side_effect=fake):
            result = call_provider(provider, b"\x00", "image/jpeg", "prompt")
        self.assertEqual(len(seen), 2)
        self.assertIn("think", seen[0])
        self.assertNotIn("think", seen[1])
        self.assertEqual(parse_verdict(result.raw).answer, "no")

    def test_invalid_is_not_silently_counted(self):
        self.assertEqual(parse_verdict("The photograph is beautiful").answer, "invalid")

    def test_ollama_thinking_field_is_used_when_content_is_empty(self):
        raw = _ollama_output({"message":{"role":"assistant","content":"","thinking":"{\"answer\":\"yes\"}"}})
        self.assertEqual(parse_verdict(raw).answer, "yes")

    def test_only_private_endpoints(self):
        self.assertEqual(validate_private_endpoint("http://127.0.0.1:11434"), "http://127.0.0.1:11434")
        with patch("fragilevision.providers.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("100.89.130.84", 443))]):
            self.assertEqual(validate_private_endpoint("https://model.example.ts.net/"), "https://model.example.ts.net")
        with self.assertRaises(ValueError):
            validate_private_endpoint("https://8.8.8.8/v1")
        with self.assertRaises(ValueError):
            validate_private_endpoint("http://127.0.0.1:11434?leak=true")

    def test_synthetic_stressor_is_deterministic_and_network_free(self):
        provider = {"kind": "ollama", "model": "synthetic", "endpoint": "https://8.8.8.8", "is_demo": 1}
        first = call_provider(provider, b"fake-image", "image/png", "prompt", seed=42)
        second = call_provider(provider, b"fake-image", "image/png", "prompt", seed=42)
        self.assertEqual(first.raw, second.raw)
        self.assertIn(parse_verdict(first.raw).answer, {"yes", "no"})

    def test_local_stress_generator_returns_only_requested_axes(self):
        provider = {"kind":"ollama","model":"synthetic","endpoint":"https://8.8.8.8","is_demo":1}
        variants, _ = generate_stress_variants(provider, "La luce è accesa?", ["negation","ambiguity"])
        self.assertEqual({item["axis"] for item in variants}, {"negation","ambiguity"})

    def test_model_discovery_normalizes_private_provider_catalogues(self):
        with patch("fragilevision.providers._get_json",
                   return_value={"models": [{"name": "qwen:8b"}, {"model": "gemma:4b"}]}):
            self.assertEqual(discover_models("ollama", "http://127.0.0.1:11434"),
                             ["gemma:4b", "qwen:8b"])
        with patch("fragilevision.providers._get_json", return_value={"data": [{"id": "mlx-vlm"}]}):
            self.assertEqual(discover_models("openai", "http://127.0.0.1:8080/v1"), ["mlx-vlm"])


    def test_openai_compatible_endpoints_are_normalized_to_v1(self):
        """LM Studio, mlx_lm.server, llama.cpp and vLLM all serve under /v1;
        the endpoint field's own placeholder gives no hint that it matters,
        so a bare host must not 404."""
        for endpoint, expected_base, expected_chat in [
            ("http://127.0.0.1:8080", "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/chat/completions"),
            ("http://127.0.0.1:8080/", "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/chat/completions"),
            ("http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/chat/completions"),
            ("http://127.0.0.1:8080/v1/chat/completions", "http://127.0.0.1:8080/v1",
             "http://127.0.0.1:8080/v1/chat/completions"),
        ]:
            self.assertEqual(_openai_base(endpoint), expected_base, endpoint)
            self.assertEqual(_openai_chat_url(endpoint), expected_chat, endpoint)

    def test_a_fully_custom_completions_url_is_respected_verbatim(self):
        custom = "http://127.0.0.1:8080/chat/completions"
        self.assertEqual(_openai_chat_url(custom), custom)

    def test_discover_models_reaches_v1_models_not_the_bare_host(self):
        """Regression: this 404'd before /v1 was added, against a bare host
        such as the endpoint field's own default placeholder."""
        import json as json_module
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        requested_paths = []

        class FakeOpenAI(BaseHTTPRequestHandler):
            def do_GET(self):
                requested_paths.append(self.path)
                if self.path != "/v1/models":
                    self.send_response(404); self.end_headers(); return
                body = json_module.dumps({"data": [{"id": "local-model"}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        models = discover_models("openai", f"http://127.0.0.1:{server.server_port}")

        self.assertEqual(models, ["local-model"])
        self.assertEqual(requested_paths, ["/v1/models"])


    def test_call_provider_also_reaches_v1_chat_completions(self):
        """The discover-models button and the actual evaluation call must
        agree on where the server lives; only one of them being fixed would
        still leave every real run 404ing against a bare-host endpoint."""
        import json as json_module
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        requested_paths = []

        class FakeOpenAI(BaseHTTPRequestHandler):
            def do_POST(self):
                requested_paths.append(self.path)
                length = int(self.headers.get("Content-Length", 0)); self.rfile.read(length)
                body = json_module.dumps({"choices": [{"message": {"content": '{"answer":"yes"}'}}],
                                          "usage": {"prompt_tokens": 10, "completion_tokens": 3}}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        provider = {"is_demo": 0, "kind": "openai", "model": "local-model",
                   "endpoint": f"http://127.0.0.1:{server.server_port}"}
        result = call_provider(provider, b"fake-bytes", "image/png", "Question?")

        self.assertEqual(requested_paths, ["/v1/chat/completions"])
        self.assertEqual(result.raw, '{"answer":"yes"}')


    def test_unload_sends_keep_alive_zero_to_generate(self):
        import json as json_module
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        received = []

        class FakeOllama(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received.append((self.path, json_module.loads(self.rfile.read(length))))
                body = json_module.dumps({"done_reason": "unload"}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        ok = unload_memory({"is_demo": 0, "kind": "ollama", "model": "qwen3-vl:8b",
                            "endpoint": f"http://127.0.0.1:{server.server_port}"})

        self.assertTrue(ok)
        self.assertEqual(received, [("/api/generate", {"model": "qwen3-vl:8b", "keep_alive": 0})])

    def test_unload_is_a_no_op_for_demo_and_openai_providers(self):
        self.assertFalse(unload_memory({"is_demo": 1, "kind": "ollama", "model": "x", "endpoint": "http://127.0.0.1"}))
        self.assertFalse(unload_memory({"is_demo": 0, "kind": "openai", "model": "x",
                                        "endpoint": "http://127.0.0.1:8080"}))

    def test_unload_against_an_unreachable_server_fails_quietly(self):
        self.assertFalse(unload_memory({"is_demo": 0, "kind": "ollama", "model": "x",
                                        "endpoint": "http://127.0.0.1:1"}))


    def test_vision_discovery_uses_the_declared_capability_not_the_name(self):
        """bge-m3 and other embedding/text-only models must never be mistaken
        for something FragileVision can point a camera at."""
        import json as json_module
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class FakeOllama(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json_module.dumps({"models": [
                    {"name": "qwen3-vl:8b", "capabilities": ["vision", "completion"]},
                    {"name": "bge-m3:latest", "capabilities": ["embedding"]},
                    {"name": "llama3.2:latest", "capabilities": ["completion", "tools"]},
                    {"name": "no-capabilities:latest"},
                ]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        models = discover_ollama_vision_models(f"http://127.0.0.1:{server.server_port}")

        self.assertEqual(models, ["qwen3-vl:8b"])


if __name__ == "__main__":
    unittest.main()
