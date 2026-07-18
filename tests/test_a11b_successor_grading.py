from __future__ import annotations

import unittest
from unittest import mock

import a11b_successor_grading as grading


def safety_evidence(*, unsupported: int) -> dict:
    return {
        "schema_version": grading.SAFETY_EVIDENCE_VERSION,
        "temporal_metric_version": grading.TEMPORAL_METRIC_VERSION,
        "comparisons": {
            contrast: {
                "unsupported_answers": {
                    "treatment": unsupported,
                    "reference": unsupported,
                    "delta": 0,
                },
                "citation_failures": {
                    "treatment": 0,
                    "reference": 0,
                    "delta": 0,
                },
                "temporal_binding_errors": {
                    "treatment": 0,
                    "reference": 0,
                    "delta": 0,
                },
                "noninferior": True,
            }
            for contrast in ("e1_minus_t1", "t1_minus_t0")
        },
    }


def answer_behavior(*, unsupported: int) -> dict:
    return {
        arm: {
            "unsupported_answers": unsupported,
            "citation_failures": 0,
            "temporal_binding_errors": 0,
        }
        for arm in ("t0", "t1", "e1")
    }


class A11bSuccessorGradingTests(unittest.TestCase):
    def test_partition_uses_status_and_preserves_full_panel_answer(self) -> None:
        question = {"question_id": "q1", "question": "What was found?"}
        unanswerable = {
            "question_id": "q1",
            "answerable": False,
            "reference_answer": None,
        }
        answerable = {
            "question_id": "q1",
            "answerable": True,
            "reference_answer": {"code": "O-ABC", "display": "Organism ABC"},
        }
        insufficient = {
            "status": "insufficient",
            "answer": None,
            "source_resource_ids": ["DiagnosticReport/r1"],
            "evidence_summary": "The selected path ends before a result resource.",
            "insufficiency_reason": "The selected path is incomplete.",
        }
        answered = {
            "status": "answered",
            "answer": "Organism ABC",
            "source_resource_ids": ["Observation/o1"],
            "evidence_summary": "Observation/o1 contains code O-ABC.",
            "insufficiency_reason": None,
        }

        self.assertEqual(
            grading.deterministic_partition(
                question=question,
                gold=unanswerable,
                answer=insufficient,
            ),
            (1, None),
        )
        verdict, panel = grading.deterministic_partition(
            question=question,
            gold=answerable,
            answer=answered,
        )
        self.assertIsNone(verdict)
        self.assertEqual(panel["status"], "answered")
        self.assertEqual(panel["evidence_summary"], answered["evidence_summary"])
        self.assertEqual(panel["source_resource_ids"], ["Observation/o1"])

    def test_unsafe_tie_cannot_promote(self) -> None:
        favorable = {
            "accuracy_difference": 0.05,
            "patient_cluster_bootstrap": {"ci_low": 0.01},
        }

        assessment = grading.promotion_assessment(
            primary=favorable,
            secondary=favorable,
            safety_evidence=safety_evidence(unsupported=96),
        )

        self.assertEqual(assessment["decision"], "do_not_promote")
        self.assertFalse(
            assessment["e1"]["gates"]["zero_critical_safety_failures"]
        )

    def test_result_assembly_replaces_the_historical_safety_gate(self) -> None:
        favorable = {
            "accuracy_difference": 0.05,
            "patient_cluster_bootstrap": {"ci_low": 0.01},
        }
        historical = {
            "analysis_version": "historical",
            "status": "completed_registered_analysis",
            "contrasts": {
                "e1_minus_t1": favorable,
                "t1_minus_t0": favorable,
            },
            "promotion_assessment": {"decision": "promote_e1"},
        }
        with mock.patch.object(
            grading.legacy,
            "assemble_result",
            return_value=historical,
        ), mock.patch.object(grading.legacy, "canonical_json_bytes"):
            result = grading.assemble_result(
                safety_evidence=safety_evidence(unsupported=96),
                answer_behavior_outcomes=answer_behavior(unsupported=96),
            )

        self.assertEqual(result["status"], "completed_unsealed_successor_analysis")
        self.assertEqual(
            result["promotion_assessment"]["decision"],
            "do_not_promote",
        )

    def test_temporal_proxy_version_fails_closed(self) -> None:
        value = safety_evidence(unsupported=0)
        value["temporal_metric_version"] = "selected_path_citation_proxy-v1"

        with self.assertRaisesRegex(ValueError, "temporal safety metric"):
            grading.validate_safety_evidence(value)


if __name__ == "__main__":
    unittest.main()
