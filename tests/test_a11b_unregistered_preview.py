from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_unregistered_preview as preview
import codex_harness


class A11bUnregisteredPreviewTests(unittest.TestCase):
    def test_transport_schema_is_structural_and_full_contract_stays_offline(self):
        schema_path = Path("schemas/a11b_answer.schema.json")
        original = schema_path.read_bytes()
        transport = json.loads(preview._transport_schema(original))
        encoded = json.dumps(transport, sort_keys=True)

        for keyword in preview.UNSUPPORTED_TRANSPORT_SCHEMA_KEYS:
            self.assertNotIn(f'"{keyword}"', encoded)
        self.assertEqual(
            transport["required"],
            [
                "answer",
                "source_resource_ids",
                "evidence_summary",
                "insufficiency_reason",
            ],
        )
        self.assertFalse(transport["additionalProperties"])
        self.assertNotIn("oneOf", transport)
        self.assertNotIn("anyOf", transport)

        invalid = {
            "answer": "Substantive answer",
            "source_resource_ids": [],
            "evidence_summary": "Visible evidence.",
            "insufficiency_reason": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            answer_path = Path(directory) / "answer.json"
            answer_path.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertFalse(
                codex_harness.answer_matches_schema(answer_path, schema_path)
            )

    def test_normalization_changes_only_substantive_reason_and_rejects_other_invalidity(self):
        schema_path = Path("schemas/a11b_answer.schema.json")
        valid_except_reason = {
            "answer": "Substantive answer",
            "source_resource_ids": ["Observation/example"],
            "evidence_summary": "Visible evidence.",
            "insufficiency_reason": "The packet did not include another detail.",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answer_path = root / "answer.json"
            normalized_path = root / "normalized-answer.json"
            answer_path.write_text(json.dumps(valid_except_reason), encoding="utf-8")

            receipt = preview._normalize_substantive_answer(
                answer_path=answer_path,
                schema_path=schema_path,
                normalized_path=normalized_path,
            )

            self.assertIsNotNone(receipt)
            normalized = json.loads(normalized_path.read_bytes())
            expected = dict(valid_except_reason)
            expected["insufficiency_reason"] = None
            self.assertEqual(normalized, expected)
            self.assertTrue(
                codex_harness.answer_matches_schema(normalized_path, schema_path)
            )

            invalid = dict(valid_except_reason)
            invalid["source_resource_ids"] = []
            answer_path.write_text(json.dumps(invalid), encoding="utf-8")
            second_path = root / "invalid-normalized-answer.json"
            self.assertIsNone(
                preview._normalize_substantive_answer(
                    answer_path=answer_path,
                    schema_path=schema_path,
                    normalized_path=second_path,
                )
            )
            self.assertFalse(second_path.exists())

    def test_historical_schema_failure_recovers_with_tamper_evident_marker(self):
        registered_schema = Path("schemas/a11b_answer.schema.json").read_bytes()
        transport_schema = preview._transport_schema(registered_schema)
        answer = {
            "answer": "Substantive answer",
            "source_resource_ids": ["Observation/example"],
            "evidence_summary": "Visible evidence.",
            "insufficiency_reason": "A non-answer detail was unavailable.",
        }
        with tempfile.TemporaryDirectory() as directory:
            slot_dir = Path(directory) / "0000"
            attempt_dir = slot_dir / "attempt-1"
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "answer.json").write_text(
                json.dumps(answer), encoding="utf-8"
            )
            (attempt_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
            (attempt_dir / "stderr.log").write_bytes(b"")
            (attempt_dir / "registered-schema.json").write_bytes(registered_schema)
            (attempt_dir / "transport-schema.json").write_bytes(transport_schema)
            receipt = preview._receipt_for_attempt(
                index=0,
                attempt_number=1,
                attempt_dir=attempt_dir,
                outcome="provider_failure",
                error=preview.NORMALIZABLE_SCHEMA_ERROR,
            )
            # The first live preview receipt predates per-schema file receipts.
            receipt.pop("registered-schema.json")
            receipt.pop("transport-schema.json")
            preview._atomic_write(attempt_dir / "receipt.json", receipt)

            with mock.patch.object(
                codex_harness, "enforce_packet_event_integrity"
            ) as integrity:
                recovered = preview._recover_normalized_attempt(
                    slot_dir=slot_dir,
                    attempt_dir=attempt_dir,
                    receipt=receipt,
                    index=0,
                    attempt_number=1,
                    prompt_sha256="a" * 64,
                    registered_schema=registered_schema,
                    transport_schema=transport_schema,
                )

            self.assertTrue(recovered)
            integrity.assert_called_once()
            self.assertTrue(
                preview._accepted_marker_valid(slot_dir, 0, "a" * 64)
            )
            normalized_path = attempt_dir / "normalized-answer.json"
            normalized_path.chmod(0o600)
            normalized_path.write_text("{}\n", encoding="utf-8")
            self.assertFalse(
                preview._accepted_marker_valid(slot_dir, 0, "a" * 64)
            )

    def test_usage_requires_exactly_one_complete_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 2,
                            "output_tokens": 3,
                            "reasoning_output_tokens": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                preview._usage_from_event(path),
                {"input": 10, "cached": 2, "output": 3, "reasoning": 1, "total": 13},
            )
            path.write_text(path.read_text() + path.read_text(), encoding="utf-8")
            self.assertIsNone(preview._usage_from_event(path))
