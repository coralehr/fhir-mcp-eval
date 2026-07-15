import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import a11_answer_harness as harness


def _sha(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _input_row() -> dict[str, str]:
    return {
        "question_id": "a11q-public-test",
        "question": "What synthetic organism was found in the latest culture?",
        "assumption": "All records and identifiers are synthetic and non-PHI.",
    }


def _record(payload: str, *, row: dict[str, str] | None = None) -> dict[str, str]:
    selected = row or _input_row()
    prompt = harness.render_prompt_bytes(selected, payload)
    return {
        "schema_version": harness.PROMPT_RECORD_VERSION,
        "question_id": selected["question_id"],
        "model_payload_json": payload,
        "model_payload_sha256": _sha(payload),
        "model_payload_utf8_bytes": len(payload.encode("utf-8")),
        "prompt_text": prompt.decode("utf-8"),
        "prompt_sha256": _sha(prompt),
    }


class A11AnswerHarnessTests(unittest.TestCase):
    def test_payload_bytes_are_inserted_verbatim(self):
        payload = '{\n  "resources" : [ ],\n  "note": "snowman ☃"\n}'
        prompt = harness.build_verified_prompt(_input_row(), _record(payload))

        self.assertEqual(prompt.count(payload.encode("utf-8")), 1)
        start = prompt.index(payload.encode("utf-8"))
        self.assertEqual(
            prompt[start : start + len(payload.encode("utf-8"))],
            payload.encode("utf-8"),
        )

    def test_prompt_envelope_is_identical_for_every_arm_payload(self):
        row = _input_row()
        left_payload = '{"resources":[]}'
        right_payload = '{"event_groups":[],"answerability_receipt":{"state":"insufficient"}}'
        left = harness.build_verified_prompt(row, _record(left_payload, row=row))
        right = harness.build_verified_prompt(row, _record(right_payload, row=row))

        left_prefix, left_suffix = left.split(left_payload.encode("utf-8"), 1)
        right_prefix, right_suffix = right.split(right_payload.encode("utf-8"), 1)
        self.assertEqual(left_prefix, right_prefix)
        self.assertEqual(left_suffix, right_suffix)
        self.assertNotIn(b"Arm:", left)
        self.assertNotIn(b"patient_fhir_id", left)

    def test_rejects_non_json_non_object_duplicate_keys_and_non_finite_values(self):
        invalid_payloads = (
            "[]",
            '{"resources":[],"resources":[]}',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    harness.build_verified_prompt(_input_row(), _record(payload))

    def test_rejects_recursive_gold_governance_control_and_arm_fields(self):
        forbidden_fields = (
            "gold",
            "reference_answer",
            "failure_mode",
            "patient_fhir_id",
            "principal_id",
            "practice_id",
            "source_version",
            "shared_retrieval_source_sha256",
            "policy_context",
            "authorization",
            "retrieval_receipt",
            "bounds",
            "arm",
            "arm_id",
            "arm_name",
            "treatment_label",
        )
        for field in forbidden_fields:
            payload = json.dumps({"resources": [{"nested": {field: "secret"}}]})
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "forbidden"):
                    harness.build_verified_prompt(_input_row(), _record(payload))

    def test_rejects_input_columns_beyond_question_question_id_and_assumption(self):
        record = _record('{"resources":[]}')
        for field in ("patient_fhir_id", "arm", "gold", "practice_id"):
            row = {**_input_row(), field: "secret"}
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "input row fields"):
                    harness.build_verified_prompt(row, record)

    def test_rejects_arm_labels_even_when_hidden_under_a_generic_key(self):
        for arm_label in ("V", "T", "E", "a11-v", "arm-t", "e-arm"):
            payload = json.dumps({"resources": [], "metadata": {"name": arm_label}})
            with self.subTest(arm_label=arm_label):
                with self.assertRaisesRegex(ValueError, "arm label"):
                    harness.build_verified_prompt(_input_row(), _record(payload))

    def test_allows_generic_clinical_source_field_used_by_event_groups(self):
        payload = json.dumps(
            {
                "event_groups": [
                    {
                        "canonical_event_time": "2100-01-15T13:00:00Z",
                        "canonical_event_time_source": "effectiveDateTime",
                        "root": {"source": "effectiveDateTime"},
                    }
                ]
            }
        )

        prompt = harness.build_verified_prompt(_input_row(), _record(payload))
        self.assertIn(payload.encode("utf-8"), prompt)

    def test_rejects_payload_hash_prompt_hash_and_question_binding_tamper(self):
        row = _input_row()
        valid = _record('{"resources":[]}', row=row)
        tampered = (
            {**valid, "model_payload_sha256": "0" * 64},
            {**valid, "model_payload_utf8_bytes": 1},
            {**valid, "prompt_sha256": "0" * 64},
            {**valid, "question_id": "a11q-another"},
            {
                **valid,
                "prompt_text": valid["prompt_text"].replace(
                    "Frozen clinical packet:", "Clinical packet:"
                ),
                "prompt_sha256": _sha(
                    valid["prompt_text"].replace(
                        "Frozen clinical packet:", "Clinical packet:"
                    )
                ),
            },
        )
        for record in tampered:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):
                    harness.build_verified_prompt(row, record)

    def test_dry_run_emits_controller_compatible_artifacts_and_summary(self):
        row = _input_row()
        payload = '{"resources":[]}'
        record = _record(payload, row=row)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "questions.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            packet_path = root / "payloads.jsonl"
            packet_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            out_dir = root / "out"

            result = harness.main(
                [
                    "--mode",
                    "packet",
                    "--input",
                    str(input_path),
                    "--packet-json",
                    str(packet_path),
                    "--out-dir",
                    str(out_dir),
                    "--schema",
                    str(Path(harness.__file__).with_name("schemas") / "codex_answer.schema.json"),
                    "--question-id",
                    row["question_id"],
                    "--dry-run",
                ]
            )

            self.assertEqual(result, 0)
            question_dir = out_dir / "questions" / row["question_id"]
            self.assertEqual(
                (question_dir / "prompt.txt").read_bytes(),
                harness.render_prompt_bytes(row, payload),
            )
            self.assertTrue((question_dir / "command.json").is_file())
            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["manifest"], manifest)
            self.assertEqual(summary["questions"][0]["status"], "dry_run")
            self.assertEqual(
                summary["questions"][0]["prompt_sha256"],
                _sha(question_dir.joinpath("prompt.txt").read_bytes()),
            )


if __name__ == "__main__":
    unittest.main()
