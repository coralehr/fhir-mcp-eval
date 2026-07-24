import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class W12EvidenceReceiptTests(unittest.TestCase):
    def load(self, name: str) -> dict:
        return json.loads((ROOT / "docs" / "results" / name).read_text())

    def test_w1_paired_counts_and_population_reconcile(self) -> None:
        receipt = self.load("W1A_RESULT.json")
        pooled = receipt["pooled"]
        population = receipt["population"]
        self.assertEqual(population["questions"], 409)
        self.assertEqual(
            population["specific_encounter_questions"]
            + population["patient_scope_questions"]
            + population["other_questions"],
            409,
        )
        self.assertEqual(
            pooled["prejoin_correct"] - pooled["comparator_correct"],
            pooled["prejoin_only"] - pooled["comparator_only"],
        )
        self.assertFalse(receipt["grading"]["historical_panel_arm_blind"])
        self.assertFalse(
            receipt["protocol_provenance"]["independent_git_anchor_before_run"]
        )

    def test_w2_paired_table_and_token_ratio_reconcile(self) -> None:
        receipt = self.load("W2A_RESULT.json")
        paired = receipt["paired"]
        self.assertEqual(
            paired["agent_join_only"]
            + paired["prejoin_only"]
            + paired["both_correct"]
            + paired["both_incorrect"],
            176,
        )
        self.assertEqual(
            paired["agent_join_correct"],
            paired["agent_join_only"] + paired["both_correct"],
        )
        self.assertEqual(
            paired["prejoin_correct"],
            paired["prejoin_only"] + paired["both_correct"],
        )
        tokens = receipt["answer_token_receipts"]
        ratio = (
            tokens["agent_join"]["input_tokens"]
            / tokens["prejoin_same_questions"]["input_tokens"]
        )
        self.assertAlmostEqual(tokens["cumulative_input_token_ratio"], ratio, 3)
        self.assertLess(paired["patient_cluster_ci_points"][0], 0)
        self.assertGreater(paired["patient_cluster_ci_points"][1], 0)


if __name__ == "__main__":
    unittest.main()
