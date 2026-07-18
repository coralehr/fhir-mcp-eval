from __future__ import annotations

import json
import unittest

import a11b_successor_panel as panel


class A11bSuccessorPanelTests(unittest.TestCase):
    def test_panel_preserves_summary_and_citations_but_blinds_host(self) -> None:
        item = {
            "arm": "t0",
            "question_id": "synthetic-q1",
            "question": "What organism was found?",
            "gold": {"acceptable_any": ["O-ABC", "Organism ABC"]},
            "status": "answered",
            "answer": "Organism ABC",
            "source_resource_ids": ["Observation/o1"],
            "evidence_summary": "Observation/o1 contains O-ABC.",
            "insufficiency_reason": None,
        }

        queue = panel.parse_queue(
            (json.dumps(item, sort_keys=True) + "\n").encode("utf-8")
        )
        blinded = panel.prepare_blinded_items(queue, {"model": "future-model"})
        prompt = panel.batch_prompt(blinded)

        self.assertIn("Observation/o1 contains O-ABC.", prompt)
        self.assertIn("Observation/o1", prompt)
        self.assertIn("internally", prompt)
        self.assertNotIn("synthetic-q1", prompt)
        self.assertNotIn('"arm"', prompt)

    def test_panel_rejects_insufficient_items(self) -> None:
        item = {
            "arm": "t0",
            "question_id": "synthetic-q1",
            "question": "What organism was found?",
            "gold": {"acceptable_any": ["O-ABC", "Organism ABC"]},
            "status": "insufficient",
            "answer": None,
            "source_resource_ids": [],
            "evidence_summary": "The result is absent.",
            "insufficiency_reason": "No result was present.",
        }

        with self.assertRaisesRegex(ValueError, "not answered"):
            panel.parse_queue(
                (json.dumps(item, sort_keys=True) + "\n").encode("utf-8")
            )


if __name__ == "__main__":
    unittest.main()
