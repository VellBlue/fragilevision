from __future__ import annotations

import unittest

from fragilevision.agreement import (bootstrap_interval, calculate_agreement, cohen_kappa,
                                     fleiss_kappa, krippendorff_alpha, observed_agreement,
                                     resolve_labels)


def label(annotator: str, value: str, *, image: int = 1, question: int = 1,
          adjudication: bool = False, at: float = 1.0) -> dict:
    return {"image_id": image, "question_id": question, "annotator": annotator, "value": value,
            "note": "", "is_adjudication": int(adjudication), "updated_at": at}


class CoefficientTests(unittest.TestCase):
    def test_krippendorff_matches_the_published_reference_dataset(self):
        """Krippendorff (2011), the canonical nominal example: alpha = 0.743."""
        observers = {
            "A": [1, 2, 3, 3, 2, 1, 4, 1, 2, None, None, None, None, None, None],
            "B": [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, None, None, None, None, None],
            "C": [None, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, None, None, None, None],
            "D": [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, None, None, None, None],
        }
        units = [tuple(str(observers[name][index]) for name in "ABCD" if observers[name][index] is not None)
                 for index in range(15)]
        self.assertAlmostEqual(krippendorff_alpha(units), 0.743, places=3)

    def test_fleiss_matches_the_published_reference_dataset(self):
        counts = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
                  [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]
        units = [tuple(str(index) for index, count in enumerate(row) for _ in range(count)) for row in counts]
        self.assertAlmostEqual(fleiss_kappa(units), 0.2099, places=4)

    def test_fleiss_is_undefined_when_the_panel_changes_size(self):
        """Fleiss assumes a fixed number of raters; real annotation rarely has one."""
        units = [("yes", "no"), ("yes", "no", "yes"), ("no", "no")]
        self.assertIsNone(fleiss_kappa(units))
        self.assertIsNotNone(krippendorff_alpha(units))

    def test_cohen_matches_a_hand_computed_table(self):
        pairs = [("yes", "yes")] * 20 + [("no", "no")] * 15 + [("yes", "no")] * 5 + [("no", "yes")] * 10
        self.assertAlmostEqual(cohen_kappa(pairs), 0.4, places=6)

    def test_perfect_agreement_on_one_category_has_no_coefficient(self):
        """The kappa paradox: chance already explains everything, so nothing is left."""
        self.assertIsNone(cohen_kappa([("yes", "yes")] * 30))
        self.assertIsNone(krippendorff_alpha([("yes", "yes")] * 30))
        self.assertEqual(observed_agreement([("yes", "yes")] * 30), 1.0)

    def test_the_bootstrap_is_deterministic_and_brackets_the_estimate(self):
        units = [("yes", "yes")] * 20 + [("no", "no")] * 15 + [("yes", "no")] * 5
        first = bootstrap_interval(units, krippendorff_alpha, resamples=200)
        second = bootstrap_interval(units, krippendorff_alpha, resamples=200)
        self.assertEqual(first, second)
        self.assertLessEqual(first[0], krippendorff_alpha(units))
        self.assertGreaterEqual(first[1], krippendorff_alpha(units))

    def test_too_few_cases_get_no_interval_instead_of_a_fake_one(self):
        self.assertEqual(bootstrap_interval([("yes", "no"), ("no", "no")], krippendorff_alpha), (None, None))


class ConsensusTests(unittest.TestCase):
    def test_one_reviewer_is_recorded_as_single_not_as_agreement(self):
        resolved = resolve_labels([label("simone", "yes")])
        self.assertEqual((resolved["value"], resolved["agreement"], resolved["label_count"]),
                         ("yes", "single", 1))

    def test_identical_judgements_are_unanimous(self):
        resolved = resolve_labels([label("simone", "no"), label("anna", "no")])
        self.assertEqual((resolved["value"], resolved["agreement"]), ("no", "unanimous"))

    def test_a_strict_majority_decides(self):
        resolved = resolve_labels([label("a", "yes"), label("b", "yes"), label("c", "no")])
        self.assertEqual((resolved["value"], resolved["agreement"], resolved["distinct_values"]),
                         ("yes", "majority", 2))

    def test_an_even_split_is_a_conflict_and_never_a_verdict(self):
        resolved = resolve_labels([label("a", "yes"), label("b", "no")])
        self.assertEqual(resolved["agreement"], "conflict")
        # `uncertain` is exactly what the accuracy metrics exclude, which is the
        # point: an unresolved disagreement must not become a benchmark number.
        self.assertEqual(resolved["value"], "uncertain")

    def test_a_plurality_is_not_a_consensus(self):
        resolved = resolve_labels([label("a", "yes"), label("b", "yes"),
                                   label("c", "no"), label("d", "uncertain"), label("e", "exclude")])
        self.assertEqual(resolved["agreement"], "conflict")

    def test_an_adjudication_overrides_the_panel_and_the_latest_one_wins(self):
        resolved = resolve_labels([label("a", "yes"), label("b", "no"),
                                   label("anna", "yes", adjudication=True, at=1.0),
                                   label("simone", "exclude", adjudication=True, at=2.0)])
        self.assertEqual((resolved["value"], resolved["agreement"], resolved["adjudicated_by"]),
                         ("exclude", "adjudicated", "simone"))
        # The independent labels still count: the panel is not erased by the ruling.
        self.assertEqual(resolved["label_count"], 2)


class ReportTests(unittest.TestCase):
    def build(self):
        labels = []
        for index in range(24):
            labels.append(label("simone", "yes" if index % 2 else "no", image=index))
            if index < 16:
                flipped = index in {3, 5}
                labels.append(label("anna", ("no" if index % 2 else "yes") if flipped
                                    else ("yes" if index % 2 else "no"), image=index))
        return calculate_agreement(labels, [{"id": 1, "key": "luce", "label": "Luce accesa"}],
                                   ["single"] * 8 + ["unanimous"] * 14 + ["conflict"] * 2)

    def test_the_report_names_the_panel_and_measures_it(self):
        report = self.build()
        self.assertEqual(report["annotator_count"], 2)
        self.assertEqual(report["overall"]["reliability_units"], 16)
        self.assertEqual(report["overall"]["panel_sizes"], {2: 16})
        self.assertAlmostEqual(report["overall"]["percent_agreement"], 14 / 16)
        self.assertEqual(len(report["pairs"]), 1)
        self.assertEqual(report["pairs"][0]["shared"], 16)
        self.assertIsNotNone(report["pairs"][0]["cohen_kappa"])
        self.assertEqual(report["questions"][0]["reliability_units"], 16)

    def test_a_lone_reviewer_is_reported_as_a_high_severity_warning(self):
        report = calculate_agreement([label("simone", "yes", image=index) for index in range(30)],
                                     [{"id": 1, "key": "k", "label": "L"}], ["single"] * 30)
        self.assertIsNone(report["overall"]["krippendorff_alpha"])
        self.assertEqual(report["overall"]["reliability_units"], 0)
        top = report["warnings"][0]
        self.assertEqual(top["severity"], "alta")
        self.assertIn("Un solo revisore", top["text"])


if __name__ == "__main__":
    unittest.main()
