from __future__ import annotations

import contextlib
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_launch_protocol as launch_protocol
import experiment_executor_deploy as deploy
import experiment_executor_service as service


class ExperimentExecutorDeployTests(unittest.TestCase):
    def test_root_transport_parent_creation_is_retry_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "transport"

            deploy._ensure_root_owned_directory(
                parent,
                mode=0o755,
                uid=os.getuid(),
                gid=os.getgid(),
            )
            deploy._ensure_root_owned_directory(
                parent,
                mode=0o755,
                uid=os.getuid(),
                gid=os.getgid(),
            )

            status = parent.lstat()
            self.assertTrue(parent.is_dir())
            self.assertEqual(status.st_uid, os.getuid())
            self.assertEqual(status.st_gid, os.getgid())
            self.assertEqual(status.st_mode & 0o777, 0o755)

    def test_root_transport_parent_rejects_unsafe_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected"
            protected.mkdir()
            parent = root / "transport"
            parent.symlink_to(protected, target_is_directory=True)

            with self.assertRaisesRegex(deploy.DeploymentError, "directory is unsafe"):
                deploy._ensure_root_owned_directory(
                    parent,
                    mode=0o755,
                    uid=os.getuid(),
                    gid=os.getgid(),
                )

    def test_complete_staging_path_uses_production_helper_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_root = root / "package"
            bundle_source = root / "source-bundle"
            audit_root = root / "audit"
            for path in (package_root, bundle_source / "snapshots", audit_root):
                path.mkdir(parents=True)
            credential_path = root / "auth.json"
            signed_anchor_path = root / "anchor.json"
            code_root = root / "installed-code"
            bundle_root = root / "installed-bundle"
            launcher_path = root / "transport" / "launcher"
            authorized_path = root / "transport" / "authorized_keys"
            drop_in_path = root / "transport" / "sshd.conf"
            package_subject = package_root / "payload/service.py"
            transport_sources = [
                package_root / "payload/run-experiment-executor-service",
                package_root / "payload/authorized_keys.entry",
                package_root / "payload/sshd_config.drop-in",
            ]
            bundle_sources = [
                bundle_source / relative
                for relative in (
                    "codex",
                    "controller.json",
                    "controller.sha256",
                    "bundle.json",
                    "commitment.key",
                    "witness_ed25519",
                    "python-tree-receipt.json",
                    "install-manifest.json",
                )
            ]
            receipt = {"sha256": "0" * 64, "bytes": 0}
            copy_receipts = {
                path: receipt
                for path in (
                    package_subject,
                    *transport_sources,
                    *bundle_sources,
                    credential_path,
                    signed_anchor_path,
                )
            }
            manifest = {
                "code_subjects": {
                    "service": {
                        "package_path": "payload/service.py",
                        "install_path": "/usr/local/lib/service.py",
                    }
                },
                "transport": {
                    "launcher": {"path": str(launcher_path)},
                    "authorized_key": {"path": str(authorized_path)},
                    "sshd_drop_in": {"path": str(drop_in_path)},
                },
            }
            controller = {
                "run_id": "a" * 64,
                "inputs": {"answer_calls": 192},
                "execution": {
                    "trusted_executor": {"bundle_commitment": "b" * 64}
                },
            }
            process = mock.Mock(pid=4321)

            def strict_copy(
                _source: Path,
                _target: Path,
                *,
                mode: int,
                uid: int,
                gid: int,
                expected_receipt: dict[str, object],
            ) -> None:
                self.assertIsInstance(mode, int)
                self.assertIsInstance(uid, int)
                self.assertIsInstance(gid, int)
                self.assertIs(expected_receipt, receipt)

            def local_mkdir(path: Path, *, mode: int, uid: int, gid: int) -> None:
                self.assertIsInstance(uid, int)
                self.assertIsInstance(gid, int)
                path.mkdir(mode=mode)

            with mock.patch.object(
                deploy,
                "validate_inputs",
                return_value=(
                    manifest,
                    controller,
                    {"entries": []},
                    copy_receipts,
                ),
            ), mock.patch.object(deploy.os, "geteuid", return_value=0), mock.patch.object(
                deploy.platform, "system", return_value="Darwin"
            ), mock.patch.object(
                deploy, "_ensure_executor_account", return_value=(os.getuid(), os.getgid())
            ), mock.patch.object(
                deploy.grp, "getgrnam", return_value=mock.Mock(gr_gid=os.getgid())
            ), mock.patch.object(
                deploy.service, "PRODUCTION_CODE_DIR", code_root
            ), mock.patch.object(
                deploy.service, "PRODUCTION_BUNDLE_DIR", bundle_root
            ), mock.patch.object(
                deploy.service, "PRODUCTION_PYTHON_PATH", root / "python"
            ), mock.patch.object(
                deploy, "_copy", side_effect=strict_copy
            ), mock.patch.object(
                deploy, "_mkdir", side_effect=local_mkdir
            ), mock.patch.object(
                deploy, "_ensure_root_owned_directory"
            ), mock.patch.object(
                deploy, "_run"
            ), mock.patch.object(
                deploy, "_probe_sandbox_denials"
            ), mock.patch.object(
                deploy.subprocess, "Popen", return_value=process
            ), mock.patch.object(
                deploy, "_await_launch_ready"
            ) as await_ready:
                pid = deploy._install_and_launch_unchecked(
                    package_root=package_root,
                    bundle_source=bundle_source,
                    audit_root=audit_root,
                    credential_path=credential_path,
                    signed_anchor_path=signed_anchor_path,
                    anchor_url="https://example.test/anchor.json",
                )

            self.assertEqual(pid, 4321)
            await_ready.assert_called_once_with(
                process=process,
                bundle_root=bundle_root,
                code_root=code_root,
                controller=controller,
                executor_uid=os.getuid(),
                root_uid=0,
                root_gid=os.getgid(),
            )

    def test_controller_identity_accepts_only_registered_profile_call_pairs(self) -> None:
        deploy._validate_controller_identity(
            {
                "experiment_profile": "a11b-successor-development-v1",
                "inputs": {"answer_calls": 192},
            }
        )
        for profile, answer_calls in (
            ("a11b-successor-development-v1", 1152),
            ("a11b-causal-isolation-v2", 192),
            ("unregistered", 192),
        ):
            with self.subTest(profile=profile, answer_calls=answer_calls), self.assertRaises(
                deploy.DeploymentError
            ):
                deploy._validate_controller_identity(
                    {
                        "experiment_profile": profile,
                        "inputs": {"answer_calls": answer_calls},
                    }
                )

    def test_install_lock_rejects_a_concurrent_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "install.lock"
            with deploy._acquire_install_lock(
                lock_path, expected_uid=os.getuid()
            ), self.assertRaisesRegex(deploy.DeploymentError, "already active"):
                with deploy._acquire_install_lock(
                    lock_path, expected_uid=os.getuid()
                ):
                    self.fail("concurrent installer acquired the lock")

    def test_partial_executor_account_is_deleted_before_retry(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str]) -> object:
            calls.append(command)
            if command[2:] == ["-list", "/Users", "UniqueID"]:
                return mock.Mock(stdout="root 0\n")
            if command[-2:] == ["UniqueID", "499"]:
                raise deploy.subprocess.CalledProcessError(1, command)
            return mock.Mock(stdout="")

        with mock.patch.object(
            deploy.pwd, "getpwnam", side_effect=KeyError("absent")
        ), mock.patch.object(
            deploy.grp, "getgrnam", return_value=mock.Mock(gr_gid=os.getgid(), gr_mem=[])
        ), mock.patch.object(
            deploy, "_run", side_effect=run
        ), self.assertRaises(deploy.subprocess.CalledProcessError):
            deploy._ensure_executor_account()

        self.assertIn(
            ["/usr/bin/dscl", ".", "-delete", "/Users/_coralexp"],
            calls,
        )

    def test_invalid_new_executor_account_is_deleted_before_retry(self) -> None:
        calls: list[list[str]] = []
        account = mock.Mock(
            pw_dir="/unexpected",
            pw_shell="/bin/sh",
            pw_uid=499,
            pw_gid=os.getgid(),
            pw_name="_coralexp",
        )

        def run(command: list[str]) -> object:
            calls.append(command)
            return mock.Mock(stdout="root 0\n" if command[2] == "-list" else "")

        with mock.patch.object(
            deploy.pwd, "getpwnam", side_effect=(KeyError("absent"), account)
        ), mock.patch.object(
            deploy.grp,
            "getgrnam",
            return_value=mock.Mock(gr_gid=os.getgid(), gr_mem=[]),
        ), mock.patch.object(
            deploy, "_run", side_effect=run
        ), self.assertRaisesRegex(deploy.DeploymentError, "sealed principal"):
            deploy._ensure_executor_account()

        self.assertIn(
            ["/usr/bin/dscl", ".", "-delete", "/Users/_coralexp"],
            calls,
        )

    def test_readiness_failure_terminates_the_waiting_child_before_rollback(self) -> None:
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.return_value = 0

        with mock.patch.object(deploy.os, "killpg") as killpg:
            deploy._terminate_child(process)

        killpg.assert_called_once_with(12345, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=5)

    def test_launch_failure_receipt_survives_outside_transaction_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt_root = root / "durable-receipts"
            with mock.patch.object(
                deploy, "FAILURE_RECEIPT_DIR", receipt_root
            ), mock.patch.object(
                deploy.grp, "getgrnam", return_value=mock.Mock(gr_gid=os.getgid())
            ), mock.patch.object(
                deploy.pwd, "getpwnam", side_effect=KeyError("absent")
            ):
                receipt_path = deploy._preserve_launch_failure(
                    bundle_root=root / "rolled-back-bundle",
                    controller={
                        "run_id": "a" * 64,
                        "inputs": {"answer_calls": 192},
                    },
                )

            receipt = json.loads(receipt_path.read_bytes())
            self.assertEqual(receipt["state"], "failed")
            self.assertEqual(receipt["schedule_length"], 192)
            self.assertEqual(receipt["model_calls_reserved"], 0)
            self.assertTrue(receipt_path.is_relative_to(receipt_root))

    def test_failed_launch_removes_only_fresh_transaction_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root = root / "code"
            bundle_root = root / "bundle"
            launcher = root / "launcher"
            authorized = root / "authorized_keys"
            drop_in = root / "sshd.conf"

            def fail_after_publication(**_kwargs: object) -> int:
                for path in (
                    code_root,
                    bundle_root,
                    code_root.with_name("code.installing"),
                    bundle_root.with_name("bundle.installing"),
                ):
                    path.mkdir()
                    (path / "owned-by-transaction").write_text(
                        "sealed", encoding="utf-8"
                    )
                for path in (launcher, authorized, drop_in):
                    path.write_text("sealed", encoding="utf-8")
                raise deploy.DeploymentError("child failed")

            with mock.patch.object(deploy.os, "geteuid", return_value=0), mock.patch.object(
                deploy.platform, "system", return_value="Darwin"
            ), mock.patch.object(
                deploy,
                "validate_inputs",
                return_value=(
                    {},
                    {"run_id": "a" * 64, "inputs": {"answer_calls": 192}},
                    {},
                    {},
                ),
            ), mock.patch.object(
                deploy, "_run"
            ), mock.patch.object(
                deploy, "_acquire_install_lock", return_value=contextlib.nullcontext()
            ), mock.patch.object(
                deploy, "_install_and_launch_unchecked", side_effect=fail_after_publication
            ), mock.patch.object(
                deploy, "_preserve_launch_failure"
            ), mock.patch.object(
                deploy.service, "PRODUCTION_CODE_DIR", code_root
            ), mock.patch.object(
                deploy.service, "PRODUCTION_BUNDLE_DIR", bundle_root
            ), mock.patch.object(
                deploy.install, "PRODUCTION_LAUNCHER_PATH", launcher
            ), mock.patch.object(
                deploy.install, "PRODUCTION_AUTHORIZED_KEYS_PATH", authorized
            ), mock.patch.object(
                deploy.install, "PRODUCTION_SSHD_DROP_IN_PATH", drop_in
            ):
                with self.assertRaisesRegex(deploy.DeploymentError, "child failed"):
                    deploy.install_and_launch(
                        package_root=root / "package",
                        bundle_source=root / "source",
                        audit_root=root / "audit",
                        credential_path=root / "credential",
                        signed_anchor_path=root / "anchor",
                        anchor_url=(
                            "https://api.github.com/repos/coralehr/fhir-mcp-eval/"
                            "contents/anchor"
                        ),
                    )

            for target in (
                code_root,
                bundle_root,
                code_root.with_name("code.installing"),
                bundle_root.with_name("bundle.installing"),
                launcher,
                authorized,
                drop_in,
            ):
                self.assertFalse(target.exists())

    def test_sealed_write_does_not_follow_a_malicious_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected"
            protected.write_bytes(b"must-survive")
            target = root / "anchor-locator.json"
            target.symlink_to(protected)

            with self.assertRaisesRegex(deploy.DeploymentError, "sealed output"):
                deploy._write_sealed_bytes(
                    target,
                    b"attacker-controlled",
                    mode=0o400,
                    uid=os.getuid(),
                    gid=os.getgid(),
                )

            self.assertEqual(protected.read_bytes(), b"must-survive")

    def test_sealed_write_does_not_follow_a_malicious_parent_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected"
            protected.mkdir()
            parent = root / "attacker-parent"
            parent.symlink_to(protected, target_is_directory=True)

            with self.assertRaisesRegex(deploy.DeploymentError, "parent is unsafe"):
                deploy._write_sealed_bytes(
                    parent / "root-owned-output",
                    b"attacker-controlled",
                    mode=0o400,
                    uid=os.getuid(),
                    gid=os.getgid(),
                )

            self.assertFalse((protected / "root-owned-output").exists())

    def test_copy_rechecks_source_bytes_at_the_publication_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "validated-input"
            source.write_bytes(b"validated")
            receipt = {
                "sha256": deploy._sha256(source.read_bytes()),
                "bytes": source.stat().st_size,
            }
            source.write_bytes(b"substituted")
            target = root / "sealed-output"

            with self.assertRaisesRegex(deploy.DeploymentError, "unsafe|changed"):
                deploy._copy(
                    source,
                    target,
                    mode=0o400,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    expected_receipt=receipt,
                )

            self.assertFalse(target.exists())

    def test_copy_rejects_source_path_replacement_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "validated-input"
            source.write_bytes(b"validated")
            receipt = {
                "sha256": deploy._sha256(b"validated"),
                "bytes": len(b"validated"),
            }
            target = root / "sealed-output"
            real_read = deploy.os.read
            replaced = False

            def replace_after_first_read(descriptor: int, count: int) -> bytes:
                nonlocal replaced
                chunk = real_read(descriptor, count)
                if chunk and not replaced:
                    replaced = True
                    source.unlink()
                    source.write_bytes(b"validated")
                return chunk

            with mock.patch.object(
                deploy.os, "read", side_effect=replace_after_first_read
            ), self.assertRaisesRegex(deploy.DeploymentError, "changed"):
                deploy._copy(
                    source,
                    target,
                    mode=0o400,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    expected_receipt=receipt,
                )

            self.assertTrue(replaced)
            self.assertFalse(target.exists())

    def test_launch_readiness_rejects_an_immediate_child_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root = root / "code"
            code_root.mkdir()
            process = mock.Mock()
            process.poll.return_value = 17

            with self.assertRaisesRegex(deploy.DeploymentError, "exited before"):
                deploy._await_launch_ready(
                    process=process,
                    bundle_root=root,
                    code_root=code_root,
                    controller={
                        "run_id": "1" * 64,
                        "inputs": {"answer_calls": 192},
                    },
                    executor_uid=os.getuid(),
                    root_uid=os.getuid(),
                    root_gid=os.getgid(),
                    timeout_seconds=1,
                    clock=lambda: 0.0,
                    sleeper=lambda _seconds: None,
                )

    def test_launch_readiness_requires_a_bound_zero_call_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root = root / "code"
            code_root.mkdir()
            status = {
                "schema_version": "a11b-nightly-status-v1",
                "run_id": "1" * 64,
                "stage": "answers",
                "state": "active",
                "schedule_position": 0,
                "schedule_length": 192,
                "model_calls_reserved": 0,
                "model_calls_closed": 0,
                "updated_at": "2026-07-18T00:00:00Z",
            }
            status_path = root / "nightly-status.json"
            status_path.write_bytes(
                service.canonical_json_line(status)
            )
            status_path.chmod(0o600)
            process = mock.Mock()
            process.poll.return_value = None

            def confirm_launch(_seconds: float) -> None:
                ack_payload = (code_root / "launch-ack.json").read_bytes()
                acknowledgement = json.loads(ack_payload)
                confirmation = launch_protocol.confirmation(
                    run_id="1" * 64,
                    controller_sha256=acknowledgement["controller_sha256"],
                    schedule_length=192,
                    acknowledgement_sha256=launch_protocol.sha256(ack_payload),
                )
                confirmation_path = root / "launch-confirmation.json"
                confirmation_path.write_bytes(
                    launch_protocol.canonical_json_line(confirmation)
                )
                confirmation_path.chmod(0o600)

            observed = deploy._await_launch_ready(
                process=process,
                bundle_root=root,
                code_root=code_root,
                controller={
                    "run_id": "1" * 64,
                    "inputs": {"answer_calls": 192},
                },
                executor_uid=os.getuid(),
                root_uid=os.getuid(),
                root_gid=os.getgid(),
                timeout_seconds=1,
                clock=lambda: 0.0,
                sleeper=confirm_launch,
            )

            self.assertEqual(observed, status)
            self.assertTrue((code_root / "launch-ack.json").is_file())
            self.assertTrue((root / "launch-confirmation.json").is_file())
            self.assertTrue((code_root / "launch-commit.json").is_file())

    def test_launch_readiness_rejects_nonzero_calls_without_acknowledging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_root = root / "code"
            code_root.mkdir()
            status = launch_protocol.readiness_status(
                run_id="1" * 64,
                schedule_length=192,
                updated_at="2026-07-18T00:00:00Z",
            )
            status["model_calls_reserved"] = 1
            status_path = root / "nightly-status.json"
            status_path.write_bytes(launch_protocol.canonical_json_line(status))
            status_path.chmod(0o600)
            process = mock.Mock()
            process.poll.return_value = None

            with self.assertRaisesRegex(deploy.DeploymentError, "readiness is invalid"):
                deploy._await_launch_ready(
                    process=process,
                    bundle_root=root,
                    code_root=code_root,
                    controller={
                        "run_id": "1" * 64,
                        "inputs": {"answer_calls": 192},
                    },
                    executor_uid=os.getuid(),
                    root_uid=os.getuid(),
                    root_gid=os.getgid(),
                    timeout_seconds=1,
                    clock=lambda: 0.0,
                    sleeper=lambda _seconds: None,
                )

            self.assertFalse((code_root / "launch-ack.json").exists())


if __name__ == "__main__":
    unittest.main()
