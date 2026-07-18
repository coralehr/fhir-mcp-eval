from __future__ import annotations

import unittest

import a11b_forensic_sensitivity as sensitivity


class A11bForensicSensitivityTests(unittest.TestCase):
    def test_explicit_insufficiency_rule_is_arm_independent(self) -> None:
        self.assertTrue(
            sensitivity.explicit_insufficiency("Insufficient evidence: no result.")
        )
        self.assertTrue(
            sensitivity.explicit_insufficiency("  Insufficient data to answer.")
        )
        self.assertFalse(
            sensitivity.explicit_insufficiency("Organism ABC was identified.")
        )

    def test_analysis_preserves_answerable_labels_and_regrades_only_unsupported(self) -> None:
        rows = []
        for question_id, answerable in (("q1", True), ("q2", False)):
            for arm in sensitivity.ARMS:
                rows.append(
                    {
                        "question_id": question_id,
                        "patient_cluster_sha256": f"patient-{question_id}",
                        "arm": arm,
                        "answerable": answerable,
                        "strict_correct": answerable,
                        "raw_answer": (
                            "Insufficient data to answer."
                            if not answerable and arm != "t0"
                            else "Organism ABC"
                        ),
                        "raw_insufficiency_reason": (
                            "Selected path incomplete."
                            if not answerable and arm != "t0"
                            else None
                        ),
                    }
                )

        result = sensitivity.analyze(rows, n_boot=100, seed=7)

        self.assertEqual(result["accuracy_by_arm"]["t0"]["correct"], 1)
        self.assertEqual(result["accuracy_by_arm"]["t1"]["correct"], 2)
        self.assertEqual(result["accuracy_by_arm"]["e1"]["correct"], 2)
        self.assertEqual(
            result["raw_unanswerable_behavior"]["t1"]["explicit_insufficiency"],
            1,
        )
        self.assertEqual(
            result["contrasts"]["t1_minus_t0"]["discordant_treatment_only"],
            1,
        )
        self.assertEqual(
            result["contrasts"]["t1_minus_t0"][
                "descriptive_95_patient_cluster_bootstrap"
            ]["alpha"],
            0.05,
        )


if __name__ == "__main__":
    unittest.main()
