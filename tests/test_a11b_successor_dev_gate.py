from __future__ import annotations

import unittest

import a11b_successor_dev_gate as gate
from a11_evidence_core import canonical_bytes, sha256


def _assignments() -> list[dict[str, str]]:
    return [
        {
            "question_id": f"q-{index:02d}",
            "patient_cluster_sha256": f"{index:064x}",
        }
        for index in range(64)
    ]


def _outcomes() -> list[dict[str, object]]:
    return [
        {
            **assignment,
            "arm": arm,
            "correct": True,
            "answer_status": "answered",
        }
        for assignment in _assignments()
        for arm in ("t0", "t1", "e1")
    ]


def _manifest(
    assignments: list[dict[str, str]],
    outcomes: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "a11b-successor-development-result-manifest-v1",
        "assignments_sha256": sha256(canonical_bytes(assignments)),
        "outcomes_sha256": sha256(canonical_bytes(outcomes)),
        "question_count": 64,
        "arms": ["t0", "t1", "e1"],
        "accepted_attempts": 192,
        "all_attempts": 192,
        "accepted_token_usage_complete": True,
        "all_attempt_token_usage_complete": True,
    }


class A11bSuccessorDevelopmentGateTests(unittest.TestCase):
    def test_rejects_a_development_probe_with_no_correctness_discordance(self) -> None:
        assignments = _assignments()
        outcomes = _outcomes()
        with self.assertRaisesRegex(ValueError, "primary.*no discordant"):
            gate.compile_gate_receipt(
                assignments=assignments,
                outcomes=outcomes,
                development_result_manifest=_manifest(assignments, outcomes),
            )

    def test_requires_nonzero_discordance_for_both_registered_contrasts(self) -> None:
        outcomes = _outcomes()
        for row in outcomes:
            if row["question_id"] == "q-00" and row["arm"] == "e1":
                row["correct"] = False
            if row["question_id"] == "q-01" and row["arm"] == "t0":
                row["correct"] = False

        assignments = _assignments()
        receipt = gate.compile_gate_receipt(
            assignments=assignments,
            outcomes=outcomes,
            development_result_manifest=_manifest(assignments, outcomes),
        )

        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["question_count"], 64)
        self.assertEqual(receipt["answer_call_count"], 192)
        self.assertEqual(receipt["contrasts"]["primary_e1_minus_t1"]["discordant"], 1)
        self.assertEqual(receipt["contrasts"]["secondary_t1_minus_t0"]["discordant"], 1)
        self.assertNotIn("question_id", receipt)
        self.assertNotIn("outcomes", receipt)

    def test_rejects_primary_discordance_when_secondary_has_none(self) -> None:
        assignments = _assignments()
        outcomes = _outcomes()
        outcomes[2]["correct"] = False

        with self.assertRaisesRegex(ValueError, "secondary.*no discordant"):
            gate.compile_gate_receipt(
                assignments=assignments,
                outcomes=outcomes,
                development_result_manifest=_manifest(assignments, outcomes),
            )

    def test_rejects_missing_duplicate_or_mismatched_patient_outcomes(self) -> None:
        assignments = _assignments()
        missing = _outcomes()[:-1]
        with self.assertRaises(ValueError):
            gate.compile_gate_receipt(
                assignments=assignments,
                outcomes=missing,
                development_result_manifest=_manifest(assignments, missing),
            )

        duplicate = _outcomes()
        duplicate[-1] = dict(duplicate[0])
        with self.assertRaises(ValueError):
            gate.compile_gate_receipt(
                assignments=assignments,
                outcomes=duplicate,
                development_result_manifest=_manifest(assignments, duplicate),
            )

        mismatch = _outcomes()
        mismatch[0]["patient_cluster_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            gate.compile_gate_receipt(
                assignments=assignments,
                outcomes=mismatch,
                development_result_manifest=_manifest(assignments, mismatch),
            )

        duplicate_cluster_assignments = _assignments()
        duplicate_cluster_assignments[-1]["patient_cluster_sha256"] = (
            duplicate_cluster_assignments[0]["patient_cluster_sha256"]
        )
        outcomes = _outcomes()
        with self.assertRaisesRegex(ValueError, "patient-disjoint"):
            gate.compile_gate_receipt(
                assignments=duplicate_cluster_assignments,
                outcomes=outcomes,
                development_result_manifest=_manifest(
                    duplicate_cluster_assignments,
                    outcomes,
                ),
            )

    def test_rejects_outcomes_not_bound_by_the_result_manifest(self) -> None:
        assignments = _assignments()
        outcomes = _outcomes()
        manifest = _manifest(assignments, outcomes)
        manifest["outcomes_sha256"] = "f" * 64

        with self.assertRaisesRegex(ValueError, "manifest binding"):
            gate.compile_gate_receipt(
                assignments=assignments,
                outcomes=outcomes,
                development_result_manifest=manifest,
            )


if __name__ == "__main__":
    unittest.main()
