from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import a11_grading


def _synthetic_result_inputs():
    question_ids = [f"q{index:03d}" for index in range(120)]
    questions = {}
    gold = {}
    labels = {arm: {} for arm in a11_grading.ARMS}
    for index, question_id in enumerate(question_ids):
        family, depth = a11_grading.REGISTERED_CELLS[index % 8]
        patient_index = index if index < 100 else index - 100
        questions[question_id] = {
            "question_id": question_id,
            "patient_fhir_id": f"patient-{patient_index:03d}",
            "family": family,
            "depth": depth,
        }
        gold[question_id] = {
            "question_id": question_id,
            "answerable": index < 96,
        }
        labels["v"][question_id] = int(index % 3 == 0)
        labels["t"][question_id] = int(index % 2 == 0)
        labels["e"][question_id] = int(index % 5 != 0)
    return question_ids, questions, gold, labels


class A11FinalResultTests(unittest.TestCase):
    def test_final_result_uses_registered_120_and_96_strata_and_100_clusters(self) -> None:
        question_ids, questions, gold, labels = _synthetic_result_inputs()

        result = a11_grading.assemble_result(
            question_ids=question_ids,
            questions=questions,
            gold=gold,
            labels=labels,
        )

        primary = result["contrasts"]["e_minus_t"]
        secondary = result["contrasts"]["t_minus_v"]
        self.assertEqual(primary["n"], 120)
        self.assertEqual(secondary["n"], 96)
        self.assertEqual(primary["patient_cluster_bootstrap"]["n_clusters"], 100)
        self.assertEqual(primary["patient_cluster_bootstrap"]["n_boot"], 10_000)
        self.assertEqual(primary["patient_cluster_bootstrap"]["seed"], 20260715)
        self.assertEqual(primary["orientation"], "treatment_minus_reference")
        self.assertEqual(secondary["orientation"], "treatment_minus_reference")
        self.assertFalse(primary["mcnemar"]["promotion_gate"])
        self.assertEqual(len(result["family_depth_breakdowns"]), 8)
        self.assertTrue(
            all(
                cell["n"] == 15
                for cell in result["family_depth_breakdowns"].values()
            )
        )

    def test_promotion_uses_difference_interval_and_safety_but_not_mcnemar(self) -> None:
        primary = {
            "accuracy_difference": 0.01,
            "patient_cluster_bootstrap": {"ci_low": 0.001, "ci_high": 0.02},
            "mcnemar": {"exact_two_sided_p": 1.0},
        }

        promoted = a11_grading.promotion_assessment(
            primary=primary,
            critical_safety_failures=[],
        )
        self.assertTrue(promoted["promoted"])
        self.assertTrue(promoted["mcnemar_is_report_only"])
        self.assertTrue(promoted["secondary_t_minus_v_is_not_a_gate"])

        at_zero = a11_grading.promotion_assessment(
            primary={
                **primary,
                "patient_cluster_bootstrap": {"ci_low": 0.0, "ci_high": 0.02},
            },
            critical_safety_failures=[],
        )
        self.assertFalse(at_zero["promoted"])

        nonpositive = a11_grading.promotion_assessment(
            primary={**primary, "accuracy_difference": 0.0},
            critical_safety_failures=[],
        )
        self.assertFalse(nonpositive["promoted"])

        unsafe = a11_grading.promotion_assessment(
            primary=primary,
            critical_safety_failures=[{"code": "path_replay_failed"}],
        )
        self.assertFalse(unsafe["promoted"])

    def test_result_and_written_bytes_are_invariant_to_input_order(self) -> None:
        question_ids, questions, gold, labels = _synthetic_result_inputs()
        first = a11_grading.assemble_result(
            question_ids=question_ids,
            questions=questions,
            gold=gold,
            labels=labels,
            mechanism_outcomes={"path_validity": {"passed": 120}},
            input_hashes={"z": "last", "a": "first"},
        )
        reversed_labels = {
            arm: dict(reversed(list(labels[arm].items())))
            for arm in reversed(a11_grading.ARMS)
        }
        second = a11_grading.assemble_result(
            question_ids=list(reversed(question_ids)),
            questions=dict(reversed(list(questions.items()))),
            gold=dict(reversed(list(gold.items()))),
            labels=reversed_labels,
            mechanism_outcomes={"path_validity": {"passed": 120}},
            input_hashes={"a": "first", "z": "last"},
        )

        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "first.json"
            second_path = root / "second.json"
            a11_grading.write_result(first_path, first)
            a11_grading.write_result(second_path, second)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())


if __name__ == "__main__":
    unittest.main()

