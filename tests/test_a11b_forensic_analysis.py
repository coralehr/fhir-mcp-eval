from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import a11b_forensic_analysis as forensic
import a11b_grading


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def receipt(payload: bytes) -> dict:
    return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


class A11bForensicAnalysisTests(unittest.TestCase):
    def _raw_fixture(
        self,
        root: Path,
        *,
        leaked: bool = False,
        tool: bool = False,
        extra_message: bool = False,
    ):
        run_id = "a" * 64
        schedule = []
        invocations = []
        attempts = []
        for index in range(forensic.EXPECTED_CALLS):
            arm = forensic.EXPECTED_ARMS[index % 3]
            question_id = f"q-{index // 3:03d}"
            prompt = (
                b'Synthetic prompt with "reference_answer": hidden'
                if leaked and index == 0
                else f"Synthetic prompt {index}".encode()
            )
            schedule.append(
                {
                    "schedule_index": index,
                    "arm": arm,
                    "question_id": question_id,
                    "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
                }
            )
            invocations.append(
                {
                    "schedule_index": index,
                    "prompt_base64": base64.b64encode(prompt).decode(),
                }
            )
            item_type = "function_call" if tool and index == 0 else "agent_message"
            event_rows = [
                    {"type": "thread.started", "thread_id": "synthetic"},
                    {"type": "turn.started"},
                    {"type": "item.completed", "item": {"type": item_type, "text": "synthetic"}},
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 0,
                            "output_tokens": 2,
                            "reasoning_output_tokens": 0,
                        },
                    },
            ]
            if extra_message and index == 0:
                event_rows.insert(
                    3,
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "second"},
                    },
                )
            events = b"\n".join(
                canonical(row).rstrip(b"\n") for row in event_rows
            ) + b"\n"
            files = {
                "answer.json": base64.b64encode(b"{}\n").decode(),
                "events.jsonl": base64.b64encode(events).decode(),
                "stderr.log": base64.b64encode(b"").decode(),
            }
            artifact = canonical({"capture": {"files_base64": files}})
            attempts.append(
                {
                    "descriptor": {"schedule_index": index, "attempt_number": 1},
                    "outcome": "accepted",
                    "token_usage": {
                        "input": 10,
                        "cached": 0,
                        "output": 2,
                        "reasoning": 0,
                        "total": 12,
                        "complete": True,
                    },
                    "artifact_base64": base64.b64encode(artifact).decode(),
                    "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
                    "artifact_bytes": len(artifact),
                }
            )
        controller = {
            "schema_version": "a11-controller-v4",
            "experiment_profile": "a11b-causal-isolation-v2",
            "run_id": run_id,
            "inputs": {"question_count": 384, "answer_calls": 1152},
            "schedule": {"arms": list(forensic.EXPECTED_ARMS), "items": schedule},
        }
        controller_path = root / "controller.json"
        controller_payload = canonical(controller)
        controller_path.write_bytes(controller_payload)
        (root / "controller.sha256").write_text(
            hashlib.sha256(controller_payload).hexdigest() + "\n"
        )
        bundle_path = root / "bundle.json"
        bundle_path.write_bytes(
            canonical(
                {
                    "schema_version": "experiment-executor-service-bundle-v1",
                    "run_id": run_id,
                    "invocations": invocations,
                }
            )
        )
        export_path = root / "executor-export.json"
        export_path.write_bytes(
            canonical(
                {
                    "schema_version": "experiment-run-export-v1",
                    "run_id": run_id,
                    "schedule_length": 1152,
                    "accepted_slots": 1152,
                    "model_calls_reserved": 1152,
                    "model_calls_closed": 1152,
                    "attempts": attempts,
                }
            )
        )
        return controller_path, bundle_path, export_path

    def test_raw_audit_passes_without_retaining_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._raw_fixture(Path(directory))
            report = forensic.audit_raw_execution(
                controller_path=paths[0], bundle_path=paths[1], executor_export_path=paths[2]
            )
            self.assertTrue(report["all_checks_passed"])
            self.assertFalse(report["answer_content_retained"])
            self.assertEqual(report["prompt_count"], 1152)

    def test_raw_audit_detects_gold_leakage_and_tool_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._raw_fixture(Path(directory), leaked=True, tool=True)
            report = forensic.audit_raw_execution(
                controller_path=paths[0], bundle_path=paths[1], executor_export_path=paths[2]
            )
            self.assertFalse(report["all_checks_passed"])
            self.assertEqual(report["leakage_match_counts"]["gold_field"], 1)
            self.assertGreater(report["failure_counts"]["tool_or_nonmessage_events"], 0)

    def test_raw_audit_requires_exactly_one_agent_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._raw_fixture(Path(directory), extra_message=True)
            report = forensic.audit_raw_execution(
                controller_path=paths[0], bundle_path=paths[1], executor_export_path=paths[2]
            )
            self.assertFalse(report["all_checks_passed"])
            self.assertEqual(
                report["failure_counts"][
                    "accepted_slots_without_exactly_one_agent_message"
                ],
                1,
            )

    def test_final_report_replays_promotion_and_requires_raw_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller_path, bundle_path, export_path = self._raw_fixture(root)
            raw = forensic.audit_raw_execution(
                controller_path=controller_path,
                bundle_path=bundle_path,
                executor_export_path=export_path,
            )
            raw_path = root / "raw-audit.json"
            raw_path.write_bytes(canonical(raw))
            behavior = {
                arm: {
                    "unsupported_answers": 0,
                    "citation_failures": 0,
                    "temporal_binding_errors": 0,
                }
                for arm in forensic.EXPECTED_ARMS
            }
            primary = {
                "accuracy_difference": 0.05,
                "patient_cluster_bootstrap": {"ci_low": 0.01},
            }
            secondary = {
                "accuracy_difference": 0.01,
                "patient_cluster_bootstrap": {"ci_low": -0.01},
            }
            safety = a11b_grading.safety_comparisons(behavior)
            promotion = a11b_grading.promotion_assessment(
                primary=primary, secondary=secondary, safety_comparisons=safety
            )
            result = {
                "status": "completed_registered_analysis",
                "question_ids": [f"q-{index:03d}" for index in range(384)],
                "arms": list(forensic.EXPECTED_ARMS),
                "registered_contrasts": ["e1_minus_t1", "t1_minus_t0"],
                "accuracy_by_arm": {
                    arm: {"n": 384, "correct": 300, "accuracy": 300 / 384}
                    for arm in forensic.EXPECTED_ARMS
                },
                "contrasts": {"e1_minus_t1": primary, "t1_minus_t0": secondary},
                "answer_behavior_outcomes": behavior,
                "economics": {
                    "answers": {
                        "attempts_by_arm": raw["attempts_by_arm"],
                        "accepted_token_usage_by_arm": raw[
                            "accepted_token_usage_by_arm"
                        ],
                        "all_attempt_token_usage_by_arm": raw[
                            "all_attempt_token_usage_by_arm"
                        ],
                        "all_attempt_token_economics_reconciled": True,
                    }
                },
                "promotion_assessment": promotion,
            }
            result_root = root / "result"
            result_root.mkdir()
            result_payload = canonical(result)
            (result_root / "result.json").write_bytes(result_payload)
            controller_sha = hashlib.sha256(controller_path.read_bytes()).hexdigest()
            (result_root / "manifest.json").write_bytes(
                canonical(
                    {
                        "schema_version": "a11b-final-result-manifest-v1",
                        "controller_manifest_sha256": controller_sha,
                        "all_checks_passed": True,
                        "artifacts": {"result.json": receipt(result_payload)},
                    }
                )
            )
            report = forensic.analyze_final_result(
                controller_path=controller_path,
                result_root=result_root,
                raw_audit_path=raw_path,
            )
            self.assertEqual(
                report["interpretation"],
                "event_grouping_supported_beyond_identical_aids",
            )
            self.assertTrue(report["no_cheating_checks_passed"])
            raw["all_checks_passed"] = False
            raw_path.write_bytes(canonical(raw))
            with self.assertRaisesRegex(forensic.ForensicError, "raw no-cheating audit"):
                forensic.analyze_final_result(
                    controller_path=controller_path,
                    result_root=result_root,
                    raw_audit_path=raw_path,
                )


if __name__ == "__main__":
    unittest.main()
