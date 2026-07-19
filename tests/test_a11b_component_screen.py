from __future__ import annotations

import copy
import unittest

import a11b_component_screen as screen
from a11_evidence_core import canonical_bytes
from a11b_corpus_builder import build_case
from a11b_event_compiler import ARM_T1, plan_question


NONCE_KEY = bytes.fromhex("33" * 32)


def _inputs() -> tuple[dict[str, object], str, dict[str, object]]:
    case = build_case(
        {
            "resourceType": "Bundle",
            "type": "transaction",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Patient",
                        "id": "patient-component-screen",
                        "meta": {"versionId": "1"},
                    }
                }
            ],
        },
        split="development",
        family_index=0,
        occurrence=0,
        nonce_key=NONCE_KEY,
    )
    question = case["question"]["question"]
    return case, question, plan_question(question)


class A11bComponentScreenTests(unittest.TestCase):
    def test_compiles_registered_single_component_and_representation_arms(
        self,
    ) -> None:
        case, question, question_plan = _inputs()
        result = screen.compile_component_screen(
            case["source"],
            question,
            question_plan,
            max_packet_bytes=512_000,
        )

        self.assertEqual(
            set(result["arms"]),
            {
                "t0",
                "temporal_rank_only",
                "selected_marker_only",
                "answerability_receipt_bundle",
                "path_only",
                "group_only",
                "t0_byte_matched_placebo",
            },
        )
        rank_arm = result["arms"]["temporal_rank_only"]
        self.assertIn("temporal_aids", rank_arm)
        self.assertNotIn("selected", canonical_bytes(rank_arm).decode())
        self.assertNotIn("answerability", canonical_bytes(rank_arm).decode())

        source_events = case["compiled"]["arms"][ARM_T1]["temporal_aids"][
            "events"
        ]
        marker_arm = result["arms"]["selected_marker_only"]
        self.assertEqual(
            marker_arm["temporal_aids"]["events"],
            [
                {"root_ref": event["root_ref"], "selected": event["selected"]}
                for event in source_events
            ],
        )
        receipt_arm = result["arms"]["answerability_receipt_bundle"]
        self.assertEqual(
            receipt_arm["temporal_aids"]["answerability_receipt"]["state"],
            "sufficient",
        )
        self.assertNotIn(
            "path_citations", result["arms"]["group_only"]["evidence"]
        )
        self.assertNotIn("event_groups", result["arms"]["path_only"])
        self.assertEqual(
            result["receipt"]["arms"]["t0_byte_matched_placebo"]["bytes"],
            result["receipt"]["arms"]["path_only"]["bytes"],
        )
        self.assertEqual(result["receipt"]["model_calls"], 0)

    def test_recompiles_governed_input_and_rejects_tampering(self) -> None:
        case, question, question_plan = _inputs()
        for mutation in ("source", "plan"):
            source = copy.deepcopy(case["source"])
            plan = copy.deepcopy(question_plan)
            if mutation == "source":
                source["gold"] = {"answer": "LEAK"}
            else:
                plan["question_sha256"] = "0" * 64

            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                screen.compile_component_screen(
                    source,
                    question,
                    plan,
                    max_packet_bytes=512_000,
                )


if __name__ == "__main__":
    unittest.main()
