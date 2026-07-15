from __future__ import annotations

import unittest
from pathlib import Path

import a11_grading


SHA = "a" * 64


def _coverage(*, omit_last: bool = False) -> dict:
    question_ids = [f"q{index:03d}" for index in range(120)]
    receipts = []
    for question_id in question_ids:
        for arm in a11_grading.ARMS:
            receipts.append(
                {
                    "kind": a11_grading.COMPLETION_KIND,
                    "schema_version": a11_grading.COMPLETION_SCHEMA_VERSION,
                    "controller_manifest_sha256": SHA,
                    "arm": arm,
                    "question_id": question_id,
                    "status": "answered",
                    "attempt_number": 1,
                    "answer_sha256": SHA,
                    "event_log_sha256": SHA,
                    "prompt_sha256": SHA,
                    "stderr_log_sha256": SHA,
                    "packet_sha256": SHA,
                    "schema_sha256": SHA,
                }
            )
    if omit_last:
        receipts.pop()
    return {
        "schema_version": a11_grading.COMPLETION_COVERAGE_VERSION,
        "controller_manifest_sha256": SHA,
        "question_ids": question_ids,
        "arms": list(a11_grading.ARMS),
        "receipts": receipts,
    }


class A11GradingTests(unittest.TestCase):
    def test_registered_analysis_config_pins_the_pre_answer_contract(self) -> None:
        config = a11_grading.registered_analysis_config(
            codex_bin="/opt/codex",
            codex_version="codex 1.2.3",
            codex_binary_sha256=SHA,
            answer_schema_sha256="b" * 64,
            panel_source_sha256="c" * 64,
            grading_source_sha256="d" * 64,
        )

        self.assertEqual(
            config["dataset_manifest_sha256"],
            a11_grading.REGISTERED_DATASET_MANIFEST_SHA256,
        )
        self.assertEqual(
            config["analysis_order"],
            list(a11_grading.REGISTERED_ANALYSIS_ORDER),
        )
        self.assertEqual(config["bootstrap"]["replicates"], 10_000)
        self.assertEqual(config["bootstrap"]["seed"], 20260715)
        self.assertTrue(config["promotion"]["mcnemar_is_report_only"])
        self.assertTrue(
            config["promotion"]["secondary_t_minus_v_is_not_a_gate"]
        )
        self.assertEqual(config["panel"]["votes"], 3)
        self.assertEqual(config["panel"]["batch_size"], 20)
        with self.assertRaisesRegex(ValueError, "sha256"):
            a11_grading.registered_analysis_config(
                codex_bin="/opt/codex",
                codex_version="codex 1.2.3",
                codex_binary_sha256="changed",
                answer_schema_sha256="b" * 64,
                panel_source_sha256="c" * 64,
                grading_source_sha256="d" * 64,
            )

    def test_deterministic_partition_only_scores_structured_abstention(self) -> None:
        question = {"question_id": "q1", "question": "What was found?"}
        answer = {"answer": "Insufficient evidence.", "insufficiency_reason": "missing"}
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

        self.assertEqual(
            a11_grading.deterministic_partition(
                question=question,
                gold=unanswerable,
                answer=answer,
            ),
            (1, None),
        )
        self.assertEqual(
            a11_grading.deterministic_partition(
                question=question,
                gold=answerable,
                answer=answer,
            ),
            (0, None),
        )
        self.assertEqual(
            a11_grading.deterministic_partition(
                question=question,
                gold=unanswerable,
                answer={
                    "answer": "Unsupported substantive claim",
                    "insufficiency_reason": None,
                },
            ),
            (0, None),
        )

        verdict, panel = a11_grading.deterministic_partition(
            question=question,
            gold=answerable,
            answer={"answer": "Organism ABC", "insufficiency_reason": None},
        )
        self.assertIsNone(verdict)
        self.assertEqual(
            panel["gold"],
            {"acceptable_any": ["O-ABC", "Organism ABC"]},
        )

    def test_gold_loader_is_not_called_before_exact_360_completion_proof(self) -> None:
        calls = 0

        def load_gold():
            nonlocal calls
            calls += 1
            return []

        with self.assertRaisesRegex(ValueError, "360"):
            a11_grading.load_gold_after_completion(
                _coverage(omit_last=True),
                gold_loader=load_gold,
                receipt_validator=lambda _receipt: True,
            )
        self.assertEqual(calls, 0)

        with self.assertRaisesRegex(ValueError, "artifact validator"):
            a11_grading.load_gold_after_completion(
                _coverage(),
                gold_loader=load_gold,
            )
        self.assertEqual(calls, 0)

        changed = _coverage()
        changed["receipts"][-1]["controller_manifest_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "identity/status"):
            a11_grading.load_gold_after_completion(
                changed,
                gold_loader=load_gold,
                receipt_validator=lambda _receipt: True,
            )
        self.assertEqual(calls, 0)

    def test_valid_completion_proof_invokes_gold_loader_after_all_receipts(self) -> None:
        coverage = _coverage()
        validated_receipts = []
        loader_observation = []

        def validate_receipt(receipt):
            validated_receipts.append((receipt["arm"], receipt["question_id"]))
            return True

        def load_gold():
            loader_observation.append(len(validated_receipts))
            return [
                {"question_id": question_id, "answerable": True}
                for question_id in coverage["question_ids"]
            ]

        gold = a11_grading.load_gold_after_completion(
            coverage,
            gold_loader=load_gold,
            receipt_validator=validate_receipt,
        )

        self.assertEqual(loader_observation, [360])
        self.assertEqual(list(gold), coverage["question_ids"])

    def test_final_labels_requires_one_and_only_one_label_source(self) -> None:
        question_ids = ["q1", "q2"]
        deterministic = {
            "v": {"q1": 1},
            "t": {"q1": 0},
            "e": {"q1": 1},
        }
        queue = [
            {"arm": arm, "question_id": "q2"}
            for arm in a11_grading.ARMS
        ]
        panel = {f"{arm}|q2": 1 for arm in a11_grading.ARMS}

        labels = a11_grading.final_labels(
            question_ids=question_ids,
            deterministic=deterministic,
            panel_queue=queue,
            panel_verdicts=panel,
        )

        self.assertEqual(labels["t"], {"q1": 0, "q2": 1})
        deterministic["v"]["q2"] = 0
        with self.assertRaisesRegex(ValueError, "exactly one"):
            a11_grading.final_labels(
                question_ids=question_ids,
                deterministic=deterministic,
                panel_queue=queue,
                panel_verdicts=panel,
            )


if __name__ == "__main__":
    unittest.main()
