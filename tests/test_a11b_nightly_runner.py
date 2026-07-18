from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import a11b_nightly_runner as runner
import run_lock


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def status(self, *, run_id: str) -> dict[str, object]:
        position = min(self.calls, 1)
        return {
            "witness": {
                "state": "complete" if position == 1 else "active",
                "schedule_position": 1152 if position == 1 else 0,
                "head": "b" * 64 if position == 0 else "c" * 64,
                "model_calls_reserved": position,
                "model_calls_closed": position,
            }
        }

    def execute_next(self, *, run_id: str, expected_head: str) -> object:
        self.calls += 1
        return SimpleNamespace(outcome="accepted")


class FailingExecutor(FakeExecutor):
    def execute_next(self, *, run_id: str, expected_head: str) -> object:
        raise RuntimeError("simulated executor failure")


class A11bNightlyRunnerTests(unittest.TestCase):
    def test_runner_carries_answers_through_registered_postprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = {
                "experiment_profile": "a11b-causal-isolation-v2",
                "run_id": "a" * 64,
                "inputs": {"answer_calls": 1152},
                "outputs": {},
            }
            executor = FakeExecutor()
            final = {"promotion": {"decision": "do_not_promote"}}
            (root / ".nightly-status.pending").write_text("stale", encoding="utf-8")
            with mock.patch.object(runner, "BUNDLE_ROOT", root), mock.patch.object(
                runner, "AUDIT_ROOT", root / "audit-input"
            ), mock.patch.object(
                runner, "STATUS_PATH", root / "nightly-status.json"
            ), mock.patch.object(
                runner, "LOCK_PATH", root / "nightly-runner.lock"
            ), mock.patch.object(
                runner.a11b_postprocess,
                "_load_controller",
                return_value=(controller, "d" * 64),
            ), mock.patch.object(
                runner.a11b_postprocess, "_verify_installed_postprocess_sources"
            ), mock.patch.object(
                runner.service,
                "load_sealed_service",
                return_value=SimpleNamespace(_executor=executor),
            ), mock.patch.object(
                runner.a11b_postprocess, "run_all", return_value=final
            ) as postprocess, mock.patch.object(
                runner,
                "_await_launch_ack",
                side_effect=lambda **_kwargs: self.assertEqual(executor.calls, 0),
            ) as await_launch_ack:
                runner.run()

            self.assertEqual(executor.calls, 1)
            status = json.loads((root / "nightly-status.json").read_bytes())
            self.assertEqual(status["stage"], "complete")
            self.assertEqual(status["promotion"], final["promotion"])
            postprocess.assert_called_once_with(
                bundle_root=root,
                audit_root=root / "audit-input",
                trusted_executor=executor,
            )
            await_launch_ack.assert_called_once()

    def test_launch_ack_must_bind_the_exact_zero_call_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root = root / "code"
            code_root.mkdir()
            status = {
                "schema_version": "a11b-nightly-status-v1",
                "run_id": "a" * 64,
                "stage": "answers",
                "state": "active",
                "schedule_position": 0,
                "schedule_length": 192,
                "model_calls_reserved": 0,
                "model_calls_closed": 0,
                "updated_at": "2026-07-18T00:00:00Z",
            }
            status_payload = runner.service.canonical_json_line(status)
            acknowledgement = {
                "schema_version": "a11b-launch-ack-v1",
                "run_id": "a" * 64,
                "controller_sha256": "b" * 64,
                "schedule_length": 192,
                "ready_status_sha256": hashlib.sha256(status_payload).hexdigest(),
            }
            ack_path = code_root / "launch-ack.json"
            ack_path.write_bytes(runner.service.canonical_json_line(acknowledgement))
            ack_path.chmod(0o444)
            confirmation_path = root / "launch-confirmation.json"
            confirmation = runner.launch_protocol.confirmation(
                run_id="a" * 64,
                controller_sha256="b" * 64,
                schedule_length=192,
                acknowledgement_sha256=runner.launch_protocol.sha256(
                    runner.launch_protocol.canonical_json_line(acknowledgement)
                ),
            )
            commit_path = code_root / "launch-commit.json"
            commit_path.write_bytes(
                runner.launch_protocol.canonical_json_line(
                    runner.launch_protocol.launch_commit(
                        run_id="a" * 64,
                        controller_sha256="b" * 64,
                        schedule_length=192,
                        confirmation_sha256=runner.launch_protocol.sha256(
                            runner.launch_protocol.canonical_json_line(confirmation)
                        ),
                    )
                )
            )
            commit_path.chmod(0o444)

            with mock.patch.object(runner, "LAUNCH_ACK_PATH", ack_path), mock.patch.object(
                runner, "LAUNCH_CONFIRMATION_PATH", confirmation_path
            ), mock.patch.object(
                runner, "LAUNCH_COMMIT_PATH", commit_path
            ), mock.patch.object(runner, "BUNDLE_ROOT", root):
                observed = runner._await_launch_ack(
                    run_id="a" * 64,
                    controller_sha256="b" * 64,
                    schedule_length=192,
                    ready_status_sha256=acknowledgement["ready_status_sha256"],
                    expected_uid=os.getuid(),
                    timeout_seconds=0,
                )

            self.assertEqual(observed, acknowledgement)
            self.assertTrue(ack_path.is_file())
            self.assertTrue(confirmation_path.is_file())

    def test_launch_ack_rejects_wrong_binding_and_mode(self) -> None:
        for mutation in ("run_id", "mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                code_root = root / "code"
                code_root.mkdir()
                acknowledgement = runner.launch_protocol.acknowledgement(
                    run_id="a" * 64,
                    controller_sha256="b" * 64,
                    schedule_length=192,
                    ready_status_sha256="c" * 64,
                )
                if mutation == "run_id":
                    acknowledgement["run_id"] = "d" * 64
                ack_path = code_root / "launch-ack.json"
                ack_path.write_bytes(
                    runner.launch_protocol.canonical_json_line(acknowledgement)
                )
                ack_path.chmod(0o600 if mutation == "mode" else 0o444)
                confirmation_path = root / "launch-confirmation.json"
                commit_path = code_root / "launch-commit.json"
                with mock.patch.object(
                    runner, "LAUNCH_ACK_PATH", ack_path
                ), mock.patch.object(
                    runner, "LAUNCH_CONFIRMATION_PATH", confirmation_path
                ), mock.patch.object(
                    runner, "LAUNCH_COMMIT_PATH", commit_path
                ), mock.patch.object(runner, "BUNDLE_ROOT", root), self.assertRaises(
                    RuntimeError
                ):
                    runner._await_launch_ack(
                        run_id="a" * 64,
                        controller_sha256="b" * 64,
                        schedule_length=192,
                        ready_status_sha256="c" * 64,
                        expected_uid=os.getuid(),
                        timeout_seconds=0,
                    )
                self.assertFalse(confirmation_path.exists())

    def test_terminal_failure_status_preserves_content_free_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(runner, "BUNDLE_ROOT", root), mock.patch.object(
                runner, "STATUS_PATH", root / "nightly-status.json"
            ):
                runner._write_terminal_failure(
                    run_id="a" * 64,
                    schedule_length=192,
                    stage="answers",
                    witness={
                        "schedule_position": 17,
                        "model_calls_reserved": 18,
                        "model_calls_closed": 17,
                    },
                )

            status = json.loads((root / "nightly-status.json").read_bytes())
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["schedule_position"], 17)
            self.assertEqual(status["model_calls_reserved"], 18)
            self.assertEqual(status["model_calls_closed"], 17)
            self.assertNotIn("error", status)

    def test_bootstrap_failure_records_content_free_terminal_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(runner, "BUNDLE_ROOT", root), mock.patch.object(
                runner, "STATUS_PATH", root / "nightly-status.json"
            ), mock.patch.object(
                runner, "LOCK_PATH", root / "nightly-runner.lock"
            ), mock.patch.object(
                runner.a11b_postprocess,
                "_load_controller",
                side_effect=RuntimeError("bootstrap failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                    runner.run()

            status = json.loads((root / "nightly-status.json").read_bytes())
            self.assertEqual(status["stage"], "bootstrap")
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["model_calls_reserved"], 0)
            self.assertEqual(status["model_calls_closed"], 0)
            self.assertNotIn("error", status)

    def test_bootstrap_failure_replaces_stale_active_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "nightly-status.json"
            status_path.write_bytes(
                runner.launch_protocol.canonical_json_line(
                    runner.launch_protocol.readiness_status(
                        run_id="f" * 64,
                        schedule_length=192,
                        updated_at="2026-07-17T00:00:00Z",
                    )
                )
            )
            status_path.chmod(0o600)
            with mock.patch.object(runner, "BUNDLE_ROOT", root), mock.patch.object(
                runner, "STATUS_PATH", status_path
            ), mock.patch.object(
                runner, "LOCK_PATH", root / "nightly-runner.lock"
            ), mock.patch.object(
                runner.a11b_postprocess,
                "_load_controller",
                side_effect=RuntimeError("bootstrap failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                    runner.run()

            status = json.loads(status_path.read_bytes())
            self.assertEqual(status["stage"], "bootstrap")
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["run_id"], "unavailable")

    def test_runner_records_terminal_failure_before_reraising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = {
                "experiment_profile": "a11b-causal-isolation-v2",
                "run_id": "a" * 64,
                "inputs": {"answer_calls": 1152},
                "outputs": {},
            }
            executor = FailingExecutor()
            with mock.patch.object(runner, "BUNDLE_ROOT", root), mock.patch.object(
                runner, "AUDIT_ROOT", root / "audit-input"
            ), mock.patch.object(
                runner, "STATUS_PATH", root / "nightly-status.json"
            ), mock.patch.object(
                runner, "LOCK_PATH", root / "nightly-runner.lock"
            ), mock.patch.object(
                runner.a11b_postprocess,
                "_load_controller",
                return_value=(controller, "d" * 64),
            ), mock.patch.object(
                runner.a11b_postprocess, "_verify_installed_postprocess_sources"
            ), mock.patch.object(
                runner.service,
                "load_sealed_service",
                return_value=SimpleNamespace(_executor=executor),
            ), mock.patch.object(
                runner, "_await_launch_ack"
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated executor failure"):
                    runner.run()

            status = json.loads((root / "nightly-status.json").read_bytes())
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["schedule_position"], 0)
            self.assertEqual(status["model_calls_reserved"], 0)
            self.assertEqual(status["model_calls_closed"], 0)
            self.assertNotIn("error", status)

    def test_runner_rejects_overlapping_resume_before_loading_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "nightly-runner.lock"
            with run_lock.acquire_single_instance(lock_path), mock.patch.object(
                runner, "LOCK_PATH", lock_path
            ), mock.patch.object(
                runner.a11b_postprocess, "_load_controller"
            ) as load_controller:
                with self.assertRaises(run_lock.AlreadyRunning):
                    runner.run()
            load_controller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
