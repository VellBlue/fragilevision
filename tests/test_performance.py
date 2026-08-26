from __future__ import annotations

import unittest

from fragilevision.performance import calculate_performance, estimate_eta_seconds, format_bytes


def response(provider_id, *, latency=800, prompt=600, completion=10, parser="json", ok=True, at=1.0):
    return {"provider_id": provider_id, "latency_ms": latency if ok else None,
            "prompt_tokens": prompt if ok else None, "completion_tokens": completion if ok else None,
            "parser": parser if ok else "error", "format_valid": 1 if ok else 0, "created_at": at}


def run(provider_id, *, status="completed", completed=10, total=10, runtime=10.0,
        memory=None, vram=None, sampled_at=None, at=1.0):
    return {"id": provider_id * 100 + total, "provider_id": provider_id, "status": status,
            "completed": completed, "total": total, "runtime_seconds": runtime, "created_at": at,
            "memory_bytes": memory, "memory_vram_bytes": vram, "memory_sampled_at": sampled_at}


class ProviderStatsTests(unittest.TestCase):
    def test_a_call_that_never_landed_is_an_error_not_a_bad_answer(self):
        responses = [response(1, ok=True) for _ in range(9)] + [response(1, ok=False)]
        report = calculate_performance(
            [{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}], responses, [])
        model = report["models"][0]
        self.assertEqual((model["responses_total"], model["responses_settled"], model["responses_errored"]),
                         (10, 9, 1))
        self.assertAlmostEqual(model["error_rate"], 0.1)

    def test_latency_and_tokens_are_computed_only_from_settled_calls(self):
        responses = [response(1, latency=100, completion=5), response(1, latency=300, completion=15),
                     response(1, ok=False)]
        report = calculate_performance(
            [{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}], responses, [])
        model = report["models"][0]
        self.assertEqual(model["median_ms"], 200)
        self.assertAlmostEqual(model["tokens_per_second"], 20 / 0.4)

    def test_throughput_is_charged_for_failed_calls_too_unlike_median_latency(self):
        """responses_per_second reflects real wall-clock cost, not just successful calls."""
        report = calculate_performance(
            [{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}],
            [response(1) for _ in range(6)] + [response(1, ok=False) for _ in range(4)],
            [run(1, completed=6, total=10, runtime=20.0)])
        self.assertAlmostEqual(report["models"][0]["responses_per_second"], 0.3)

    def test_a_provider_with_no_history_reports_dashes_not_zeros(self):
        report = calculate_performance([{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}], [], [])
        model = report["models"][0]
        self.assertIsNone(model["median_ms"])
        self.assertIsNone(model["responses_per_second"])
        self.assertIsNone(model["tokens_per_second"])


class MemoryTests(unittest.TestCase):
    def test_the_most_recent_reading_wins(self):
        runs = [run(1, memory=4_000_000_000, sampled_at=1.0), run(1, memory=6_000_000_000, sampled_at=5.0, total=20)]
        report = calculate_performance([{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}], [], runs)
        self.assertEqual(report["models"][0]["memory_bytes"], 6_000_000_000)

    def test_memory_is_never_observed_for_openai_compatible_or_demo_providers(self):
        providers = [{"id": 1, "name": "Cloud", "model": "m", "kind": "openai", "is_demo": 0},
                    {"id": 2, "name": "Demo", "model": "m", "kind": "ollama", "is_demo": 1}]
        report = calculate_performance(providers, [], [])
        for model in report["models"]:
            self.assertFalse(model["memory_observable"] and not model["is_demo"] and model["kind"] == "openai")
        openai_model = next(m for m in report["models"] if m["kind"] == "openai")
        demo_model = next(m for m in report["models"] if m["is_demo"])
        self.assertFalse(openai_model["memory_observable"])
        self.assertTrue(any("non è un’API standard" in w["text"] or "OpenAI-compatibile" in w["text"]
                            for w in openai_model["warnings"]))
        self.assertTrue(any("simulatore" in w["text"].lower() for w in demo_model["warnings"]))

    def test_near_identical_memory_and_vram_reads_as_unified_memory(self):
        runs = [run(1, memory=6_000_000_000, vram=5_950_000_000, sampled_at=1.0)]
        report = calculate_performance([{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}], [], runs)
        self.assertTrue(any("unificata" in w["text"] for w in report["models"][0]["warnings"]))

    def test_format_bytes_scales_units(self):
        self.assertEqual(format_bytes(None), None)
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(6_400_000_000), "6.0 GB")


class WarningTests(unittest.TestCase):
    def test_a_high_error_rate_is_flagged_only_with_enough_calls(self):
        few = calculate_performance([{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}],
                                    [response(1, ok=False)], [])
        self.assertFalse(any(w["severity"] == "alta" for w in few["models"][0]["warnings"]))
        many = calculate_performance([{"id": 1, "name": "M", "model": "m", "kind": "ollama", "is_demo": 0}],
                                     [response(1, ok=False) for _ in range(3)] + [response(1) for _ in range(7)], [])
        self.assertTrue(any(w["severity"] == "alta" for w in many["models"][0]["warnings"]))


class EtaTests(unittest.TestCase):
    def test_eta_uses_this_models_own_median_latency(self):
        self.assertAlmostEqual(estimate_eta_seconds([800, 1000, 1200], 5), 5.0)

    def test_no_pending_work_is_zero_regardless_of_history(self):
        self.assertEqual(estimate_eta_seconds([800], 0), 0.0)

    def test_no_history_is_an_honest_unknown_not_a_zero(self):
        self.assertIsNone(estimate_eta_seconds([], 5))


if __name__ == "__main__":
    unittest.main()
