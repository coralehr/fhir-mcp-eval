from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import experiment_executor as executor
import experiment_witness as witness
import trusted_codex_driver as driver_module


SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["answer"],
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
    },
    sort_keys=True,
).encode()


class _CompletedProcess:
    def __init__(self, *, stdout: object, stderr: object) -> None:
        self.pid = 4242
        self.returncode = 0
        self._stdout = stdout
        self._stderr = stderr
        self.input: bytes | None = None

    def communicate(self, input: bytes, timeout: int) -> tuple[None, None]:
        self.input = input
        self._stdout.write(b'{"type":"thread.started","thread_id":"t"}\n')
        self._stdout.write(b'{"type":"turn.started"}\n')
        self._stdout.write(
            b'{"type":"turn.completed","usage":{"input_tokens":1,'
            b'"cached_input_tokens":0,"output_tokens":1,'
            b'"reasoning_output_tokens":0,"total_tokens":2}}\n'
        )
        self._stdout.flush()
        self._stderr.flush()
        return None, None


class TrustedCodexDriverTests(unittest.TestCase):
    def _private_dir(self, root: Path, name: str) -> Path:
        path = root / name
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        return path

    def _runtime(self, root: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
        path = root / "codex-native"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o700)
        return path

    def _sandbox_exec(self, root: Path) -> tuple[Path, str]:
        sandbox_exec = root / "sandbox-exec"
        if not sandbox_exec.exists():
            sandbox_exec.write_text(
                '#!/bin/sh\ntest "$1" = "-p" || exit 91\nshift 2\nexec "$@"\n',
                encoding="utf-8",
            )
            sandbox_exec.chmod(0o500)
        return sandbox_exec, hashlib.sha256(sandbox_exec.read_bytes()).hexdigest()

    def _driver(self, root: Path) -> driver_module.TrustedCodexDriver:
        account_home = self._private_dir(root, "home")
        codex_home = self._private_dir(root, "codex-home")
        auth = codex_home / "auth.json"
        auth.write_text("credential-sentinel", encoding="utf-8")
        auth.chmod(0o600)
        scratch = self._private_dir(root, "scratch")
        sandbox_exec, sandbox_exec_sha256 = self._sandbox_exec(root)
        return driver_module.TrustedCodexDriver(
            account_home=account_home,
            codex_home=codex_home,
            scratch_root=scratch,
            sandbox_exec_path=sandbox_exec,
            sandbox_exec_sha256=sandbox_exec_sha256,
        )

    def _invocation(self, runtime: Path) -> executor.SealedInvocation:
        return executor.SealedInvocation(
            phase="answer",
            schedule_index=0,
            prompt=b"exact prompt bytes\n\x00",
            output_schema=SCHEMA,
            model="gpt-test-model",
            reasoning_effort="high",
            runtime_path=str(runtime),
            runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            timeout_seconds=17,
        )

    def _service_for_driver(
        self,
        root: Path,
        invocation: executor.SealedInvocation,
        model_driver: driver_module.TrustedCodexDriver,
    ) -> tuple[executor.ExperimentExecutor, witness.WitnessLedger]:
        private_key = root / "witness-ed25519"
        driver_module.subprocess.run(
            [
                str(witness.SSH_KEYGEN_PATH),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "trusted-driver-test",
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        authenticator = witness.SshEd25519Authenticator(
            private_key_path=private_key,
            identity="trusted-driver-test",
        )
        call_key = bytes(range(32))
        run_id = witness.keyed_commitment(
            b"r" * 32,
            domain="trusted-driver-test-run",
            payload=b"sealed-controller",
        )
        ledger = witness.WitnessLedger(
            root / "ledger",
            run_id=run_id,
            schedule=(
                witness.ScheduleItem(
                    phase=invocation.phase,
                    schedule_index=invocation.schedule_index,
                    call_commitment=invocation.call_commitment(call_key),
                    max_attempts=1,
                ),
            ),
            authenticator=authenticator,
            clock=lambda: "2026-07-15T21:00:00Z",
        )
        service = executor.ExperimentExecutor(
            root / "executor",
            ledger=ledger,
            invocations=(invocation,),
            commitment_key=call_key,
            driver=model_driver,
        )
        return service, ledger

    def test_constructs_fixed_native_command_and_scrubbed_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            capture = self._private_dir(root, "capture")
            driver = self._driver(root)
            spawned: dict[str, object] = {}

            def popen(args: list[str], **kwargs: object) -> _CompletedProcess:
                spawned["args"] = args
                spawned.update(kwargs)
                cwd = Path(str(kwargs["cwd"]))
                child_tmp = Path(str(kwargs["env"]["TMPDIR"]))
                spawned["cwd_private_empty"] = (
                    cwd.parent.parent == driver.scratch_root
                    and stat.S_IMODE(cwd.stat().st_mode) == 0o700
                    and list(cwd.iterdir()) == []
                )
                spawned["tmp_private_empty"] = (
                    child_tmp.parent == cwd.parent
                    and stat.S_IMODE(child_tmp.stat().st_mode) == 0o700
                    and list(child_tmp.iterdir()) == []
                )
                answer_index = args.index("--output-last-message") + 1
                Path(args[answer_index]).write_text(
                    json.dumps({"answer": "ok"}), encoding="utf-8"
                )
                process = _CompletedProcess(
                    stdout=kwargs["stdout"], stderr=kwargs["stderr"]
                )
                spawned["process"] = process
                return process

            hostile = {
                "OPENAI_API_KEY": "must-not-leak",
                "HTTPS_PROXY": "must-not-leak",
                "PYTHONPATH": "must-not-leak",
                "NODE_OPTIONS": "must-not-leak",
                "CODEX_UNTRUSTED": "must-not-leak",
            }
            with (
                mock.patch.dict(os.environ, hostile, clear=False),
                mock.patch.object(driver_module.subprocess, "Popen", side_effect=popen),
                mock.patch.object(driver, "_require_native_runtime"),
            ):
                invocation = self._invocation(runtime)
                termination = driver.invoke(invocation, capture)

            self.assertEqual(termination.exit_code, 0)
            self.assertFalse(termination.timed_out)
            args = spawned["args"]
            schema_path = args[args.index("--output-schema") + 1]
            answer_path = args[args.index("--output-last-message") + 1]
            working_directory = args[args.index("-C") + 1]
            self.assertEqual(
                args,
                [
                    str(driver.sandbox_exec_path),
                    "-p",
                    driver._SANDBOX_PROFILE,
                    str(runtime),
                    "-a",
                    "never",
                    "--strict-config",
                    "-c",
                    'shell_environment_policy.inherit="none"',
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--json",
                    "--output-schema",
                    schema_path,
                    "--output-last-message",
                    answer_path,
                    "-C",
                    working_directory,
                    "-s",
                    "read-only",
                    "--color",
                    "never",
                    "-c",
                    'model_reasoning_effort="high"',
                    "-m",
                    "gpt-test-model",
                    "-",
                ],
            )
            self.assertFalse(spawned["shell"])
            self.assertTrue(spawned["start_new_session"])
            self.assertEqual(spawned["umask"], 0o077)
            self.assertTrue(callable(spawned["preexec_fn"]))
            self.assertEqual(spawned["stdin"], driver_module.subprocess.PIPE)
            environment = spawned["env"]
            self.assertEqual(
                set(environment),
                {"CODEX_HOME", "HOME", "LANG", "LC_ALL", "NO_COLOR", "PATH", "TMPDIR"},
            )
            for name in hostile:
                self.assertNotIn(name, environment)
            self.assertEqual(environment["CODEX_HOME"], str(driver.codex_home))
            self.assertEqual(environment["HOME"], str(driver.account_home))
            self.assertEqual(environment["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
            self.assertEqual(environment["LANG"], "en_US.UTF-8")
            self.assertEqual(environment["LC_ALL"], "en_US.UTF-8")
            self.assertEqual(environment["NO_COLOR"], "1")
            self.assertEqual(spawned["process"].input, invocation.prompt)
            self.assertTrue(spawned["cwd_private_empty"])
            self.assertTrue(spawned["tmp_private_empty"])
            self.assertEqual(spawned["cwd"], args[args.index("-C") + 1])
            self.assertEqual(
                {path.name for path in capture.iterdir()},
                {"answer.json", "events.jsonl", "stderr.log"},
            )
            self.assertEqual((capture / "stderr.log").read_bytes(), b"")
            self.assertEqual(stat.S_IMODE((capture / "events.jsonl").stat().st_mode), 0o600)

    def test_rejects_non_native_runtime_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            capture = self._private_dir(root, "capture")
            driver = self._driver(root)
            with (
                mock.patch.object(driver_module.subprocess, "Popen") as popen,
                self.assertRaises(driver_module.TrustedDriverError),
            ):
                driver.invoke(self._invocation(runtime), capture)
            popen.assert_not_called()

    def test_rejects_untrusted_codex_home_and_credential_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account_home = self._private_dir(root, "home")
            scratch = self._private_dir(root, "scratch")
            sandbox_exec, sandbox_exec_sha256 = self._sandbox_exec(root)
            for label, mutate in (
                ("home_mode", lambda home, auth: home.chmod(0o755)),
                ("auth_mode", lambda home, auth: auth.chmod(0o644)),
                (
                    "auth_symlink",
                    lambda home, auth: (
                        auth.unlink(),
                        auth.symlink_to(root / "outside-auth"),
                    ),
                ),
            ):
                with self.subTest(label=label):
                    codex_home = root / f"codex-{label}"
                    codex_home.mkdir(mode=0o700)
                    auth = codex_home / "auth.json"
                    auth.write_text("sentinel", encoding="utf-8")
                    auth.chmod(0o600)
                    if label == "auth_symlink":
                        (root / "outside-auth").write_text("sentinel", encoding="utf-8")
                    mutate(codex_home, auth)
                    with self.assertRaises(driver_module.TrustedDriverError):
                        driver_module.TrustedCodexDriver(
                            account_home=account_home,
                            codex_home=codex_home,
                            scratch_root=scratch,
                            sandbox_exec_path=sandbox_exec,
                            sandbox_exec_sha256=sandbox_exec_sha256,
                        )

    def test_revalidates_credential_metadata_before_each_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            capture = self._private_dir(root, "capture")
            driver = self._driver(root)
            (driver.codex_home / "auth.json").chmod(0o644)
            with (
                mock.patch.object(driver_module.subprocess, "Popen") as popen,
                mock.patch.object(driver, "_require_native_runtime"),
                self.assertRaises(driver_module.TrustedDriverError),
            ):
                driver.invoke(self._invocation(runtime), capture)
            popen.assert_not_called()

    def test_timeout_kills_process_group_and_returns_terminal_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            capture = self._private_dir(root, "capture")
            driver = self._driver(root)

            class TimedOut(_CompletedProcess):
                def communicate(self, input: bytes, timeout: int) -> tuple[None, None]:
                    raise driver_module.subprocess.TimeoutExpired("codex", timeout)

                def wait(self, timeout: int | None = None) -> int:
                    self.returncode = -9
                    return self.returncode

            process: TimedOut | None = None

            def popen(_args: list[str], **kwargs: object) -> TimedOut:
                nonlocal process
                process = TimedOut(stdout=kwargs["stdout"], stderr=kwargs["stderr"])
                return process

            with (
                mock.patch.object(driver_module.subprocess, "Popen", side_effect=popen),
                mock.patch.object(driver_module.os, "killpg") as killpg,
                mock.patch.object(driver, "_require_native_runtime"),
                mock.patch.object(
                    driver_module.TrustedCodexDriver,
                    "_wait_for_process_group_exit",
                ),
            ):
                termination = driver.invoke(self._invocation(runtime), capture)

            self.assertIsNotNone(process)
            killpg.assert_called_once_with(process.pid, driver_module.signal.SIGKILL)
            self.assertTrue(termination.timed_out)
            self.assertEqual(termination.exit_code, -9)
            self.assertTrue((capture / "events.jsonl").is_file())
            self.assertTrue((capture / "stderr.log").is_file())
            self.assertFalse((capture / "answer.json").exists())

    @unittest.skipUnless(sys.platform == "darwin", "Mach-O validation is macOS-only")
    def test_real_native_validator_accepts_only_immutable_single_link_macho(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path("/usr/bin/true")
            accepted = root / "native"
            shutil.copyfile(source, accepted)
            accepted.chmod(0o500)
            driver_module.TrustedCodexDriver._require_native_runtime(accepted)

            accepted.chmod(0o700)
            writable = root / "writable"
            shutil.copyfile(source, writable)
            writable.chmod(0o720)
            hardlink = root / "hardlink"
            os.link(accepted, hardlink)
            symlink = root / "symlink"
            symlink.symlink_to(source)
            for path in (accepted, writable, hardlink, symlink):
                with self.subTest(path=path.name):
                    with self.assertRaises(driver_module.TrustedDriverError):
                        driver_module.TrustedCodexDriver._require_native_runtime(path)

    def test_communicate_error_with_credential_is_killed_and_scrubbed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            capture = self._private_dir(root, "capture")
            driver = self._driver(root)

            class Broken(_CompletedProcess):
                def communicate(self, input: bytes, timeout: int) -> tuple[None, None]:
                    self._stdout.write(b"credential-sentinel")
                    self._stdout.flush()
                    raise OSError("simulated transport failure")

                def wait(self, timeout: int | None = None) -> int:
                    self.returncode = -9
                    return self.returncode

            broken: Broken | None = None

            def popen(_args: list[str], **kwargs: object) -> Broken:
                nonlocal broken
                broken = Broken(stdout=kwargs["stdout"], stderr=kwargs["stderr"])
                return broken

            with (
                mock.patch.object(driver_module.subprocess, "Popen", side_effect=popen),
                mock.patch.object(driver_module.os, "killpg") as killpg,
                mock.patch.object(driver, "_require_native_runtime"),
                mock.patch.object(
                    driver_module.TrustedCodexDriver,
                    "_wait_for_process_group_exit",
                ),
                self.assertRaisesRegex(
                    driver_module.TrustedDriverError, "credential material"
                ),
            ):
                driver.invoke(self._invocation(runtime), capture)
            self.assertIsNotNone(broken)
            killpg.assert_any_call(broken.pid, signal.SIGKILL)
            self.assertEqual(list(capture.iterdir()), [])

    def test_post_spawn_credential_metadata_failure_scrubs_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            capture = self._private_dir(root, "capture")
            driver = self._driver(root)
            auth_path = driver.codex_home / "auth.json"

            class MutatesCredential(_CompletedProcess):
                def communicate(self, input: bytes, timeout: int) -> tuple[None, None]:
                    self._stdout.write(b'{"type":"thread.started","thread_id":"t"}\n')
                    self._stdout.flush()
                    auth_path.chmod(0o644)
                    return None, None

            def popen(_args: list[str], **kwargs: object) -> MutatesCredential:
                return MutatesCredential(
                    stdout=kwargs["stdout"], stderr=kwargs["stderr"]
                )

            with (
                mock.patch.object(driver_module.subprocess, "Popen", side_effect=popen),
                mock.patch.object(driver, "_require_native_runtime"),
                self.assertRaises(driver_module.TrustedDriverError),
            ):
                driver.invoke(self._invocation(runtime), capture)
            self.assertEqual(list(capture.iterdir()), [])

    def test_benign_fake_native_is_accepted_and_omits_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(
                root,
                body=(
                    f"#!{sys.executable}\n"
                    "import json, pathlib, sys\n"
                    "args = sys.argv[1:]\n"
                    "answer = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                    "_prompt = sys.stdin.buffer.read()\n"
                    "answer.write_text(json.dumps({'answer': 'synthetic'}))\n"
                    "events = [\n"
                    " {'type': 'thread.started', 'thread_id': 'fake'},\n"
                    " {'type': 'turn.started'},\n"
                    " {'type': 'turn.completed', 'usage': {\n"
                    "  'input_tokens': 8, 'cached_input_tokens': 2,\n"
                    "  'output_tokens': 3, 'reasoning_output_tokens': 1,\n"
                    "  'total_tokens': 11}},\n"
                    "]\n"
                    "for event in events: print(json.dumps(event), flush=True)\n"
                ),
            )
            invocation = self._invocation(runtime)
            class FakeNativeDriver(driver_module.TrustedCodexDriver):
                @staticmethod
                def _require_native_runtime(_path: Path) -> None:
                    return None

            configured = self._driver(root)
            production_driver = FakeNativeDriver(
                account_home=configured.account_home,
                codex_home=configured.codex_home,
                scratch_root=configured.scratch_root,
                sandbox_exec_path=configured.sandbox_exec_path,
                sandbox_exec_sha256=configured.sandbox_exec_sha256,
            )
            service, ledger = self._service_for_driver(
                root, invocation, production_driver
            )

            result = service.execute_next(
                run_id=ledger.run_id,
                expected_head=witness.GENESIS_HEAD,
            )
            artifact = service.fetch_artifact(
                run_id=ledger.run_id, artifact_ref=result.artifact_ref
            )

            self.assertEqual(result.outcome, "accepted")
            self.assertEqual(result.token_usage["total"], 11)
            self.assertNotIn(b"credential-sentinel", artifact)
            self.assertEqual(ledger.status()["model_calls_closed"], 1)

    def test_real_driver_failures_cannot_cross_executor_acceptance_boundary(
        self,
    ) -> None:
        cases = (
            (
                "nonempty_stderr",
                "import json, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "sys.stdin.buffer.read()\n"
                "pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps({'answer': 'synthetic'}))\n"
                "print(json.dumps({'type': 'thread.started', 'thread_id': 'fake'}), flush=True)\n"
                "print(json.dumps({'type': 'turn.started'}), flush=True)\n"
                "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'reasoning_output_tokens': 0, 'total_tokens': 2}}), flush=True)\n"
                "print('unexpected diagnostic', file=sys.stderr, flush=True)\n",
                17,
            ),
            (
                "timeout",
                "import sys, time\n"
                "sys.stdin.buffer.read()\n"
                "time.sleep(30)\n",
                1,
            ),
        )
        for label, body, timeout_seconds in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runtime = self._runtime(root, body=f"#!{sys.executable}\n{body}")

                class FakeNativeDriver(driver_module.TrustedCodexDriver):
                    @staticmethod
                    def _require_native_runtime(_path: Path) -> None:
                        return None

                configured = self._driver(root)
                production_driver = FakeNativeDriver(
                    account_home=configured.account_home,
                    codex_home=configured.codex_home,
                    scratch_root=configured.scratch_root,
                    sandbox_exec_path=configured.sandbox_exec_path,
                    sandbox_exec_sha256=configured.sandbox_exec_sha256,
                )
                invocation = self._invocation(runtime)
                invocation = executor.SealedInvocation(
                    **{
                        **invocation.__dict__,
                        "timeout_seconds": timeout_seconds,
                    }
                )
                service, ledger = self._service_for_driver(
                    root, invocation, production_driver
                )

                result = service.execute_next(
                    run_id=ledger.run_id,
                    expected_head=witness.GENESIS_HEAD,
                )

                self.assertEqual(result.outcome, "contaminated")
                self.assertEqual(ledger.status()["state"], "aborted")

    def test_runtime_cannot_copy_credential_into_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(
                root,
                body=(
                    f"#!{sys.executable}\n"
                    "import os, pathlib, sys\n"
                    "args = sys.argv[1:]\n"
                    "answer = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                    "answer.write_bytes((pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json').read_bytes())\n"
                    "sys.stdin.buffer.read()\n"
                ),
            )

            class FakeNativeDriver(driver_module.TrustedCodexDriver):
                @staticmethod
                def _require_native_runtime(_path: Path) -> None:
                    return None

            configured = self._driver(root)
            driver = FakeNativeDriver(
                account_home=configured.account_home,
                codex_home=configured.codex_home,
                scratch_root=configured.scratch_root,
                sandbox_exec_path=configured.sandbox_exec_path,
                sandbox_exec_sha256=configured.sandbox_exec_sha256,
            )
            capture = self._private_dir(root, "capture")
            with self.assertRaises(driver_module.TrustedDriverError):
                driver.invoke(self._invocation(runtime), capture)
            self.assertEqual(list(capture.iterdir()), [])

    def test_rotated_nested_token_cannot_enter_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            new_token = "new-access-token-0123456789"
            runtime = self._runtime(
                root,
                body=(
                    f"#!{sys.executable}\n"
                    "import json, os, pathlib, sys\n"
                    "args = sys.argv[1:]\n"
                    "auth = pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json'\n"
                    f"new_token = {new_token!r}\n"
                    "auth.write_text(json.dumps({'tokens': {'access_token': new_token}}))\n"
                    "answer = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                    "answer.write_text(new_token)\n"
                    "sys.stdin.buffer.read()\n"
                ),
            )

            class FakeNativeDriver(driver_module.TrustedCodexDriver):
                @staticmethod
                def _require_native_runtime(_path: Path) -> None:
                    return None

            configured = self._driver(root)
            auth = configured.codex_home / "auth.json"
            auth.write_text(
                json.dumps(
                    {"tokens": {"access_token": "old-access-token-0123456789"}}
                ),
                encoding="utf-8",
            )
            auth.chmod(0o600)
            driver = FakeNativeDriver(
                account_home=configured.account_home,
                codex_home=configured.codex_home,
                scratch_root=configured.scratch_root,
                sandbox_exec_path=configured.sandbox_exec_path,
                sandbox_exec_sha256=configured.sandbox_exec_sha256,
            )
            capture = self._private_dir(root, "capture")
            with self.assertRaises(driver_module.TrustedDriverError):
                driver.invoke(self._invocation(runtime), capture)
            self.assertEqual(list(capture.iterdir()), [])

    def test_child_file_size_limit_bounds_capture_during_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(
                root,
                body=(
                    f"#!{sys.executable}\n"
                    "import pathlib, sys\n"
                    "args = sys.argv[1:]\n"
                    "sys.stdin.buffer.read()\n"
                    "answer = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                    "answer.write_bytes(b'x' * 8192)\n"
                ),
            )

            class FakeNativeDriver(driver_module.TrustedCodexDriver):
                @staticmethod
                def _require_native_runtime(_path: Path) -> None:
                    return None

            configured = self._driver(root)
            driver = FakeNativeDriver(
                account_home=configured.account_home,
                codex_home=configured.codex_home,
                scratch_root=configured.scratch_root,
                sandbox_exec_path=configured.sandbox_exec_path,
                sandbox_exec_sha256=configured.sandbox_exec_sha256,
            )
            capture = self._private_dir(root, "capture")
            with mock.patch.object(driver, "_MAX_CAPTURE_BYTES", 1024):
                termination = driver.invoke(self._invocation(runtime), capture)
            self.assertNotEqual(termination.exit_code, 0)
            self.assertTrue(capture.is_dir())
            self.assertTrue(all(path.stat().st_size <= 1024 for path in capture.iterdir()))

    def test_surviving_descendant_is_killed_before_driver_returns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "child.pid"
            prompt = json.dumps({"pid_path": str(pid_path)}).encode()
            runtime = self._runtime(
                root,
                body=(
                    f"#!{sys.executable}\n"
                    "import json, os, pathlib, sys, time\n"
                    "args = sys.argv[1:]\n"
                    "config = json.loads(sys.stdin.buffer.read())\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " pathlib.Path(config['pid_path']).write_text(str(os.getpid()))\n"
                    " time.sleep(30)\n"
                    " os._exit(0)\n"
                    "answer = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                    "answer.write_text(json.dumps({'answer': 'synthetic'}))\n"
                    "print(json.dumps({'type': 'thread.started', 'thread_id': 'fake'}), flush=True)\n"
                    "print(json.dumps({'type': 'turn.started'}), flush=True)\n"
                    "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1, 'reasoning_output_tokens': 0, 'total_tokens': 2}}), flush=True)\n"
                ),
            )

            class FakeNativeDriver(driver_module.TrustedCodexDriver):
                @staticmethod
                def _require_native_runtime(_path: Path) -> None:
                    return None

            configured = self._driver(root)
            driver = FakeNativeDriver(
                account_home=configured.account_home,
                codex_home=configured.codex_home,
                scratch_root=configured.scratch_root,
                sandbox_exec_path=configured.sandbox_exec_path,
                sandbox_exec_sha256=configured.sandbox_exec_sha256,
            )
            capture = self._private_dir(root, "capture")
            invocation = self._invocation(runtime)
            invocation = executor.SealedInvocation(
                **{**invocation.__dict__, "prompt": prompt}
            )
            with self.assertRaises(driver_module.TrustedDriverError):
                driver.invoke(invocation, capture)
            deadline = time.monotonic() + 2
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pid_path.exists())
            child_pid = int(pid_path.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    @unittest.skipUnless(
        sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
        "macOS sandbox-exec is required",
    )
    def test_production_sandbox_denies_process_creation(self) -> None:
        sandbox_exec = Path("/usr/bin/sandbox-exec")
        allowed = driver_module.subprocess.run(
            [
                str(sandbox_exec),
                "-p",
                driver_module.TrustedCodexDriver._SANDBOX_PROFILE,
                "/usr/bin/true",
            ],
            check=False,
            capture_output=True,
        )
        denied = driver_module.subprocess.run(
            [
                str(sandbox_exec),
                "-p",
                driver_module.TrustedCodexDriver._SANDBOX_PROFILE,
                sys.executable,
                "-c",
                "import os; os.fork()",
            ],
            check=False,
            capture_output=True,
        )
        if allowed.returncode != 0:
            self.skipTest("sandbox-exec is blocked by the parent test sandbox")
        self.assertNotEqual(denied.returncode, 0)

    def test_reasoning_effort_is_rejected_before_witness_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            with self.assertRaises(executor.ExecutorProtocolError):
                executor.SealedInvocation(
                    phase="answer",
                    schedule_index=0,
                    prompt=b"prompt",
                    output_schema=SCHEMA,
                    model="model",
                    reasoning_effort='high"; unsafe=true',
                    runtime_path=str(runtime),
                    runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
                    timeout_seconds=1,
                )

    def test_model_must_be_bounded_argv_safe_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = self._runtime(root)
            for model in ("bad\x00model", "", "x" * 129, "-looks-like-an-option"):
                with self.subTest(model=repr(model)):
                    with self.assertRaises(executor.ExecutorProtocolError):
                        executor.SealedInvocation(
                            phase="answer",
                            schedule_index=0,
                            prompt=b"prompt",
                            output_schema=SCHEMA,
                            model=model,
                            reasoning_effort="high",
                            runtime_path=str(runtime),
                            runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
                            timeout_seconds=1,
                        )


if __name__ == "__main__":
    unittest.main()
