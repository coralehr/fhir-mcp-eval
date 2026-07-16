from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import unittest
import fcntl
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import experiment_executor as executor
import experiment_witness as witness


CALL_KEY = bytes(range(32))
RUN_KEY = b"r" * 32


class FakeModelDriver:
    def __init__(
        self,
        *,
        events: list[dict[str, object]] | None = None,
        answer: dict[str, object] | None = None,
        stderr: bytes = b"",
        exit_code: int = 0,
        fail_after_spawn: bool = False,
        omit_answer: bool = False,
        omit_stderr: bool = False,
        mutate_runtime: bool = False,
        raw_events: bytes | None = None,
    ) -> None:
        self.events = [
            {"type": "thread.started", "thread_id": "test-thread"},
            {"type": "turn.started"},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 12,
                    "cached_input_tokens": 3,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 17,
                },
            },
        ] if events is None else events
        self.answer = answer or {"answer": "synthetic"}
        self.stderr = stderr
        self.exit_code = exit_code
        self.fail_after_spawn = fail_after_spawn
        self.omit_answer = omit_answer
        self.omit_stderr = omit_stderr
        self.mutate_runtime = mutate_runtime
        self.raw_events = raw_events
        self.spawn_count = 0
        self._lock = threading.Lock()

    def invoke(
        self,
        invocation: executor.SealedInvocation,
        capture_dir: Path,
    ) -> executor.DriverTermination:
        with self._lock:
            self.spawn_count += 1
        if self.fail_after_spawn:
            raise RuntimeError("simulated process-host crash")
        capture_dir.mkdir(parents=True, exist_ok=True)
        if self.raw_events is None:
            (capture_dir / "events.jsonl").write_text(
                "".join(
                    json.dumps(event, sort_keys=True) + "\n"
                    for event in self.events
                ),
                encoding="utf-8",
            )
        else:
            (capture_dir / "events.jsonl").write_bytes(self.raw_events)
        if not self.omit_answer:
            (capture_dir / "answer.json").write_text(
                json.dumps(self.answer, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if not self.omit_stderr:
            (capture_dir / "stderr.log").write_bytes(self.stderr)
        if self.mutate_runtime:
            runtime_path = Path(invocation.runtime_path)
            runtime_path.chmod(0o700)
            runtime_path.write_bytes(b"mutated runtime")
            runtime_path.chmod(0o500)
        return executor.DriverTermination(
            exit_code=self.exit_code,
            timed_out=False,
            runtime_sha256=invocation.runtime_sha256,
        )


class FileCountDriver(FakeModelDriver):
    def __init__(self, counter_path: Path) -> None:
        super().__init__()
        self.counter_path = counter_path

    def invoke(
        self,
        invocation: executor.SealedInvocation,
        capture_dir: Path,
    ) -> executor.DriverTermination:
        descriptor = os.open(self.counter_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            raw = os.read(descriptor, 64)
            value = int(raw or b"0") + 1
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, str(value).encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return super().invoke(invocation, capture_dir)


def _test_invocation(root: Path, index: int = 0) -> executor.SealedInvocation:
    runtime = root / "trusted-codex"
    if not runtime.exists():
        runtime.write_bytes(b"synthetic pinned runtime")
        runtime.chmod(0o500)
    return executor.SealedInvocation(
        phase="answer",
        schedule_index=index,
        prompt=f"synthetic prompt {index}".encode(),
        output_schema=json.dumps(
            {
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
            sort_keys=True,
        ).encode(),
        model="gpt-test-pinned",
        reasoning_effort="high",
        runtime_path=str(runtime),
        runtime_sha256=witness.sha256_bytes(runtime.read_bytes()),
        timeout_seconds=600,
    )


def _process_execute_worker(
    root_text: str,
    counter_text: str,
    start: multiprocessing.synchronize.Event,
    output: multiprocessing.queues.Queue,
) -> None:
    root = Path(root_text)
    invocation = _test_invocation(root)
    authenticator = witness.SshEd25519Authenticator(
        private_key_path=root / "witness-ed25519",
        identity="coralehr-test-executor",
    )
    run_id = witness.keyed_commitment(
        RUN_KEY, domain="executor-test-run", payload=b"sealed controller"
    )
    ledger = witness.WitnessLedger(
        root / "ledger",
        run_id=run_id,
        schedule=(
            witness.ScheduleItem(
                phase=invocation.phase,
                schedule_index=invocation.schedule_index,
                call_commitment=invocation.call_commitment(CALL_KEY),
                max_attempts=3,
            ),
        ),
        authenticator=authenticator,
        clock=lambda: "2026-07-15T20:00:00Z",
    )
    service = executor.ExperimentExecutor(
        root / "executor",
        ledger=ledger,
        invocations=(invocation,),
        commitment_key=CALL_KEY,
        driver=FileCountDriver(Path(counter_text)),
    )
    start.wait()
    try:
        result = service.execute_next(
            run_id=run_id,
            expected_head=witness.GENESIS_HEAD,
        )
        output.put(("ok", result.artifact_ref, result.outcome))
    except Exception as exc:  # pragma: no cover - reported to the parent
        output.put(("error", type(exc).__name__, str(exc)))


class CrashOnce:
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        self.triggered = False

    def __call__(self, boundary: str) -> None:
        if boundary == self.boundary and not self.triggered:
            self.triggered = True
            raise RuntimeError(f"simulated crash at {boundary}")


class ExperimentExecutorTests(unittest.TestCase):
    def make_authenticator(
        self, root: Path
    ) -> witness.SshEd25519Authenticator:
        private_key = root / "witness-ed25519"
        subprocess.run(
            [
                str(witness.SSH_KEYGEN_PATH),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "coralehr-test-executor",
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return witness.SshEd25519Authenticator(
            private_key_path=private_key,
            identity="coralehr-test-executor",
        )

    @staticmethod
    def invocation(root: Path, index: int = 0) -> executor.SealedInvocation:
        return _test_invocation(root, index)

    def make_system(
        self,
        root: Path,
        *,
        driver: FakeModelDriver | None = None,
        checkpoint: object | None = None,
        invocations: tuple[executor.SealedInvocation, ...] | None = None,
    ) -> tuple[executor.ExperimentExecutor, witness.WitnessLedger, FakeModelDriver]:
        calls = invocations or (self.invocation(root),)
        authenticator = self.make_authenticator(root)
        ledger = witness.WitnessLedger(
            root / "ledger",
            run_id=witness.keyed_commitment(
                RUN_KEY, domain="executor-test-run", payload=b"sealed controller"
            ),
            schedule=tuple(
                witness.ScheduleItem(
                    phase=call.phase,
                    schedule_index=call.schedule_index,
                    call_commitment=call.call_commitment(CALL_KEY),
                    max_attempts=3,
                )
                for call in calls
            ),
            authenticator=authenticator,
            clock=lambda: "2026-07-15T20:00:00Z",
        )
        actual_driver = driver or FakeModelDriver()
        service = executor.ExperimentExecutor(
            root / "executor",
            ledger=ledger,
            invocations=calls,
            commitment_key=CALL_KEY,
            driver=actual_driver,
            checkpoint=checkpoint,
        )
        return service, ledger, actual_driver

    def restart(
        self,
        root: Path,
        ledger: witness.WitnessLedger,
        driver: FakeModelDriver,
        *,
        invocations: tuple[executor.SealedInvocation, ...] | None = None,
    ) -> executor.ExperimentExecutor:
        return executor.ExperimentExecutor(
            root / "executor",
            ledger=ledger,
            invocations=invocations or (self.invocation(root),),
            commitment_key=CALL_KEY,
            driver=driver,
        )

    def test_execute_next_derives_and_witnesses_one_complete_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            result = service.execute_next(
                run_id=ledger.run_id,
                expected_head=witness.GENESIS_HEAD,
            )

            self.assertEqual(driver.spawn_count, 1)
            self.assertEqual(result.outcome, "accepted")
            self.assertEqual(
                result.token_usage,
                {
                    "input": 12,
                    "cached": 3,
                    "output": 5,
                    "reasoning": 2,
                    "total": 17,
                    "complete": True,
                    "source": "turn.completed",
                },
            )
            self.assertEqual(ledger.status()["model_calls_reserved"], 1)
            self.assertEqual(ledger.status()["model_calls_closed"], 1)
            artifact = json.loads(
                service.fetch_artifact(
                    run_id=ledger.run_id,
                    artifact_ref=result.artifact_ref,
                )
            )
            self.assertEqual(artifact["derived"]["outcome"], "accepted")
            self.assertIn("events.jsonl", artifact["capture"]["files_base64"])
            status = service.status(run_id=ledger.run_id)
            self.assertEqual(len(status["signed_receipts"]), 2)
            self.assertEqual(status["attempts"][0]["state"], "closed")

    def test_public_call_rejects_caller_controlled_execution_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            with self.assertRaises(TypeError):
                service.execute_next(  # type: ignore[call-arg]
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                    prompt=b"caller injection",
                )
            self.assertEqual(driver.spawn_count, 0)
            self.assertEqual(ledger.status()["events"], 0)

    def test_stale_head_blocks_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            with self.assertRaisesRegex(executor.ExecutorProtocolError, "head"):
                service.execute_next(run_id=ledger.run_id, expected_head="c" * 64)
            self.assertEqual(driver.spawn_count, 0)
            self.assertEqual(ledger.status()["events"], 0)

    def test_crash_after_open_is_retry_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            crash = CrashOnce("after_open")
            service, ledger, driver = self.make_system(root, checkpoint=crash)
            with self.assertRaisesRegex(RuntimeError, "after_open"):
                service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )

            restarted = self.restart(root, ledger, driver)
            result = restarted.execute_next(
                run_id=ledger.run_id,
                expected_head=witness.GENESIS_HEAD,
            )
            self.assertEqual(result.outcome, "accepted")
            self.assertEqual(driver.spawn_count, 1)
            self.assertEqual(ledger.status()["model_calls_reserved"], 1)

    def test_spawn_intent_without_capture_is_terminal_and_never_respawns(self) -> None:
        for mode in ("before_driver", "inside_driver"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                crash = CrashOnce("after_spawn_intent") if mode == "before_driver" else None
                driver = FakeModelDriver(fail_after_spawn=mode == "inside_driver")
                service, ledger, driver = self.make_system(
                    root, driver=driver, checkpoint=crash
                )
                with self.assertRaises((RuntimeError, executor.ExecutorIndeterminateError)):
                    service.execute_next(
                        run_id=ledger.run_id,
                        expected_head=witness.GENESIS_HEAD,
                    )
                spawn_count = driver.spawn_count

                restarted = self.restart(root, ledger, driver)
                for _ in range(2):
                    with self.assertRaises(executor.ExecutorIndeterminateError):
                        restarted.execute_next(
                            run_id=ledger.run_id,
                            expected_head=witness.GENESIS_HEAD,
                        )
                self.assertEqual(driver.spawn_count, spawn_count)
                self.assertEqual(ledger.status()["state"], "aborted")
                self.assertEqual(ledger.status()["model_calls_closed"], 1)
                self.assertEqual(
                    restarted.status(run_id=ledger.run_id)["attempts"][0]["state"],
                    "indeterminate",
                )

    def test_capture_and_close_crashes_recover_without_respawn(self) -> None:
        for boundary in ("after_capture", "after_close", "after_result"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service, ledger, driver = self.make_system(
                    root, checkpoint=CrashOnce(boundary)
                )
                with self.assertRaisesRegex(RuntimeError, boundary):
                    service.execute_next(
                        run_id=ledger.run_id,
                        expected_head=witness.GENESIS_HEAD,
                    )

                restarted = self.restart(root, ledger, driver)
                result = restarted.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
                self.assertEqual(result.outcome, "accepted")
                self.assertEqual(driver.spawn_count, 1)
                self.assertEqual(ledger.status()["model_calls_closed"], 1)

    def test_concurrent_same_head_has_one_reservation_and_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)

            def execute() -> executor.ExecutionBundle:
                return service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: execute(), range(2)))
            self.assertEqual(driver.spawn_count, 1)
            self.assertEqual(results[0], results[1])
            self.assertEqual(ledger.status()["model_calls_reserved"], 1)

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "requires fork-capable POSIX process concurrency",
    )
    def test_cross_process_same_head_has_one_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _service, ledger, _driver = self.make_system(root)
            counter = root / "spawn-count"
            counter.write_text("0", encoding="ascii")
            context = multiprocessing.get_context("fork")
            start = context.Event()
            output = context.Queue()
            processes = [
                context.Process(
                    target=_process_execute_worker,
                    args=(str(root), str(counter), start, output),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)
            rows = [output.get(timeout=5) for _ in processes]
            self.assertEqual({row[0] for row in rows}, {"ok"})
            self.assertEqual(len({row[1] for row in rows}), 1)
            self.assertEqual(counter.read_text(encoding="ascii"), "1")
            self.assertEqual(ledger.status()["model_calls_reserved"], 1)
            self.assertEqual(ledger.status()["model_calls_closed"], 1)

    def test_provider_failure_usage_is_explicitly_incomplete(self) -> None:
        events = [
            {"type": "thread.started", "thread_id": "test-thread"},
            {"type": "turn.started"},
            {"type": "error", "message": "synthetic provider failure"},
            {
                "type": "turn.failed",
                "error": {"message": "synthetic provider failure"},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, _ = self.make_system(
                root,
                driver=FakeModelDriver(
                    events=events, exit_code=1, omit_answer=True
                ),
            )
            result = service.execute_next(
                run_id=ledger.run_id,
                expected_head=witness.GENESIS_HEAD,
            )
            self.assertEqual(result.outcome, "provider_failure")
            self.assertFalse(result.token_usage["complete"])
            self.assertIsNone(result.token_usage["total"])
            self.assertEqual(result.token_usage["source"], "provider.error")
            self.assertEqual(ledger.status()["state"], "active")

    def test_nonconforming_answer_or_mixed_failure_transcript_is_not_accepted(
        self,
    ) -> None:
        valid_usage = {
            "input_tokens": 12,
            "cached_input_tokens": 3,
            "output_tokens": 5,
            "reasoning_output_tokens": 2,
            "total_tokens": 17,
        }
        cases = (
            (
                "wrong_schema",
                FakeModelDriver(answer={"not_answer": "synthetic"}),
            ),
            (
                "error_then_completed",
                FakeModelDriver(
                    events=[
                        {"type": "thread.started", "thread_id": "test-thread"},
                        {"type": "turn.started"},
                        {"type": "error", "message": "synthetic error"},
                        {
                            "type": "turn.failed",
                            "error": {"message": "synthetic error"},
                        },
                        {"type": "turn.completed", "usage": valid_usage},
                    ]
                ),
            ),
        )
        for label, driver in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service, ledger, driver = self.make_system(root, driver=driver)
                result = service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
                self.assertEqual(result.outcome, "contaminated")
                self.assertEqual(driver.spawn_count, 1)
                self.assertEqual(ledger.status()["state"], "aborted")

    def test_missing_stderr_or_runtime_drift_is_terminal_contamination(self) -> None:
        for label, driver in (
            ("missing_stderr", FakeModelDriver(omit_stderr=True)),
            ("runtime_drift", FakeModelDriver(mutate_runtime=True)),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service, ledger, driver = self.make_system(root, driver=driver)
                result = service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
                self.assertEqual(result.outcome, "contaminated")
                self.assertEqual(driver.spawn_count, 1)
                self.assertEqual(ledger.status()["state"], "aborted")

    def test_pre_spawn_runtime_change_aborts_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            runtime = Path(service.invocations[0].runtime_path)
            runtime.chmod(0o700)
            runtime.write_bytes(b"changed before spawn")
            runtime.chmod(0o500)
            with self.assertRaises(executor.ExecutorIndeterminateError):
                service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
            self.assertEqual(driver.spawn_count, 0)
            self.assertEqual(ledger.status()["state"], "aborted")

    def test_empty_or_malformed_event_capture_closes_contaminated(self) -> None:
        cases = (
            ("empty", []),
            ("missing_completed", [{"type": "thread.started", "thread_id": "x"}]),
        )
        for label, events in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service, ledger, driver = self.make_system(
                    root, driver=FakeModelDriver(events=events)
                )
                result = service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
                self.assertEqual(result.outcome, "contaminated")
                self.assertEqual(driver.spawn_count, 1)
                self.assertEqual(ledger.status()["state"], "aborted")

    def test_marker_publication_is_atomic_on_partial_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "marker.json"

            def partial_write(descriptor: int, payload: bytes) -> None:
                os.write(descriptor, payload[:3])
                raise OSError("simulated interrupted write")

            with mock.patch.object(
                executor.ExperimentExecutor,
                "_write_all",
                side_effect=partial_write,
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    executor.ExperimentExecutor._publish_bytes(target, b"payload")
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".tmp-*")), [])

    def test_interrupted_open_and_capture_publication_follow_crash_policy(
        self,
    ) -> None:
        original_write_all = executor.ExperimentExecutor._write_all
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            interrupted = False

            def fail_first_write(descriptor: int, payload: bytes) -> None:
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    os.write(descriptor, payload[:3])
                    raise OSError("interrupted opened marker")
                original_write_all(descriptor, payload)

            with mock.patch.object(
                executor.ExperimentExecutor,
                "_write_all",
                side_effect=fail_first_write,
            ):
                with self.assertRaisesRegex(OSError, "opened marker"):
                    service.execute_next(
                        run_id=ledger.run_id,
                        expected_head=witness.GENESIS_HEAD,
                    )
            result = self.restart(root, ledger, driver).execute_next(
                run_id=ledger.run_id,
                expected_head=witness.GENESIS_HEAD,
            )
            self.assertEqual(result.outcome, "accepted")
            self.assertEqual(driver.spawn_count, 1)
            self.assertEqual(ledger.status()["model_calls_reserved"], 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            interrupted = False

            def fail_capture_write(descriptor: int, payload: bytes) -> None:
                nonlocal interrupted
                if (
                    not interrupted
                    and b'"kind":"experiment_executor_capture"' in payload
                ):
                    interrupted = True
                    os.write(descriptor, payload[:7])
                    raise OSError("interrupted capture marker")
                original_write_all(descriptor, payload)

            with mock.patch.object(
                executor.ExperimentExecutor,
                "_write_all",
                side_effect=fail_capture_write,
            ):
                with self.assertRaisesRegex(OSError, "capture marker"):
                    service.execute_next(
                        run_id=ledger.run_id,
                        expected_head=witness.GENESIS_HEAD,
                    )
            with self.assertRaises(executor.ExecutorIndeterminateError):
                self.restart(root, ledger, driver).execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
            self.assertEqual(driver.spawn_count, 1)
            self.assertEqual(ledger.status()["state"], "aborted")

    def test_malformed_token_economics_never_close_as_accepted(self) -> None:
        def stream(usage: object, *, duplicate: bool = False) -> bytes:
            events: list[dict[str, object]] = [
                {"type": "thread.started", "thread_id": "test-thread"},
                {"type": "turn.started"},
                {"type": "turn.completed", "usage": usage},
            ]
            if duplicate:
                events.append({"type": "turn.completed", "usage": usage})
            return "".join(
                json.dumps(event, sort_keys=True) + "\n" for event in events
            ).encode()

        base = {
            "input_tokens": 12,
            "cached_input_tokens": 3,
            "output_tokens": 5,
            "reasoning_output_tokens": 2,
            "total_tokens": 17,
        }
        cases = {
            "negative": stream({**base, "input_tokens": -1}),
            "boolean": stream({**base, "input_tokens": True}),
            "cached_gt_input": stream({**base, "cached_input_tokens": 13}),
            "reasoning_gt_output": stream(
                {**base, "reasoning_output_tokens": 6}
            ),
            "total_mismatch": stream({**base, "total_tokens": 99}),
            "duplicate_completed": stream(base, duplicate=True),
            "malformed_json": b'{"type":"thread.started"}\n{broken\n',
            "missing_newline": stream(base).rstrip(b"\n"),
        }
        for label, raw_events in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service, ledger, driver = self.make_system(
                    root, driver=FakeModelDriver(raw_events=raw_events)
                )
                result = service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
                self.assertEqual(result.outcome, "contaminated")
                self.assertEqual(driver.spawn_count, 1)
                self.assertEqual(ledger.status()["state"], "aborted")

    def test_artifact_tamper_is_detected_without_resampling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, ledger, driver = self.make_system(root)
            result = service.execute_next(
                run_id=ledger.run_id,
                expected_head=witness.GENESIS_HEAD,
            )
            bundle_path = next((root / "executor" / "attempts").glob("*/bundle.json"))
            bundle_path.chmod(0o600)
            bundle_path.write_bytes(bundle_path.read_bytes() + b" ")
            with self.assertRaises(executor.ExecutorIntegrityError):
                service.fetch_artifact(
                    run_id=ledger.run_id,
                    artifact_ref=result.artifact_ref,
                )
            with self.assertRaises(executor.ExecutorIntegrityError):
                service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )
            with self.assertRaises(executor.ExecutorIntegrityError):
                service.status(run_id=ledger.run_id)
            self.assertEqual(driver.spawn_count, 1)


if __name__ == "__main__":
    unittest.main()
