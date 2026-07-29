import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import public_item_record as records


REPO = Path(__file__).resolve().parents[1]


def valid_record() -> dict:
    return {
        "schema_version": "public-eval-item-v1",
        "record_id": "pubitem_0123456789abcdef01234567",
        "experiment_id": "a6a-run2",
        "question_id": "pubq_0123456789abcdef01234567",
        "cluster_id": "pubc_0123456789abcdef01234567",
        "arm_id": "pubarm_0123456789abcdef",
        "grading_id": "grade_0123456789abcdef01234567",
        "evidence": {
            "selected_resource_ids": [
                "pubres_0123456789abcdef01234567",
                "pubres_89abcdef0123456701234567",
            ],
            "packet_sha256": "a" * 64,
            "restricted_source_sha256": "b" * 64,
        },
        "execution": {
            "provider": "provider-family",
            "model": "model-version",
            "server": "server-version",
            "prompt_sha256": "c" * 64,
            "schema_sha256": "d" * 64,
            "reasoning_effort": "high",
            "seed": 7,
            "retry_policy": "no-result-shopping-v1",
        },
        "outcome": {
            "representation": "categorical_score",
            "value": "correct",
            "answer_sha256": "e" * 64,
        },
        "grading": {
            "deterministic_grade": 1,
            "panel_votes": [1, 1, 0],
            "human_adjudication": None,
            "exclusion_reason": None,
        },
        "economics": {
            "input_tokens": 100,
            "cached_input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 130,
            "provider_cost_usd": None,
            "latency_ms": 4321,
            "retry_count": 0,
            "transport_failure_count": 0,
        },
        "privacy": {
            "contains_raw_clinical_text": False,
            "omitted_fields": ["question_text", "answer_text"],
            "omission_rationale": "Healthcare-derived text is retained only in the restricted archive.",
            "review_status": "aggregate-and-identifiers-only",
        },
    }


class PublicItemRecordTests(unittest.TestCase):
    def test_valid_minimized_record_passes_and_hash_is_stable(self):
        record = valid_record()
        records.validate_record(record)
        self.assertEqual(
            records.record_sha256(record), records.record_sha256(copy.deepcopy(record))
        )
        self.assertEqual(len(records.record_sha256(record)), 64)

    def test_rejects_unknown_or_raw_sensitive_fields_at_any_depth(self):
        for path, value in (
            (("patient_fhir_id",), "Patient/123"),
            (("question",), "What is this patient's diagnosis?"),
            (("execution", "prompt"), "raw prompt"),
            (("outcome", "answer_text"), "raw answer"),
            (("grading", "trace"), "hidden reasoning"),
        ):
            with self.subTest(path=path):
                record = valid_record()
                target = record
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(
                    ValueError, "unknown field|forbidden field"
                ):
                    records.validate_record(record)

    def test_rejects_nonopaque_public_identifiers(self):
        for field, value in (
            ("question_id", "q-real-123"),
            ("cluster_id", "Patient/123"),
            ("arm_id", "treatment-graph"),
            ("grading_id", "a6a_question_1"),
        ):
            with self.subTest(field=field):
                record = valid_record()
                record[field] = value
                with self.assertRaisesRegex(ValueError, field):
                    records.validate_record(record)

    def test_rejects_token_reconciliation_and_vote_errors(self):
        record = valid_record()
        record["economics"]["total_tokens"] = 999
        with self.assertRaisesRegex(ValueError, "total_tokens"):
            records.validate_record(record)

        record = valid_record()
        record["grading"]["panel_votes"] = [1, 2, 0]
        with self.assertRaisesRegex(ValueError, "panel_votes"):
            records.validate_record(record)

    def test_rejects_missing_restricted_source_binding(self):
        record = valid_record()
        record["evidence"].pop("restricted_source_sha256")
        with self.assertRaisesRegex(ValueError, "restricted_source_sha256"):
            records.validate_record(record)

    def test_archive_validation_rejects_duplicates_and_mixed_experiments(self):
        first = valid_record()
        second = copy.deepcopy(first)
        second["record_id"] = "pubitem_89abcdef0123456701234567"
        second["arm_id"] = "pubarm_89abcdef01234567"
        records.validate_archive([first, second], expected_experiment_id="a6a-run2")

        with self.assertRaisesRegex(ValueError, "duplicate record_id"):
            records.validate_archive([first, copy.deepcopy(first)])

        second["experiment_id"] = "other-experiment"
        with self.assertRaisesRegex(ValueError, "experiment_id"):
            records.validate_archive([first, second], expected_experiment_id="a6a-run2")

    def test_json_schema_and_normative_validator_have_the_same_fields(self):
        schema = json.loads(
            (REPO / "schemas/public_eval_item_v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["required"]), records.TOP_LEVEL_FIELDS)
        self.assertEqual(set(schema["properties"]), records.TOP_LEVEL_FIELDS)
        for section, expected_fields in records.SECTION_FIELDS.items():
            self.assertEqual(
                set(schema["properties"][section]["required"]), expected_fields
            )
            self.assertEqual(
                set(schema["properties"][section]["properties"]), expected_fields
            )

    def test_cli_reports_exact_archive_hash_and_never_skips_blank_lines(self):
        payload = records.canonical_json(valid_record())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            path.write_bytes(payload)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "public_item_record.py"),
                    "--input",
                    str(path),
                    "--experiment-id",
                    "a6a-run2",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            path.write_bytes(payload + b"\n")
            blank = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "public_item_record.py"),
                    "--input",
                    str(path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["archive_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(receipt["record_count"], 1)
        self.assertNotEqual(blank.returncode, 0)
        self.assertIn("blank JSONL line", blank.stderr)


if __name__ == "__main__":
    unittest.main()
