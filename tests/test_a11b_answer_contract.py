from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import a11b_answer_contract as contract
import codex_harness


SCHEMA = Path("schemas/a11b_answer_v2.schema.json")
TRANSPORT_SCHEMA = Path("schemas/a11b_answer_v2_transport.schema.json")


class A11bAnswerContractTests(unittest.TestCase):
    def test_answered_and_insufficient_states_are_unambiguous(self) -> None:
        answered = {
            "status": "answered",
            "answer": "O-ABC",
            "source_resource_ids": ["Observation/example"],
            "evidence_summary": "Observation/example contains O-ABC.",
            "insufficiency_reason": None,
        }
        insufficient = {
            "status": "insufficient",
            "answer": None,
            "source_resource_ids": ["DiagnosticReport/example"],
            "evidence_summary": "The selected path ends before a result resource.",
            "insufficiency_reason": "The selected path is incomplete.",
        }

        self.assertEqual(contract.validate_answer(answered), answered)
        self.assertEqual(contract.validate_answer(insufficient), insufficient)
        self.assertEqual(contract.answer_status(answered), "answered")
        self.assertEqual(contract.answer_status(insufficient), "insufficient")

    def test_contradictory_and_whitespace_only_states_fail_closed(self) -> None:
        invalid = (
            {
                "status": "insufficient",
                "answer": "O-ABC",
                "source_resource_ids": ["Observation/example"],
                "evidence_summary": "Visible evidence.",
                "insufficiency_reason": "Missing evidence.",
            },
            {
                "status": "answered",
                "answer": "O-ABC",
                "source_resource_ids": ["Observation/example"],
                "evidence_summary": "Visible evidence.",
                "insufficiency_reason": "Missing evidence.",
            },
            {
                "status": "insufficient",
                "answer": None,
                "source_resource_ids": [],
                "evidence_summary": "Visible evidence.",
                "insufficiency_reason": "   ",
            },
        )

        for answer in invalid:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                contract.validate_answer(answer)

    def test_json_schema_matches_the_runtime_contract(self) -> None:
        cases = (
            {
                "status": "answered",
                "answer": "O-ABC",
                "source_resource_ids": ["Observation/example"],
                "evidence_summary": "Observation/example contains O-ABC.",
                "insufficiency_reason": None,
            },
            {
                "status": "insufficient",
                "answer": None,
                "source_resource_ids": [],
                "evidence_summary": "No selected result was available.",
                "insufficiency_reason": "The selected path is incomplete.",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, answer in enumerate(cases):
                path = root / f"answer-{index}.json"
                path.write_text(json.dumps(answer), encoding="utf-8")
                self.assertTrue(codex_harness.answer_matches_schema(path, SCHEMA))

    def test_prompt_instructions_define_the_same_two_states(self) -> None:
        instructions = contract.prompt_instructions()

        self.assertIn('status="answered"', instructions)
        self.assertIn('status="insufficient"', instructions)
        self.assertIn("answer=null", instructions)
        self.assertIn("citations may be empty", instructions)

    def test_transport_schema_is_structural_and_full_validation_stays_offline(
        self,
    ) -> None:
        transport = json.loads(TRANSPORT_SCHEMA.read_text(encoding="utf-8"))
        encoded = json.dumps(transport, sort_keys=True)

        for unsupported in ("oneOf", "pattern", "minItems", "uniqueItems"):
            self.assertNotIn(f'"{unsupported}"', encoded)
        self.assertEqual(
            set(transport["required"]),
            contract.FIELDS,
        )


if __name__ == "__main__":
    unittest.main()
