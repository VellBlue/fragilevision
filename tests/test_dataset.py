from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fragilevision.core import hamming_distance, perceptual_hash
from fragilevision.dataset import (analyze_balance, analyze_resolution, build_split,
                                   find_near_duplicates, inspect_files, inspect_split)


def image(image_id, group="", split="", width=4000, height=3000):
    return {"id": image_id, "sha256": f"{image_id:064x}", "filename": f"f{image_id}.jpg",
            "stored_path": f"/nowhere/{image_id}.jpg", "source_group": group, "split": split,
            "width": width, "height": height, "mime": "image/jpeg"}


class DatasetTests(unittest.TestCase):
    def scenes(self, count=6, per_scene=2):
        return [image(scene * per_scene + index + 1, f"scena-{scene}")
                for scene in range(count) for index in range(per_scene)]

    def test_the_hash_reads_shape_not_exposure(self):
        """Comparing neighbours is what makes one photograph survive a re-export."""
        gradient = [x / 16 for _ in range(8) for x in range(9)]
        brighter = [value + 0.25 for value in gradient]
        self.assertEqual(perceptual_hash(9, 8, gradient), perceptual_hash(9, 8, brighter))
        self.assertEqual(hamming_distance("", "abcd"), 64)

    def test_a_near_duplicate_across_two_groups_is_singled_out(self):
        """Two frames of one scene filed as two groups defeat scene balancing."""
        found = find_near_duplicates(
            [image(1, "alba"), image(2, "alba"), image(3, "tramonto")],
            {1: "ffffffffffffffff", 2: "ffffffffffffff00", 3: "ffffffffffffff01"})
        self.assertEqual(found["pair_count"], 3)
        self.assertEqual(found["cross_group_pairs"], 2)
        self.assertEqual(found["identical_pairs"], 1)

    def test_a_near_duplicate_pair_never_lands_on_both_sides(self):
        """The point of the whole feature: test must not score on what train holds."""
        images = self.scenes()
        pair = [{"a_id": 1, "b_id": 11, "distance": 2}]  # crosses scena-0 and scena-5
        for seed in range(20):
            assignment = build_split(images, pair, seed=seed, test_ratio=0.34)["assignment"]
            self.assertEqual(assignment[1], assignment[11], f"seed {seed} ha separato la coppia")

    def test_a_source_group_is_never_split(self):
        result = build_split(self.scenes(count=8, per_scene=3), [], seed=3, test_ratio=0.25)
        for scene in range(8):
            sides = {result["assignment"][scene * 3 + index + 1] for index in range(3)}
            self.assertEqual(len(sides), 1, f"scena-{scene} divisa fra {sides}")

    def test_the_split_is_reproducible_from_its_seed(self):
        images = self.scenes()
        self.assertEqual(build_split(images, [], seed=11, test_ratio=0.3)["assignment"],
                         build_split(images, [], seed=11, test_ratio=0.3)["assignment"])

    def test_a_single_scene_dataset_is_refused_rather_than_faked(self):
        with self.assertRaises(ValueError) as caught:
            build_split([image(index, "unico") for index in range(1, 9)], [], seed=0, test_ratio=0.3)
        self.assertIn("unità indipendenti", str(caught.exception))

    def test_a_leak_across_an_existing_split_is_reported(self):
        report = inspect_split([image(1, "a", split="train"), image(2, "b", split="test")],
                               [{"a_id": 1, "b_id": 2, "distance": 1}])
        self.assertEqual(report["leak_count"], 1)

    def test_a_dominant_class_and_a_thin_minority_are_called_out(self):
        annotations = [{"question_id": 1, "value": "yes"} for _ in range(19)] + [{"question_id": 1, "value": "no"}]
        report = analyze_balance([{"id": 1, "key": "luce", "label": "Luce"}], annotations, image_count=20)[0]
        self.assertAlmostEqual(report["majority_share"], 0.95)
        self.assertEqual(len(report["warnings"]), 2)

    def test_a_missing_file_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            report = inspect_files([{"id": 1, "sha256": "y", "filename": "gone.png",
                                     "stored_path": str(Path(directory) / "gone.png"),
                                     "width": 4, "height": 4}])
            self.assertEqual(report["missing_count"], 1)
            self.assertFalse(report["checksums_verified"])

    def test_resolution_outliers_and_model_downscaling_are_separate_facts(self):
        images = [image(index) for index in range(1, 9)] + [image(9, width=320, height=240)]
        report = analyze_resolution(images)
        self.assertEqual(report["outlier_count"], 1)
        self.assertEqual(report["downscaled_for_model"], 8)


if __name__ == "__main__":
    unittest.main()
