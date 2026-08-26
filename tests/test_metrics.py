from __future__ import annotations

import unittest

from fragilevision.metrics import calculate_arena, calculate_metrics, exact_mcnemar_p, paired_difference_interval, wilson_interval


class MetricsTests(unittest.TestCase):
    def row(self, image, variant, answer, truth, canonical=False, repetition=0, source_group=""):
        return {
            "image_id": image, "question_id": 1, "question_key": "light",
            "variant_id": variant, "variant_name": "Canonical" if canonical else "Negated",
            "language": "en", "mutation_type": "canonical" if canonical else "negation",
            "canonical": canonical, "ground_truth": truth, "answer": answer,
            "format_valid": True, "latency_ms": 100, "repetition": repetition,
            "source_group": source_group,
        }

    def test_wilson_is_bounded(self):
        low, high = wilson_interval(8, 10)
        self.assertGreater(low, 0)
        self.assertLess(high, 1)
        self.assertLess(low, 0.8)
        self.assertGreater(high, 0.8)

    def test_exact_mcnemar_edge_cases(self):
        self.assertEqual(exact_mcnemar_p(0, 0), 1.0)
        self.assertEqual(exact_mcnemar_p(1, 0), 1.0)
        self.assertLess(exact_mcnemar_p(10, 0), 0.01)

    def test_fragility_is_paired_and_separate_from_accuracy(self):
        rows = [
            self.row(1, 10, "yes", "yes", True),
            self.row(2, 10, "no", "no", True),
            self.row(1, 11, "no", "yes"),
            self.row(2, 11, "no", "no"),
        ]
        result = calculate_metrics(rows)
        self.assertEqual(result["summary"]["prompt_fragility_score"], 50.0)
        self.assertEqual(result["summary"]["majority_baseline"], 0.5)
        canonical = next(item for item in result["variants"] if item["canonical"])
        alternative = next(item for item in result["variants"] if not item["canonical"])
        self.assertEqual(canonical["accuracy"], 1.0)
        self.assertEqual(alternative["accuracy"], 0.5)
        self.assertEqual(result["summary"]["repeat_instability_score"], 0.0)

    def test_repeat_drift_is_not_prompt_fragility(self):
        rows = [
            self.row(1, 10, "yes", "yes", True, 0),
            self.row(1, 10, "no", "yes", True, 1),
        ]
        result = calculate_metrics(rows)
        self.assertEqual(result["summary"]["prompt_fragility_score"], 0.0)
        self.assertEqual(result["summary"]["repeat_instability_score"], 100.0)

    def test_scene_balance_and_evidence_gate_are_explicit(self):
        rows = [
            self.row(1, 10, "yes", "yes", True, source_group="scene-a"),
            self.row(2, 10, "no", "no", True, source_group="scene-a"),
            self.row(1, 11, "no", "yes", source_group="scene-a"),
            self.row(2, 11, "no", "no", source_group="scene-a"),
        ]
        result = calculate_metrics(rows)
        self.assertEqual(result["summary"]["independent_groups"], 1)
        self.assertAlmostEqual(result["summary"]["scene_balanced_accuracy"], 0.75)
        self.assertEqual(result["evidence_gate"]["grade"], "C")
        self.assertFalse(next(item for item in result["evidence_gate"]["checks"] if item["key"] == "groups")["passed"])

    def test_paired_test_uses_repeat_majority_not_last_response(self):
        rows = [
            self.row(1, 10, "yes", "yes", True, 0),
            self.row(1, 10, "yes", "yes", True, 1),
            self.row(1, 10, "no", "yes", True, 2),
            self.row(1, 11, "no", "yes", False, 0),
            self.row(1, 11, "no", "yes", False, 1),
            self.row(1, 11, "yes", "yes", False, 2),
        ]
        comparison = calculate_metrics(rows)["comparisons"][0]
        self.assertEqual(comparison["canonical_only_correct"], 1)
        self.assertEqual(comparison["alternative_only_correct"], 0)

    def test_model_arena_ranks_and_compares_only_paired_units(self):
        config = {"question_ids":[1],"variant_ids":[],"repetitions":1,"temperature":0,"seed":7,"max_tokens":96}
        run_a = {"id":1,"project_id":4,"status":"completed","name":"Arena · A","provider_name":"A",
                 "provider_model":"model-a","provider_is_demo":0,"config":config,"started_at":10,"finished_at":14,"completed":4}
        run_b = {"id":2,"project_id":4,"status":"completed","name":"Arena · B","provider_name":"B",
                 "provider_model":"model-b","provider_is_demo":0,"config":config,"started_at":20,"finished_at":28,"completed":4}
        truths = ["yes","no","yes","no"]
        answers_a = ["yes","no","no","no"]
        answers_b = ["yes","yes","no","no"]
        rows_a = [self.row(index,10,answer,truth,True) for index,(answer,truth) in enumerate(zip(answers_a,truths),1)]
        rows_b = [self.row(index,10,answer,truth,True) | {"latency_ms":200} for index,(answer,truth) in enumerate(zip(answers_b,truths),1)]
        arena = calculate_arena([{"run":run_a,"rows":rows_a},{"run":run_b,"rows":rows_b}])
        self.assertEqual(arena["models"][0]["run_id"], 1)
        self.assertEqual(arena["compatibility"]["common_units"], 4)
        self.assertEqual(arena["pairwise"][0]["paired"], 4)
        self.assertEqual(arena["pairwise"][0]["a_only_correct"], 1)
        self.assertAlmostEqual(arena["pairwise"][0]["accuracy_delta"], 0.25)
        self.assertEqual(arena["leaders"]["speed"], 1)

    def arena_run(self, run_id, name, **overrides):
        config = {"question_ids":[1],"variant_ids":[],"repetitions":1,"temperature":0,"seed":7,"max_tokens":96}
        return {"id":run_id,"project_id":9,"status":"completed","name":f"Arena · {name}","provider_name":name,
                "provider_model":name.lower(),"provider_is_demo":0,"config":config,
                "started_at":0,"finished_at":10,"completed":10} | overrides

    def test_model_arena_ranks_on_common_units_only(self):
        """A model that gives up on the hard half must not win by shrinking its sample."""
        truths = ["yes"] * 5 + ["no"] * 5
        # HONEST answers everything and is wrong on four hard cases.
        honest = [self.row(index + 1, 10, "yes" if index < 6 else "no", truth, True, source_group=f"g{index}")
                  for index, truth in enumerate(truths)]
        # QUITTER answers only the five easy cases and returns nothing readable elsewhere.
        quitter = [self.row(index + 1, 10, truth if index < 5 else "invalid", truth, True, source_group=f"g{index}")
                   for index, truth in enumerate(truths)]
        arena = calculate_arena([
            {"run": self.arena_run(1, "HONEST"), "rows": honest},
            {"run": self.arena_run(2, "QUITTER"), "rows": quitter},
        ])
        by_name = {model["provider_name"]: model for model in arena["models"]}
        self.assertEqual(arena["compatibility"]["common_units"], 5)
        self.assertFalse(arena["compatibility"]["fully_matched"])
        # Both are perfect on the units they actually share.
        self.assertEqual(by_name["HONEST"]["accuracy"], by_name["QUITTER"]["accuracy"])
        self.assertEqual(by_name["HONEST"]["matched_units"], by_name["QUITTER"]["matched_units"])
        # The unmatched self-reported figure stays visible, but never ranks.
        self.assertAlmostEqual(by_name["HONEST"]["own_accuracy"], 0.9)
        self.assertAlmostEqual(by_name["QUITTER"]["own_accuracy"], 1.0)
        self.assertAlmostEqual(by_name["QUITTER"]["coverage"], 0.5)
        self.assertIn("copertura", arena["warning"])

    def test_model_arena_refuses_a_comparison_without_common_units(self):
        first = [self.row(1, 10, "yes", "yes", True), self.row(2, 10, "yes", "yes", True)]
        second = [self.row(3, 10, "yes", "yes", True), self.row(4, 10, "yes", "yes", True)]
        with self.assertRaises(ValueError) as caught:
            calculate_arena([{"run": self.arena_run(1, "A"), "rows": first},
                             {"run": self.arena_run(2, "B"), "rows": second}])
        self.assertIn("Nessuna unità comune", str(caught.exception))

    def test_balanced_accuracy_is_undefined_with_a_single_class(self):
        rows = [self.row(index, 10, "yes", "yes", True) for index in range(1, 5)]
        variant = calculate_metrics(rows)["variants"][0]
        self.assertIsNone(variant["balanced_accuracy"])
        rows.append(self.row(9, 10, "no", "no", True))
        self.assertIsNotNone(calculate_metrics(rows)["variants"][0]["balanced_accuracy"])

    def test_split_repetitions_are_reported_as_ties_not_hidden(self):
        rows = [self.row(1, 10, "yes", "yes", True, repetition=0),
                self.row(1, 10, "no", "yes", True, repetition=1)]
        summary = calculate_metrics(rows)["summary"]
        self.assertEqual(summary["tie_units"], 1)
        self.assertEqual(summary["ground_truth_cases"], 1)

    def test_parser_breakdown_separates_strict_json_from_prose(self):
        strict = [self.row(index, 10, "yes", "yes", True) | {"parser": "json"} for index in range(1, 4)]
        prose = [self.row(index, 10, "yes", "no", True) | {"parser": "word"} for index in range(4, 6)]
        metrics = calculate_metrics(strict + prose)
        breakdown = {item["parser"]: item for item in metrics["parser_breakdown"]}
        self.assertEqual(breakdown["json"]["accuracy"], 1.0)
        self.assertEqual(breakdown["word"]["accuracy"], 0.0)
        self.assertAlmostEqual(metrics["summary"]["strict_share"], 0.6)
        self.assertEqual(metrics["summary"]["strict_accuracy"], 1.0)

    def test_model_arena_rejects_incompatible_protocols(self):
        base = {"id":1,"project_id":1,"status":"completed","provider_is_demo":0,
                "config":{"question_ids":[1],"repetitions":1,"temperature":0,"seed":0,"max_tokens":96}}
        changed = base | {"id":2,"config":base["config"] | {"seed":1}}
        with self.assertRaisesRegex(ValueError, "non sono compatibili"):
            calculate_arena([{"run":base,"rows":[]},{"run":changed,"rows":[]}])

    def test_model_arena_collapses_repetitions_before_accuracy(self):
        config = {"question_ids":[1],"repetitions":3,"temperature":0,"seed":0,"max_tokens":96}
        base = {"project_id":1,"status":"completed","provider_is_demo":0,"config":config,
                "started_at":1,"finished_at":2,"completed":3}
        run_a = base | {"id":1,"provider_name":"A","provider_model":"a"}
        run_b = base | {"id":2,"provider_name":"B","provider_model":"b"}
        rows_a = [self.row(1,10,answer,"yes",True,repetition) for repetition,answer in enumerate(["yes","yes","no"])]
        rows_b = [self.row(1,10,answer,"yes",True,repetition) for repetition,answer in enumerate(["no","no","yes"])]
        arena = calculate_arena([{"run":run_a,"rows":rows_a},{"run":run_b,"rows":rows_b}])
        self.assertEqual(arena["models"][0]["accuracy"], 1.0)
        self.assertEqual(arena["models"][1]["accuracy"], 0.0)
        self.assertEqual(arena["models"][0]["evaluated_units"], 1)
        self.assertEqual(arena["pairwise"][0]["paired"], 1)

    def test_paired_difference_interval_contains_observed_delta(self):
        low, high = paired_difference_interval([1,0,0,-1,1])
        self.assertLessEqual(low, 0.2)
        self.assertGreaterEqual(high, 0.2)


if __name__ == "__main__":
    unittest.main()
