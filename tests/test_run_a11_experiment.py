from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run_a11_experiment as controller


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class A11ControllerTests(unittest.TestCase):
    def test_registered_execution_and_analysis_are_frozen(self) -> None:
        self.assertEqual(controller.REGISTERED_MODEL, "gpt-5.6-sol")
        self.assertEqual(controller.REGISTERED_REASONING_EFFORT, "high")
        self.assertEqual(controller.REGISTERED_TIMEOUT_SECONDS, 600)
        self.assertEqual(controller.REGISTERED_MAX_ATTEMPTS, 3)
        self.assertEqual(controller.ARMS, ("v", "t", "e"))
        self.assertEqual(
            controller.REGISTERED_ANALYSIS_ORDER[:3],
            (
                "hard_failures",
                "primary_e_minus_t_all_efficacy",
                "secondary_t_minus_v_answerable",
            ),
        )

    def test_prompt_records_are_exact_blind_and_arm_envelope_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {
                    "question_id": "a11q-one",
                    "question": "What synthetic organism was found?",
                    "assumption": "Synthetic non-PHI data.",
                },
                {
                    "question_id": "a11q-two",
                    "question": "What synthetic specimen was used?",
                    "assumption": "Synthetic non-PHI data.",
                },
            ]
            with (root / "answer_input.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            payloads = {
                "v": '{\n  "resources": []\n}',
                "t": '{"resources":[],"path_citations":[]}',
                "e": '{"event_groups":[],"answerability_receipt":{"state":"insufficient"}}',
            }
            for arm, payload in payloads.items():
                encoded = payload.encode()
                _write_jsonl(
                    root / f"{arm}_packets.jsonl",
                    [
                        {
                            "question_id": row["question_id"],
                            "model_payload_json": payload,
                            "model_payload_sha256": controller._sha256_bytes(encoded),
                            "model_payload_utf8_bytes": len(encoded),
                        }
                        for row in rows
                    ],
                )

            records, prompt_index, question_ids = controller.build_prompt_records(
                answer_inputs_dir=root
            )

            self.assertEqual(question_ids, ("a11q-one", "a11q-two"))
            self.assertEqual(len(prompt_index), 6)
            self.assertEqual(set(records), {"v", "t", "e"})
            for arm in controller.ARMS:
                decoded = [json.loads(line) for line in records[arm].splitlines()]
                self.assertEqual(len(decoded), 2)
                for record in decoded:
                    prompt = record["prompt_text"].encode()
                    self.assertEqual(
                        record["prompt_sha256"], controller._sha256_bytes(prompt)
                    )
                    self.assertNotIn(b"patient_fhir_id", prompt)
                    self.assertNotIn(b"Arm:", prompt)
                    self.assertIn(
                        record["model_payload_json"].encode(),
                        prompt,
                    )

    def test_strict_usage_requires_one_integer_reconciled_completed_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 3,
                            "output_tokens": 4,
                            "reasoning_output_tokens": 2,
                        },
                    }
                )
                + "\n"
            )
            receipt = controller.strict_event_usage(path)
            self.assertEqual(receipt["total_tokens"], 14)
            self.assertEqual(
                receipt["total_tokens_source"], "derived_input_plus_output"
            )
            self.assertTrue(receipt["cached_input_tokens_complete"])

            path.write_text(path.read_text() + path.read_text())
            with self.assertRaisesRegex(ValueError, "exactly one"):
                controller.strict_event_usage(path)

            path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "total_tokens": 15,
                        },
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(ValueError, "reconcile"):
                controller.strict_event_usage(path)

    def test_only_exact_answerless_provider_failure_is_retryable(self) -> None:
        provider_audit = {
            "contaminated": True,
            "event_log_exists": True,
            "findings": [],
            "parse_error_lines": [],
            "integrity_errors": ["turn_completed_missing"],
            "event_count": 4,
            "turn_completed_count": 0,
            "thread_started_count": 1,
            "turn_started_count": 1,
            "error_event_count": 1,
            "turn_failed_count": 1,
            "item_event_count": 0,
            "event_type_sequence": [
                "thread_started",
                "turn_started",
                "error",
                "turn_failed",
            ],
            "utf8_valid": True,
            "terminal_newline": True,
            "provider_failure_shape": True,
        }
        receipt = {
            "status": "invalid",
            "harness_exit_code": 1,
            "answer_sha256": None,
            "event_integrity": provider_audit,
        }
        self.assertTrue(controller.is_a11_retryable_provider_failure(receipt))
        self.assertFalse(
            controller.is_a11_retryable_provider_failure(
                {
                    **receipt,
                    "event_integrity": {
                        **provider_audit,
                        "contaminated": False,
                        "integrity_errors": [],
                        "turn_completed_count": 1,
                    },
                }
            )
        )
        self.assertFalse(
            controller.is_a11_retryable_provider_failure(
                {**receipt, "answer_sha256": "a" * 64}
            )
        )

    def test_status_and_live_refuse_to_create_a_missing_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "manifest.json"
            for mode in ("--status", "--live"):
                with self.subTest(mode=mode):
                    with self.assertRaisesRegex(
                        SystemExit, "not sealed"
                    ):
                        controller.main(
                            [mode, "--controller-manifest", str(missing)]
                        )

    def test_output_directories_must_be_distinct_and_nonnested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "overlap"):
                controller._assert_distinct_outputs({"v": root, "t": root})
            with self.assertRaisesRegex(ValueError, "nested"):
                controller._assert_distinct_outputs(
                    {"v": root, "t": root / "nested"}
                )

    def test_unreconciled_all_attempt_economics_blocks_grading(self) -> None:
        complete = {
            arm: {
                "accepted_complete": True,
                "all_attempt_complete": True,
            }
            for arm in controller.ARMS
        }
        controller.require_reconciled_answer_economics(
            {
                "all_attempt_token_economics_reconciled": True,
                "token_receipt_completeness_by_arm": complete,
            }
        )
        incomplete = {arm: dict(row) for arm, row in complete.items()}
        incomplete["e"]["all_attempt_complete"] = False
        with self.assertRaisesRegex(ValueError, "not fully reconciled"):
            controller.require_reconciled_answer_economics(
                {
                    "all_attempt_token_economics_reconciled": False,
                    "token_receipt_completeness_by_arm": incomplete,
                }
            )

    def test_panel_economics_reconciliation_requires_core_token_receipts(self) -> None:
        empty = {
            "accepted": {"calls": 0, "tokens": {}, "completeness": {}},
            "all_attempts": {"calls": 0, "tokens": {}, "completeness": {}},
        }
        self.assertTrue(controller._panel_economics_reconciled(empty))
        complete = {
            scope: {
                "calls": 1,
                "tokens": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                },
                "completeness": {
                    "input_tokens": True,
                    "output_tokens": True,
                    "total_tokens": True,
                },
            }
            for scope in ("accepted", "all_attempts")
        }
        self.assertTrue(controller._panel_economics_reconciled(complete))
        complete["all_attempts"]["completeness"]["total_tokens"] = False
        self.assertFalse(controller._panel_economics_reconciled(complete))

    def test_zero_panel_finalizer_records_panel_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grading = root / "grading"
            grading.mkdir()
            (grading / "manifest.json").write_text("{}\n", encoding="utf-8")
            gold_path = root / "gold.jsonl"
            question_path = root / "questions.jsonl"
            gold_path.write_text("{}\n", encoding="utf-8")
            question_path.write_text("{}\n", encoding="utf-8")
            result_dir = root / "result"
            bundle = SimpleNamespace(
                manifest_sha256="a" * 64,
                question_ids=("q1",),
                arms=(),
                manifest={
                    "outputs": {
                        "grading": str(grading),
                        "panel": str(root / "panel"),
                        "result": str(result_dir),
                    },
                    "dataset": {"manifest_sha256": "b" * 64},
                    "answer_inputs": {"manifest_sha256": "c" * 64},
                    "snapshots": {
                        "dataset_gold_jsonl": {"snapshot_path": str(gold_path)},
                        "dataset_questions_jsonl": {
                            "snapshot_path": str(question_path)
                        },
                    },
                },
            )
            progress = {
                "all_attempt_token_economics_reconciled": True,
                "token_receipt_completeness_by_arm": {
                    arm: {
                        "accepted_complete": True,
                        "all_attempt_complete": True,
                    }
                    for arm in controller.ARMS
                },
            }
            grading_manifest = {"answer_economics": {}}
            assembled = {
                "status": "completed_registered_analysis",
                "promotion_assessment": {"promoted": False},
            }

            def fake_assemble(**kwargs):
                return {**assembled, "input_hashes": kwargs["input_hashes"]}

            with (
                mock.patch.object(controller, "build_completion_coverage", return_value={}),
                mock.patch.object(controller, "a11_progress", return_value=progress),
                mock.patch.object(
                    controller,
                    "_verified_grading_artifacts",
                    return_value=(grading_manifest, {}, []),
                ),
                mock.patch.object(
                    controller,
                    "_verified_panel_verdicts",
                    return_value=(
                        {},
                        {
                            "accepted": {"calls": 0},
                            "all_attempts": {"calls": 0},
                        },
                    ),
                ),
                mock.patch("a11_grading.load_gold_after_completion", return_value={"q1": {}}),
                mock.patch("a11_grading.final_labels", return_value={arm: {"q1": 0} for arm in controller.ARMS}),
                mock.patch("a11_grading.assemble_result", side_effect=fake_assemble),
                mock.patch.object(controller, "_sealed_payloads", return_value={}),
                mock.patch.object(controller, "_mechanism_outcomes", return_value={}),
                mock.patch.object(controller, "_answer_behavior_outcomes", return_value={}),
                mock.patch.object(controller, "_compilation_economics", return_value={}),
            ):
                final = controller.finalize_result(bundle)
            self.assertEqual(final["status"], "completed_registered_analysis")
            written = json.loads((result_dir / "result.json").read_text())
            self.assertEqual(
                written["input_hashes"]["panel_disposition"],
                "panel_not_required_empty_queue",
            )
            self.assertIsNone(
                written["input_hashes"]["panel_verdict_manifest_sha256"]
            )


if __name__ == "__main__":
    unittest.main()
