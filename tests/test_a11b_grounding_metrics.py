from __future__ import annotations

import copy
import unittest

import a11b_grounding_metrics as grounding
from a11_evidence_core import canonical_bytes, sha256


def _answered(*sources: str, value: str = "Alpha finding") -> dict[str, object]:
    return {
        "status": "answered",
        "answer": value,
        "source_resource_ids": list(sources),
        "evidence_summary": "The cited path supports this answer.",
        "insufficiency_reason": None,
    }


def _insufficient(*sources: str) -> dict[str, object]:
    return {
        "status": "insufficient",
        "answer": None,
        "source_resource_ids": list(sources),
        "evidence_summary": "The required evidence path is unavailable.",
        "insufficiency_reason": "Required evidence is unavailable.",
    }


def _packet_row(
    question_id: str, arm: str, *resource_refs: str
) -> dict[str, object]:
    packet = {
        "schema_version": "a11b-component-screen-v1",
        "evidence": {
            "resources": [
                {
                    "resourceType": resource_ref.split("/", 1)[0],
                    "id": resource_ref.split("/", 1)[1],
                }
                for resource_ref in resource_refs
            ],
            "path_citations": [],
        },
    }
    return {
        "question_id": question_id,
        "arm": arm,
        "packet": packet,
    }


def _receipts(*packet_rows: dict[str, object]) -> list[dict[str, str]]:
    return [
        {
            "question_id": str(row["question_id"]),
            "arm": str(row["arm"]),
            "packet_sha256": sha256(canonical_bytes(row["packet"])),
        }
        for row in packet_rows
    ]


class A11bGroundingMetricsTests(unittest.TestCase):
    def test_scores_terminal_path_answerability_and_joint_support(self) -> None:
        gold = [
            {
                "question_id": "q-answerable",
                "answerable": True,
                "reference_answer": {"code": "123", "display": "Alpha finding"},
                "selected_terminal_resource_ref": "Observation/terminal",
                "selected_path_refs": [
                    "Observation/root",
                    "Observation/intermediate",
                    "Observation/terminal",
                ],
            },
            {
                "question_id": "q-insufficient",
                "answerable": False,
                "reference_answer": None,
                "selected_terminal_resource_ref": None,
                "selected_path_refs": [],
            },
        ]
        answers = [
            {
                "question_id": "q-answerable",
                "arm": "t0",
                "answer": _answered("Observation/terminal"),
            },
            {
                "question_id": "q-insufficient",
                "arm": "t0",
                "answer": _insufficient("Observation/root"),
            },
        ]
        packets = [
            _packet_row(
                row["question_id"],
                "t0",
                "Observation/root",
                "Observation/intermediate",
                "Observation/terminal",
            )
            for row in gold
        ]

        result = grounding.compile_grounding_report(
            gold_rows=gold,
            accepted_answers=answers,
            packet_rows=packets,
            registered_packet_receipts=_receipts(*packets),
            registered_question_ids=[row["question_id"] for row in gold],
            registered_arms=["t0"],
        )

        answerable = result["outcomes"][0]
        self.assertTrue(answerable["correct"])
        self.assertTrue(answerable["answerability_state_correct"])
        self.assertTrue(answerable["selected_terminal_hit"])
        self.assertTrue(answerable["any_selected_path_ref_hit"])
        self.assertFalse(answerable["full_selected_path_coverage"])
        self.assertEqual(
            answerable["citation_precision"], {"numerator": 1, "denominator": 1}
        )
        self.assertEqual(
            answerable["citation_recall"], {"numerator": 1, "denominator": 3}
        )
        self.assertTrue(answerable["correct_and_citation_supported"])
        self.assertEqual(
            result["by_arm"]["t0"]["correct_and_citation_supported"],
            {"numerator": 1, "denominator": 1},
        )
        self.assertEqual(
            result["by_arm"]["t0"]["answerability_state_correct"],
            {"numerator": 2, "denominator": 2},
        )
        insufficient = result["outcomes"][1]
        self.assertIsNone(insufficient["citation_precision"])
        self.assertIsNone(insufficient["citation_recall"])
        self.assertEqual(
            result["by_arm"]["t0"]["citation_precision"],
            {"numerator": 1, "denominator": 1},
        )
        self.assertEqual(result["model_calls"], 0)

    def test_correct_guess_without_terminal_support_is_reported_separately(
        self,
    ) -> None:
        gold = [
            {
                "question_id": "q-guess",
                "answerable": True,
                "reference_answer": {"code": "123", "display": "Alpha finding"},
                "selected_terminal_resource_ref": "Observation/terminal",
                "selected_path_refs": [
                    "Observation/root",
                    "Observation/terminal",
                ],
            }
        ]
        packets = [
            _packet_row(
                "q-guess",
                "t0",
                "Observation/root",
                "Observation/terminal",
            )
        ]
        result = grounding.compile_grounding_report(
            gold_rows=gold,
            accepted_answers=[
                {
                    "question_id": "q-guess",
                    "arm": "t0",
                    "answer": _answered("Observation/root"),
                }
            ],
            packet_rows=packets,
            registered_packet_receipts=_receipts(*packets),
            registered_question_ids=["q-guess"],
            registered_arms=["t0"],
        )

        self.assertTrue(result["outcomes"][0]["correct"])
        self.assertFalse(result["outcomes"][0]["citation_supported"])
        self.assertTrue(result["outcomes"][0]["unsupported_correct"])
        self.assertTrue(result["outcomes"][0]["any_selected_path_ref_hit"])
        self.assertFalse(result["outcomes"][0]["full_selected_path_coverage"])
        self.assertEqual(
            result["by_arm"]["t0"]["unsupported_correct"],
            {"numerator": 1, "denominator": 1},
        )

    def test_requires_registered_coverage_and_packet_bound_visibility(self) -> None:
        gold = [
            {
                "question_id": "q-bound",
                "answerable": True,
                "reference_answer": {"code": "123", "display": "Alpha finding"},
                "selected_terminal_resource_ref": "Observation/terminal",
                "selected_path_refs": ["Observation/terminal"],
            }
        ]
        packet = _packet_row(
            "q-bound", "t0", "Observation/root", "Observation/terminal"
        )
        answer = {
            "question_id": "q-bound",
            "arm": "t0",
            "answer": _answered("Observation/hidden"),
        }

        result = grounding.compile_grounding_report(
            gold_rows=gold,
            accepted_answers=[answer],
            packet_rows=[packet],
            registered_packet_receipts=_receipts(packet),
            registered_question_ids=["q-bound"],
            registered_arms=["t0"],
        )
        self.assertEqual(result["outcomes"][0]["invalid_citation_count"], 1)
        self.assertFalse(result["outcomes"][0]["citation_supported"])

        with self.assertRaises(ValueError):
            grounding.compile_grounding_report(
                gold_rows=gold,
                accepted_answers=[answer],
                packet_rows=[packet],
                registered_packet_receipts=_receipts(packet),
                registered_question_ids=["q-bound"],
                registered_arms=["t0", "path_only"],
            )

        registered_receipts = _receipts(packet)
        forged = copy.deepcopy(packet)
        forged["packet"]["evidence"]["resources"].append(
            {"resourceType": "Observation", "id": "hidden"}
        )
        with self.assertRaises(ValueError):
            grounding.compile_grounding_report(
                gold_rows=gold,
                accepted_answers=[answer],
                packet_rows=[forged],
                registered_packet_receipts=registered_receipts,
                registered_question_ids=["q-bound"],
                registered_arms=["t0"],
            )


if __name__ == "__main__":
    unittest.main()
