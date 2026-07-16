from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
