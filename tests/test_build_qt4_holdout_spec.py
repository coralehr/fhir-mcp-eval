import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import build_qt4_holdout_spec as holdout


class BuildQt4HoldoutSpecTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.input_path = self.root / "questions.csv"
        rows = [
            {
                "split": "valid",
                "question_id": "dev-1",
                "question": "Development question one",
                "main_table_name": "chartevents",
            },
            {
                "split": "valid",
                "question_id": "dev-2",
                "question": "Development question two",
                "main_table_name": "chartevents",
            },
            {
                "split": "valid",
                "question_id": "hold-micro",
                "question": "What organism was found in the culture?",
                "main_table_name": "microbiologyevents",
            },
            {
                "split": "valid",
                "question_id": "hold-other",
                "question": "Which diagnosis was recorded?",
                "main_table_name": "diagnoses_icd",
            },
            {
                "split": "test",
                "question_id": "test-1",
                "question": "Test question",
                "main_table_name": "chartevents",
            },
        ]
        with self.input_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        self.manifest_path = self.root / "dev-manifest.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "kind": "a6_query_aware_packet_manifest",
                    "config": {
                        "split": "valid",
                        "limit": 2,
                        "planner": "question-only",
                    },
                    "input": {
                        "sha256": hashlib.sha256(
                            self.input_path.read_bytes()
                        ).hexdigest()
                    },
                    "questions": 2,
                    "packet_hashes": {"dev-1": "a", "dev-2": "b"},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_builds_exact_remainder_and_hash_order(self):
        result = holdout.build_holdout(
            input_path=self.input_path,
            development_manifest_path=self.manifest_path,
            split="valid",
            order_salt="test-salt:",
        )

        expected_ids = sorted(
            ["hold-micro", "hold-other"],
            key=lambda question_id: (
                hashlib.sha256(f"test-salt:{question_id}".encode()).hexdigest(),
                question_id,
            ),
        )
        self.assertEqual(result.question_ids, expected_ids)
        self.assertEqual(result.microbiology_question_ids, ["hold-micro"])
        self.assertEqual(result.development_question_count, 2)
        self.assertEqual(result.valid_question_count, 4)
        self.assertEqual(len(result.rows), 2)

    def test_rejects_manifest_not_matching_the_first_development_rows(self):
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["packet_hashes"] = {"dev-1": "a", "hold-other": "b"}
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "first 2 valid rows"):
            holdout.build_holdout(
                input_path=self.input_path,
                development_manifest_path=self.manifest_path,
                split="valid",
                order_salt="test-salt:",
            )

    def test_rejects_dispatch_label_disagreement(self):
        with self.input_path.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        rows[3]["question"] = "Which culture diagnosis was recorded?"
        with self.input_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["input"]["sha256"] = hashlib.sha256(
            self.input_path.read_bytes()
        ).hexdigest()
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "dispatcher and source stratum disagree"):
            holdout.build_holdout(
                input_path=self.input_path,
                development_manifest_path=self.manifest_path,
                split="valid",
                order_salt="test-salt:",
            )

    def test_rejects_input_hash_drift(self):
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["input"]["sha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "input SHA-256"):
            holdout.build_holdout(
                input_path=self.input_path,
                development_manifest_path=self.manifest_path,
                split="valid",
                order_salt="test-salt:",
            )


if __name__ == "__main__":
    unittest.main()
