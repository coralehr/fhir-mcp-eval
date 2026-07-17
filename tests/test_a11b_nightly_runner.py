from __future__ import annotations

import json
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


class A11bNightlyRunnerTests(unittest.TestCase):
    def test_runner_carries_answers_through_registered_postprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = {
                "run_id": "a" * 64,
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
            ) as postprocess:
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
