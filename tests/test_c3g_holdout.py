import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

import c3g_holdout


def row(question: str, patient: str, template: str = "t1", table: str = "lab"):
    return {
        "question_id": question,
        "patient_fhir_id": patient,
        "template_id": template,
        "main_table_name": table,
    }


class C3GHoldoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = b"k" * 32

    def digest(self, namespace: str, value: str) -> str:
        return hmac.new(
            self.key,
            f"c3g-{namespace}-v1\0{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def registry(self, questions=(), patients=()):
        return {
            "schema_version": "c3g-burned-registry-v1",
            "key_id": "test-key",
            "question_hmacs": sorted(self.digest("question", value) for value in questions),
            "patient_hmacs": sorted(self.digest("patient", value) for value in patients),
        }

    def candidates(self, count=60):
        return [
            row(
                f"q{i:03d}",
                f"Patient/p{i:03d}",
                template=f"t{i % 3}",
                table=("lab", "med", "visit")[i % 3],
            )
            for i in range(count)
        ]

    def test_rejects_question_or_patient_seen_in_prior_artifacts(self) -> None:
        candidates = self.candidates()
        burned = self.registry(questions=["q002"], patients=["Patient/p003"])

        with self.assertRaisesRegex(ValueError, "burned question"):
            c3g_holdout.select_holdout(
                candidates,
                burned_registry=burned,
                key=self.key,
                key_id="test-key",
                seed=7,
                target_questions=40,
                min_patients=40,
            )

        clean = [item for item in candidates if item["question_id"] != "q002"]
        with self.assertRaisesRegex(ValueError, "burned Patient"):
            c3g_holdout.select_holdout(
                clean,
                burned_registry=burned,
                key=self.key,
                key_id="test-key",
                seed=7,
                target_questions=40,
                min_patients=40,
            )

    def test_whole_patient_selection_is_deterministic_and_disjoint(self) -> None:
        candidates = self.candidates(70)
        first = c3g_holdout.select_holdout(
            candidates,
            burned_registry=self.registry(),
            key=self.key,
            key_id="test-key",
            seed=20260723,
            target_questions=45,
            min_patients=40,
        )
        second = c3g_holdout.select_holdout(
            list(reversed(candidates)),
            burned_registry=self.registry(),
            key=self.key,
            key_id="test-key",
            seed=20260723,
            target_questions=45,
            min_patients=40,
        )

        self.assertEqual(first.private_rows, second.private_rows)
        self.assertGreaterEqual(len(first.private_rows), 45)
        self.assertGreaterEqual(first.receipt["selected_patient_clusters"], 40)
        self.assertNotIn("question_id", json.dumps(first.receipt))
        self.assertNotIn("patient_fhir_id", json.dumps(first.receipt))
        self.assertEqual(
            sum(first.receipt["strata_counts"].values()),
            len(first.private_rows),
        )

    def test_rejects_duplicate_question_and_patient_split_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate question"):
            c3g_holdout.validate_rows([row("q1", "p1"), row("q1", "p2")])

    def test_key_file_must_be_private_and_at_least_32_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(self.key)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "mode 0400 or 0600"):
                c3g_holdout.load_key(path)
            path.chmod(0o600)
            self.assertEqual(c3g_holdout.load_key(path), self.key)
            path.write_bytes(b"short")
            with self.assertRaisesRegex(ValueError, "at least 32 bytes"):
                c3g_holdout.load_key(path)


if __name__ == "__main__":
    unittest.main()
