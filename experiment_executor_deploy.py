#!/usr/bin/env python3
"""Install and launch one fresh, externally anchored A11b executor on macOS."""

from __future__ import annotations

import argparse
import fcntl
import grp
import hashlib
import json
import os
import platform
import pwd
import resource
import signal
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import a11b_launch_protocol as launch_protocol
import a11b_postprocess
import experiment_anchor
import experiment_executor_install as install
import experiment_executor_service as service
import experiment_witness as witness


class DeploymentError(ValueError):
    """A fresh executor installation cannot be proven safe."""


INSTALL_LOCK_PATH = Path("/var/run/coralehr-experiment-executor-install.lock")
FAILURE_RECEIPT_DIR = Path("/var/log/coralehr-experiment-executor")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _decode_canonical(path: Path, payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"deployment input is invalid: {path.name}") from exc
    if not isinstance(value, dict) or service.canonical_json_line(value) != payload:
        raise DeploymentError(f"deployment input is noncanonical: {path.name}")
    return value


def _verify_file(path: Path, receipt: Mapping[str, Any]) -> dict[str, Any]:
    payload = _read_source_bytes(
        path,
        expected_receipt=receipt,
        byte_cap=int(receipt.get("bytes", -1)),
    )
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def _validate_controller_identity(controller: Mapping[str, Any]) -> None:
    try:
        launch_protocol.validate_controller_profile(controller)
    except ValueError as exc:
        raise DeploymentError("controller identity changed") from exc


def validate_inputs(
    *,
    package_root: Path,
    bundle_root: Path,
    audit_root: Path,
    credential_path: Path,
    signed_anchor_path: Path,
    anchor_url: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[Path, dict[str, Any]],
]:
    copy_receipts: dict[Path, dict[str, Any]] = {}

    def record(path: Path, receipt: Mapping[str, Any]) -> None:
        copy_receipts[path] = _verify_file(path, receipt)

    install_manifest_path = package_root / "install-manifest.json"
    install_manifest_payload = _read_source_bytes(install_manifest_path)
    try:
        manifest = install.validate_install_manifest(
            _decode_canonical(install_manifest_path, install_manifest_payload)
        )
    except install.InstallProtocolError as exc:
        raise DeploymentError("install package identity changed") from exc
    python_receipt = manifest["python_runtime"]
    code_subjects = manifest.get("code_subjects")
    if not isinstance(code_subjects, dict):
        raise DeploymentError("install code inventory changed")
    for subject in code_subjects.values():
        if not isinstance(subject, dict):
            raise DeploymentError("install code receipt changed")
        record(package_root / subject["package_path"], subject)
    transport = manifest.get("transport")
    if not isinstance(transport, dict):
        raise DeploymentError("install transport inventory changed")
    for name, package_name in (
        ("launcher", "run-experiment-executor-service"),
        ("authorized_key", "authorized_keys.entry"),
        ("sshd_drop_in", "sshd_config.drop-in"),
    ):
        record(package_root / "payload" / package_name, transport[name])

    python_root = package_root / "payload/python"
    entries = python_receipt["entries"]
    expected_python = {str(entry["path"]) for entry in entries}
    observed_python: set[str] = set()
    for path in python_root.rglob("*"):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode) or (
            not stat.S_ISDIR(status.st_mode) and not stat.S_ISREG(status.st_mode)
        ):
            raise DeploymentError("Python payload contains an unsafe entry")
        if stat.S_ISREG(status.st_mode):
            observed_python.add(path.relative_to(python_root).as_posix())
    if observed_python != expected_python:
        raise DeploymentError("Python payload inventory changed")
    for entry in entries:
        record(python_root / str(entry["path"]), entry)

    controller_path = bundle_root / "controller.json"
    controller_bytes = _read_source_bytes(controller_path)
    controller_sha = _sha256(controller_bytes)
    controller_sidecar = _read_source_bytes(bundle_root / "controller.sha256")
    if controller_sidecar != (controller_sha + "\n").encode("ascii"):
        raise DeploymentError("controller sidecar changed")
    controller = json.loads(controller_bytes)
    _validate_controller_identity(controller)
    install_manifest_receipt = {
        "sha256": _sha256(install_manifest_payload),
        "bytes": len(install_manifest_payload),
    }
    python_tree_payload = service.canonical_json_line(python_receipt)
    python_tree_receipt = {
        "sha256": _sha256(python_tree_payload),
        "bytes": len(python_tree_payload),
    }
    inputs = controller.get("inputs")
    snapshots = controller.get("snapshots")
    if (
        not isinstance(inputs, dict)
        or not isinstance(snapshots, dict)
        or inputs.get("install_manifest_sha256")
        != install_manifest_receipt["sha256"]
        or inputs.get("python_tree_receipt_sha256")
        != python_tree_receipt["sha256"]
        or {
            key: snapshots.get("install_manifest", {}).get(key)
            for key in ("sha256", "bytes")
        }
        != install_manifest_receipt
        or {
            key: snapshots.get("python_tree", {}).get(key)
            for key in ("sha256", "bytes")
        }
        != python_tree_receipt
    ):
        raise DeploymentError("controller install binding changed")
    record(bundle_root / "install-manifest.json", install_manifest_receipt)
    record(bundle_root / "python-tree-receipt.json", python_tree_receipt)
    trusted = controller["execution"]["trusted_executor"]
    package_receipts = {
        name: {"sha256": row["sha256"], "bytes": row["bytes"]}
        for name, row in code_subjects.items()
    }
    for subject in trusted["code_subjects"]:
        if package_receipts.get(subject["name"]) != {
            "sha256": subject["sha256"],
            "bytes": subject["bytes"],
        }:
            raise DeploymentError("package differs from anchored service code")
    snapshot_packages = (
        (
            ("a11_evidence_core", "a11_evidence_core"),
            ("a11b_answer_contract", "a11b_answer_contract"),
            ("a11b_postprocess", "a11b_postprocess"),
            ("a11b_successor_dev_gate", "a11b_successor_dev_gate"),
            (
                "a11b_successor_development_grading",
                "a11b_successor_development_grading",
            ),
            (
                "a11b_successor_development_postprocess",
                "a11b_successor_development_postprocess",
            ),
            ("run_lock", "run_lock"),
            ("a11b_nightly_bootstrap", "a11b_nightly_bootstrap"),
            ("a11b_nightly_runner", "a11b_nightly_runner"),
        )
        if controller["experiment_profile"] == "a11b-successor-development-v1"
        else (
            ("a11_grading", "a11b_grading"),
            ("a11b_postprocess", "a11b_postprocess"),
            ("paired_stats", "paired_stats"),
            ("panel_grade", "panel_grade"),
            ("run_a11_panel", "run_a11b_panel"),
            ("run_lock", "run_lock"),
            ("a11b_nightly_bootstrap", "a11b_nightly_bootstrap"),
            ("a11b_nightly_runner", "a11b_nightly_runner"),
        )
    )
    for snapshot_name, package_name in snapshot_packages:
        snapshot = controller["snapshots"][snapshot_name]
        if package_receipts.get(package_name) != {
            "sha256": snapshot["sha256"],
            "bytes": snapshot["bytes"],
        }:
            raise DeploymentError("package differs from anchored postprocess code")
    runtime = controller["execution"]["trusted_executor"]["runtime"]
    record(bundle_root / "codex", runtime)
    audit_manifest = a11b_postprocess._verify_audit_tree(
        audit_root,
        controller["inputs"]["audit_manifest_sha256"],
    )
    audit_manifest_path = audit_root / "manifest.json"
    audit_manifest_payload = _read_source_bytes(audit_manifest_path)
    if _sha256(audit_manifest_payload) != controller["inputs"][
        "audit_manifest_sha256"
    ]:
        raise DeploymentError("audit manifest changed after verification")
    copy_receipts[audit_manifest_path] = {
        "sha256": _sha256(audit_manifest_payload),
        "bytes": len(audit_manifest_payload),
    }
    audit_sidecar = audit_root / "manifest.sha256"
    audit_sidecar_payload = _read_source_bytes(audit_sidecar)
    if audit_sidecar_payload != (
        controller["inputs"]["audit_manifest_sha256"] + "\n"
    ).encode("ascii"):
        raise DeploymentError("audit manifest sidecar changed after verification")
    copy_receipts[audit_sidecar] = {
        "sha256": _sha256(audit_sidecar_payload),
        "bytes": len(audit_sidecar_payload),
    }
    for relative, receipt in audit_manifest["artifacts"].items():
        record(audit_root / relative, receipt)

    signed_bytes = _read_source_bytes(signed_anchor_path)
    copy_receipts[signed_anchor_path] = {
        "sha256": _sha256(signed_bytes),
        "bytes": len(signed_bytes),
    }
    experiment_anchor.verify_signed_external_anchor_receipt(
        controller_path,
        anchor_url,
        signed_bytes,
        expected_controller_sha256=controller_sha,
        expected_verifier=service.ANCHOR_CHECKER_VERIFIER,
    )
    credential_payload = _read_source_bytes(
        credential_path,
        byte_cap=1024 * 1024,
    )
    credential_status = credential_path.lstat()
    if (
        credential_path.is_symlink()
        or not stat.S_ISREG(credential_status.st_mode)
        or stat.S_IMODE(credential_status.st_mode) != 0o600
        or credential_status.st_nlink != 1
        or not 0 < credential_status.st_size <= 1024 * 1024
    ):
        raise DeploymentError("Codex credential metadata is unsafe")
    copy_receipts[credential_path] = {
        "sha256": _sha256(credential_payload),
        "bytes": len(credential_payload),
    }

    controller_receipt = {
        "sha256": controller_sha,
        "bytes": len(controller_bytes),
    }
    copy_receipts[controller_path] = controller_receipt
    sidecar_path = bundle_root / "controller.sha256"
    sidecar_payload = _read_source_bytes(sidecar_path)
    copy_receipts[sidecar_path] = {
        "sha256": _sha256(sidecar_payload),
        "bytes": len(sidecar_payload),
    }
    bundle_path = bundle_root / "bundle.json"
    bundle_payload = _read_source_bytes(bundle_path)
    try:
        bundle = json.loads(bundle_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("sealed service bundle is invalid") from exc
    if (
        not isinstance(bundle, dict)
        or service.canonical_json_line(bundle) != bundle_payload
    ):
        raise DeploymentError("sealed service bundle is noncanonical")
    commitment_path = bundle_root / "commitment.key"
    commitment_key = _read_source_bytes(commitment_path, byte_cap=32)
    if len(commitment_key) != 32:
        raise DeploymentError("commitment key must contain exactly 32 bytes")
    bundle_commitment = witness.keyed_commitment(
        commitment_key,
        domain="executor-bundle",
        payload=bundle_payload,
    )
    if service._public_binding(
        bundle,
        bundle_commitment=bundle_commitment,
    ) != trusted:
        raise DeploymentError("sealed service bundle differs from the controller")
    for path, payload in (
        (bundle_path, bundle_payload),
        (commitment_path, commitment_key),
    ):
        copy_receipts[path] = {
            "sha256": _sha256(payload),
            "bytes": len(payload),
        }
    witness_path = bundle_root / "witness_ed25519"
    witness_payload = _read_source_bytes(
        witness_path,
        byte_cap=service.MAX_CONTROL_FILE_BYTES,
    )
    copy_receipts[witness_path] = {
        "sha256": _sha256(witness_payload),
        "bytes": len(witness_payload),
    }
    snapshots_root = bundle_root / "snapshots"
    expected_snapshot_files: set[str] = set()
    for snapshot in snapshots.values():
        if not isinstance(snapshot, Mapping):
            raise DeploymentError("controller snapshot receipt changed")
        filename = Path(str(snapshot.get("snapshot_path", ""))).name
        if not filename or filename in expected_snapshot_files:
            raise DeploymentError("controller snapshot inventory changed")
        expected_snapshot_files.add(filename)
        record(snapshots_root / filename, snapshot)
    observed_snapshot_files = {
        path.name for path in snapshots_root.iterdir() if path.is_file()
    }
    if observed_snapshot_files != expected_snapshot_files:
        raise DeploymentError("controller snapshot inventory changed")
    return manifest, controller, python_receipt, copy_receipts


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
        timeout=60,
    )


@contextmanager
def _acquire_install_lock(
    path: Path = INSTALL_LOCK_PATH, *, expected_uid: int = 0
):
    """Serialize root installation and make rollback transaction-owned."""

    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise DeploymentError("installation lock is unavailable") from exc
    try:
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_nlink != 1
            or status.st_uid != expected_uid
            or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise DeploymentError("installation lock metadata is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError("another installation is already active") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _sshd_drop_in_check_command(
    package_root: Path, bundle_source: Path
) -> list[str]:
    """Validate the isolated fragment with a sealed key, without host state."""

    return [
        "/usr/sbin/sshd",
        "-t",
        "-f",
        str(package_root / "payload/sshd_config.drop-in"),
        "-h",
        str(bundle_source / "witness_ed25519"),
    ]


def _ensure_executor_account() -> tuple[int, int]:
    created = False
    record_path = f"/Users/{install.EXECUTOR_ACCOUNT}"
    try:
        try:
            account = pwd.getpwnam(install.EXECUTOR_ACCOUNT)
        except KeyError:
            listing = _run(["/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"])
            used = {
                int(line.rsplit(None, 1)[1])
                for line in listing.stdout.splitlines()
                if line.rsplit(None, 1)[-1].isdigit()
            }
            uid = next(
                (
                    candidate
                    for candidate in range(499, 400, -1)
                    if candidate not in used
                ),
                None,
            )
            if uid is None:
                raise DeploymentError("no unused system UID is available")
            records = {
                "UniqueID": str(uid),
                "PrimaryGroupID": str(grp.getgrnam("staff").gr_gid),
                "NFSHomeDirectory": str(service.PRODUCTION_BUNDLE_DIR),
                "UserShell": "/bin/sh",
                "RealName": "CoralEHR Experiment Executor",
                "IsHidden": "1",
                "AuthenticationAuthority": ";DisabledUser;",
            }
            _run(["/usr/bin/dscl", ".", "-create", record_path])
            created = True
            for field, value in records.items():
                _run(
                    [
                        "/usr/bin/dscl",
                        ".",
                        "-create",
                        record_path,
                        field,
                        value,
                    ]
                )
            account = pwd.getpwnam(install.EXECUTOR_ACCOUNT)
        if (
            account.pw_dir != str(service.PRODUCTION_BUNDLE_DIR)
            or account.pw_shell != "/bin/sh"
            or account.pw_uid == 0
            or account.pw_name in grp.getgrnam("admin").gr_mem
        ):
            raise DeploymentError(
                "executor account differs from the sealed principal"
            )
        return account.pw_uid, account.pw_gid
    except BaseException as original:
        if created:
            try:
                _run(["/usr/bin/dscl", ".", "-delete", record_path])
            except BaseException:
                original.add_note(
                    "transaction-owned executor account cleanup failed"
                )
        raise


def _mkdir(path: Path, *, mode: int, uid: int, gid: int) -> None:
    path.mkdir(mode=mode)
    os.chown(path, uid, gid)
    path.chmod(mode)


def _ensure_root_owned_directory(
    path: Path, *, mode: int, uid: int, gid: int
) -> None:
    """Create or validate one fixed transport parent without following it."""

    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise DeploymentError(f"root-owned directory is unsafe: {path.name}") from exc
    child_descriptor: int | None = None
    try:
        parent_status = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_status.st_mode)
            or parent_status.st_uid != uid
            or parent_status.st_gid != gid
            or stat.S_IMODE(parent_status.st_mode) & 0o022
        ):
            raise DeploymentError(f"root-owned directory is unsafe: {path.name}")
        try:
            child_descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            try:
                os.mkdir(path.name, mode, dir_fd=parent_descriptor)
                child_descriptor = os.open(
                    path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise DeploymentError(
                    f"root-owned directory is unsafe: {path.name}"
                ) from exc
            os.fchown(child_descriptor, uid, gid)
            os.fchmod(child_descriptor, mode)
            os.fsync(parent_descriptor)
        except OSError as exc:
            raise DeploymentError(
                f"root-owned directory is unsafe: {path.name}"
            ) from exc
        child_status = os.fstat(child_descriptor)
        if (
            not stat.S_ISDIR(child_status.st_mode)
            or child_status.st_uid != uid
            or child_status.st_gid != gid
            or stat.S_IMODE(child_status.st_mode) != mode
        ):
            raise DeploymentError(f"root-owned directory is unsafe: {path.name}")
    finally:
        if child_descriptor is not None:
            os.close(child_descriptor)
        os.close(parent_descriptor)


def _read_source_bytes(
    path: Path,
    *,
    expected_receipt: Mapping[str, Any] | None = None,
    byte_cap: int = service.MAX_BUNDLE_BYTES,
    expected_uid: int | None = None,
    expected_mode: int | None = None,
) -> bytes:
    """Read one unique regular source once and reject concurrent replacement."""

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentError(f"deployment source is unsafe: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > byte_cap
            or (expected_uid is not None and before.st_uid != expected_uid)
            or (
                expected_mode is not None
                and stat.S_IMODE(before.st_mode) != expected_mode
            )
        ):
            raise DeploymentError(f"deployment source is unsafe: {path.name}")
        payload = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            payload.extend(chunk)
            if len(payload) > byte_cap:
                raise DeploymentError(f"deployment source is oversized: {path.name}")
        after = os.fstat(descriptor)
        path_status = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (path_status.st_dev, path_status.st_ino)
            != (after.st_dev, after.st_ino)
            or len(payload) != before.st_size
        ):
            raise DeploymentError(f"deployment source changed: {path.name}")
    finally:
        os.close(descriptor)
    result = bytes(payload)
    if expected_receipt is not None and {
        "sha256": _sha256(result),
        "bytes": len(result),
    } != {
        "sha256": expected_receipt.get("sha256"),
        "bytes": expected_receipt.get("bytes"),
    }:
        raise DeploymentError(f"deployment source changed: {path.name}")
    return result


def _copy(
    path: Path,
    target: Path,
    *,
    mode: int,
    uid: int,
    gid: int,
    expected_receipt: Mapping[str, Any],
) -> None:
    payload = _read_source_bytes(
        path,
        expected_receipt=expected_receipt,
        byte_cap=int(expected_receipt.get("bytes", -1)),
    )
    _write_sealed_bytes(target, payload, mode=mode, uid=uid, gid=gid)


def _write_sealed_bytes(
    target: Path,
    payload: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    """Publish trusted bytes exclusively without following the target path."""

    try:
        parent_descriptor = os.open(
            target.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise DeploymentError(f"sealed output parent is unsafe: {target.name}") from exc
    try:
        try:
            descriptor = os.open(
                target.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise DeploymentError(f"sealed output is unsafe: {target.name}") from exc
        try:
            sent = 0
            while sent < len(payload):
                sent += os.write(descriptor, payload[sent:])
            os.fchmod(descriptor, mode)
            os.fchown(descriptor, uid, gid)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def _write_canary(path: Path, *, uid: int, gid: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    try:
        os.write(descriptor, b"sandbox-denial-canary\n")
        os.fchmod(descriptor, 0o400)
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _probe_sandbox_denials(
    *, bundle_root: Path, controller: Mapping[str, Any], uid: int, gid: int
) -> None:
    """Prove the installed model sandbox cannot read prior arms or controls."""

    results_root = bundle_root / "results"
    _mkdir(results_root, mode=0o700, uid=uid, gid=gid)
    temporary_canaries = (
        bundle_root / "state/sandbox-read-canary",
        results_root / "sandbox-read-canary",
        bundle_root / "nightly-status.json",
    )
    for path in temporary_canaries:
        _write_canary(path, uid=uid, gid=gid)
    try:
        snapshot_files = sorted((bundle_root / "snapshots").iterdir())
        if not snapshot_files:
            raise DeploymentError("sandbox snapshot probe is unavailable")
        probes = (
            bundle_root / "audit-input/manifest.json",
            temporary_canaries[0],
            temporary_canaries[1],
            snapshot_files[0],
            bundle_root / "controller.json",
            bundle_root / "bundle.json",
            bundle_root / "commitment.key",
            bundle_root / "witness_ed25519",
            bundle_root / "python-tree-receipt.json",
            bundle_root / "external-anchor-verification.json",
            bundle_root / "anchor-locator.json",
            bundle_root / "install-manifest.json",
            temporary_canaries[2],
            bundle_root / "nightly-runner.log",
        )
        sandbox = controller["execution"]["trusted_executor"]["sandbox"]
        for target in probes:
            if target.is_symlink() or not target.is_file():
                raise DeploymentError("sandbox denial probe target is unavailable")
            process = subprocess.run(
                [
                    str(sandbox["path"]),
                    "-p",
                    str(sandbox["profile"]),
                    "/bin/cat",
                    str(target),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                user=uid,
                group=gid,
                extra_groups=(),
                check=False,
            )
            if process.returncode == 0 or process.stdout:
                raise DeploymentError("model sandbox read-denial canary failed")
    finally:
        for path in temporary_canaries:
            path.unlink(missing_ok=True)
        if results_root.is_dir() and not any(results_root.iterdir()):
            results_root.rmdir()


def _await_launch_ready(
    *,
    process: subprocess.Popen[bytes],
    bundle_root: Path,
    code_root: Path,
    controller: Mapping[str, Any],
    executor_uid: int,
    root_gid: int,
    root_uid: int = 0,
    timeout_seconds: int = launch_protocol.READY_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Require zero-call child readiness, then release its launch barrier."""

    deadline = clock() + timeout_seconds
    status_path = bundle_root / "nightly-status.json"
    run_id = controller.get("run_id")
    answer_calls = controller.get("inputs", {}).get("answer_calls")
    while True:
        returncode = process.poll()
        if returncode is not None:
            raise DeploymentError(
                f"experiment child exited before readiness: {returncode}"
            )
        if status_path.exists():
            status_payload = _read_source_bytes(
                status_path,
                byte_cap=service.MAX_CONTROL_FILE_BYTES,
                expected_uid=executor_uid,
                expected_mode=0o600,
            )
            try:
                status = json.loads(status_payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DeploymentError("launch readiness status is invalid") from exc
            try:
                launch_protocol.validate_readiness(
                    status,
                    run_id=str(run_id),
                    schedule_length=int(answer_calls),
                )
            except (TypeError, ValueError) as exc:
                raise DeploymentError("experiment child readiness is invalid") from exc
            if launch_protocol.canonical_json_line(status) != status_payload:
                raise DeploymentError("experiment child readiness is invalid")
            controller_sha = _sha256(service.canonical_json_line(dict(controller)))
            acknowledgement = launch_protocol.acknowledgement(
                run_id=str(run_id),
                controller_sha256=controller_sha,
                schedule_length=int(answer_calls),
                ready_status_sha256=_sha256(status_payload),
            )
            acknowledgement_payload = launch_protocol.canonical_json_line(
                acknowledgement
            )
            _write_sealed_bytes(
                code_root / "launch-ack.json",
                acknowledgement_payload,
                mode=0o444,
                uid=root_uid,
                gid=root_gid,
            )
            expected_confirmation = launch_protocol.confirmation(
                run_id=str(run_id),
                controller_sha256=controller_sha,
                schedule_length=int(answer_calls),
                acknowledgement_sha256=launch_protocol.sha256(
                    acknowledgement_payload
                ),
            )
            confirmation_path = bundle_root / "launch-confirmation.json"
            confirmation_deadline = clock() + timeout_seconds
            while True:
                returncode = process.poll()
                if returncode is not None:
                    raise DeploymentError(
                        "experiment child exited before launch confirmation: "
                        f"{returncode}"
                    )
                if confirmation_path.exists():
                    confirmation_payload = _read_source_bytes(
                        confirmation_path,
                        byte_cap=service.MAX_CONTROL_FILE_BYTES,
                        expected_uid=executor_uid,
                        expected_mode=0o600,
                    )
                    try:
                        confirmation = json.loads(confirmation_payload)
                        launch_protocol.require_exact(
                            confirmation, expected_confirmation
                        )
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        raise DeploymentError(
                            "experiment child launch confirmation is invalid"
                        ) from exc
                    if (
                        launch_protocol.canonical_json_line(confirmation)
                        != confirmation_payload
                    ):
                        raise DeploymentError(
                            "experiment child launch confirmation is invalid"
                        )
                    commit = launch_protocol.launch_commit(
                        run_id=str(run_id),
                        controller_sha256=controller_sha,
                        schedule_length=int(answer_calls),
                        confirmation_sha256=launch_protocol.sha256(
                            confirmation_payload
                        ),
                    )
                    _write_sealed_bytes(
                        code_root / "launch-commit.json",
                        launch_protocol.canonical_json_line(commit),
                        mode=0o444,
                        uid=root_uid,
                        gid=root_gid,
                    )
                    return status
                if clock() >= confirmation_deadline:
                    raise DeploymentError(
                        "experiment child launch confirmation timed out"
                    )
                sleeper(0.1)
        if clock() >= deadline:
            raise DeploymentError("experiment child readiness timed out")
        sleeper(0.1)


def _terminate_child(process: subprocess.Popen[bytes]) -> None:
    """Stop the isolated child before the outer transaction removes its roots."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except ProcessLookupError:
        process.wait(timeout=5)


def _preserve_launch_failure(
    *, bundle_root: Path, controller: Mapping[str, Any]
) -> Path:
    """Publish one root-owned, content-free receipt outside rollback roots."""

    root_uid = os.geteuid()
    root_gid = grp.getgrnam("wheel").gr_gid
    controller_sha = _sha256(service.canonical_json_line(dict(controller)))
    receipt: dict[str, Any] = {
        "schema_version": "a11b-launch-failure-v1",
        "controller_sha256": controller_sha,
        "run_id": str(controller.get("run_id", "unavailable")),
        "stage": "bootstrap",
        "state": "failed",
        "schedule_position": 0,
        "schedule_length": int(controller.get("inputs", {}).get("answer_calls", 0)),
        "model_calls_reserved": 0,
        "model_calls_closed": 0,
    }
    try:
        executor_uid = pwd.getpwnam(install.EXECUTOR_ACCOUNT).pw_uid
        status_payload = _read_source_bytes(
            bundle_root / "nightly-status.json",
            byte_cap=service.MAX_CONTROL_FILE_BYTES,
            expected_uid=executor_uid,
            expected_mode=0o600,
        )
        status = json.loads(status_payload)
        launch_protocol.validate_readiness(
            status,
            run_id=str(controller["run_id"]),
            schedule_length=int(controller["inputs"]["answer_calls"]),
        )
        receipt.update(
            {
                "stage": str(status["stage"]),
                "schedule_position": int(status["schedule_position"]),
                "model_calls_reserved": int(status["model_calls_reserved"]),
                "model_calls_closed": int(status["model_calls_closed"]),
            }
        )
    except BaseException:
        pass
    if FAILURE_RECEIPT_DIR.exists():
        status = FAILURE_RECEIPT_DIR.lstat()
        if (
            FAILURE_RECEIPT_DIR.is_symlink()
            or not stat.S_ISDIR(status.st_mode)
            or status.st_uid != root_uid
            or stat.S_IMODE(status.st_mode) != 0o700
        ):
            raise DeploymentError("failure receipt directory is unsafe")
    else:
        _mkdir(FAILURE_RECEIPT_DIR, mode=0o700, uid=root_uid, gid=root_gid)
    path = FAILURE_RECEIPT_DIR / f"{controller_sha}.json"
    payload = service.canonical_json_line(receipt)
    if path.exists():
        existing = _read_source_bytes(
            path,
            byte_cap=service.MAX_CONTROL_FILE_BYTES,
            expected_uid=root_uid,
            expected_mode=0o444,
        )
        if existing != payload:
            raise DeploymentError("existing failure receipt binding changed")
        return path
    _write_sealed_bytes(path, payload, mode=0o444, uid=root_uid, gid=root_gid)
    return path


def _install_and_launch_unchecked(
    *,
    package_root: Path,
    bundle_source: Path,
    audit_root: Path,
    credential_path: Path,
    signed_anchor_path: Path,
    anchor_url: str,
) -> int:
    manifest, controller, python_receipt, copy_receipts = validate_inputs(
        package_root=package_root,
        bundle_root=bundle_source,
        audit_root=audit_root,
        credential_path=credential_path,
        signed_anchor_path=signed_anchor_path,
        anchor_url=anchor_url,
    )
    if os.geteuid() != 0 or platform.system() != "Darwin":
        raise DeploymentError("installation requires macOS root")
    uid, gid = _ensure_executor_account()
    wheel = grp.getgrnam("wheel").gr_gid
    code_root = service.PRODUCTION_CODE_DIR
    bundle_root = service.PRODUCTION_BUNDLE_DIR
    if code_root.exists() or bundle_root.exists():
        raise DeploymentError("fresh install paths already exist")

    code_root.parent.mkdir(parents=True, exist_ok=True)
    code_stage = code_root.with_name(code_root.name + ".installing")
    _mkdir(code_stage, mode=0o755, uid=0, gid=wheel)
    for subject in manifest["code_subjects"].values():
        _copy(
            package_root / subject["package_path"],
            code_stage / Path(subject["install_path"]).name,
            mode=0o444,
            uid=0,
            gid=wheel,
            expected_receipt=copy_receipts[
                package_root / subject["package_path"]
            ],
        )
    python_stage = code_stage / "python"
    _mkdir(python_stage, mode=0o755, uid=0, gid=wheel)
    for entry in python_receipt["entries"]:
        target = python_stage / entry["path"]
        parents = list(target.parents)
        for parent in reversed(parents[: parents.index(python_stage)]):
            if not parent.exists():
                _mkdir(parent, mode=0o755, uid=0, gid=wheel)
        _copy(
            package_root / "payload/python" / entry["path"],
            target,
            mode=int(entry["mode"], 8),
            uid=0,
            gid=wheel,
            expected_receipt=copy_receipts[
                package_root / "payload/python" / entry["path"]
            ],
        )
    _copy(
        bundle_source / "codex",
        code_stage / "codex",
        mode=0o500,
        uid=uid,
        gid=gid,
        expected_receipt=copy_receipts[bundle_source / "codex"],
    )

    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    bundle_stage = bundle_root.with_name(bundle_root.name + ".installing")
    # Keep the staging root inaccessible to the executor until every child is
    # sealed. Nested entries may carry their final ownership safely because the
    # root-owned 0700 ancestor prevents a stale executor process from racing us.
    _mkdir(bundle_stage, mode=0o700, uid=0, gid=wheel)
    for relative, mode in {
        "controller.json": 0o400,
        "controller.sha256": 0o400,
        "bundle.json": 0o400,
        "commitment.key": 0o600,
        "witness_ed25519": 0o600,
        "python-tree-receipt.json": 0o400,
        "install-manifest.json": 0o400,
    }.items():
        source = bundle_source / relative
        _copy(
            source,
            bundle_stage / relative,
            mode=mode,
            uid=uid,
            gid=gid,
            expected_receipt=copy_receipts[source],
        )
    snapshots = bundle_stage / "snapshots"
    _mkdir(snapshots, mode=0o700, uid=uid, gid=gid)
    for source in sorted((bundle_source / "snapshots").iterdir()):
        _copy(
            source,
            snapshots / source.name,
            mode=0o400,
            uid=uid,
            gid=gid,
            expected_receipt=copy_receipts[source],
        )
    installed_audit = bundle_stage / "audit-input"
    _mkdir(installed_audit, mode=0o700, uid=uid, gid=gid)
    for source in sorted(audit_root.rglob("*")):
        relative = source.relative_to(audit_root)
        target = installed_audit / relative
        if source.is_dir():
            if not target.exists():
                _mkdir(target, mode=0o700, uid=uid, gid=gid)
        else:
            _copy(
                source,
                target,
                mode=0o400,
                uid=uid,
                gid=gid,
                expected_receipt=copy_receipts[source],
            )
    for relative in (
        "state",
        "state/witness",
        "state/executor",
        "codex-home",
        "scratch",
        "scratch/service-tmp",
    ):
        target = bundle_stage / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            _mkdir(target, mode=0o700, uid=uid, gid=gid)
    _copy(
        credential_path,
        bundle_stage / "codex-home/auth.json",
        mode=0o600,
        uid=uid,
        gid=gid,
        expected_receipt=copy_receipts[credential_path],
    )
    controller_sha = copy_receipts[bundle_source / "controller.json"]["sha256"]
    locator = {
        "kind": "experiment_executor_anchor_locator",
        "schema_version": service.ANCHOR_LOCATOR_SCHEMA_VERSION,
        "anchor_url": anchor_url,
        "controller_sha256": controller_sha,
        "bundle_commitment": controller["execution"]["trusted_executor"]["bundle_commitment"],
    }
    locator_path = bundle_stage / "anchor-locator.json"
    locator_payload = service.canonical_json_line(locator)
    _write_sealed_bytes(
        locator_path,
        locator_payload,
        mode=0o400,
        uid=uid,
        gid=gid,
    )
    _copy(
        signed_anchor_path,
        bundle_stage / "external-anchor-verification.json",
        mode=0o400,
        uid=uid,
        gid=gid,
        expected_receipt=copy_receipts[signed_anchor_path],
    )
    os.chown(bundle_stage, uid, gid)
    bundle_stage.chmod(0o700)
    os.rename(code_stage, code_root)
    os.rename(bundle_stage, bundle_root)

    launcher = manifest["transport"]["launcher"]
    authorized = manifest["transport"]["authorized_key"]
    drop_in = manifest["transport"]["sshd_drop_in"]
    for parent in (
        Path(launcher["path"]).parent,
        Path(authorized["path"]).parent,
        Path(drop_in["path"]).parent,
    ):
        _ensure_root_owned_directory(parent, mode=0o755, uid=0, gid=wheel)
    for target in (Path(launcher["path"]), Path(authorized["path"]), Path(drop_in["path"])):
        if target.exists() or target.is_symlink():
            raise DeploymentError(f"transport target already exists: {target}")
    _copy(
        package_root / "payload/run-experiment-executor-service",
        Path(launcher["path"]),
        mode=0o555,
        uid=0,
        gid=wheel,
        expected_receipt=copy_receipts[
            package_root / "payload/run-experiment-executor-service"
        ],
    )
    _copy(
        package_root / "payload/authorized_keys.entry",
        Path(authorized["path"]),
        mode=0o600,
        uid=0,
        gid=wheel,
        expected_receipt=copy_receipts[
            package_root / "payload/authorized_keys.entry"
        ],
    )
    _copy(
        package_root / "payload/sshd_config.drop-in",
        Path(drop_in["path"]),
        mode=0o644,
        uid=0,
        gid=wheel,
        expected_receipt=copy_receipts[
            package_root / "payload/sshd_config.drop-in"
        ],
    )
    _run(["/usr/sbin/sshd", "-t"])

    log_path = bundle_root / "nightly-runner.log"
    log_descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchown(log_descriptor, uid, gid)

    def child_limits() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)

    try:
        _probe_sandbox_denials(
            bundle_root=bundle_root,
            controller=controller,
            uid=uid,
            gid=gid,
        )
        process = subprocess.Popen(
            [
                str(service.PRODUCTION_PYTHON_PATH),
                "-I",
                "-B",
                "-S",
                str(code_root / "a11b_nightly_bootstrap.py"),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_descriptor,
            stderr=log_descriptor,
            cwd=bundle_root,
            env=service.PRODUCTION_ENVIRONMENT,
            user=uid,
            group=gid,
            extra_groups=(),
            start_new_session=True,
            preexec_fn=child_limits,
        )
    finally:
        os.close(log_descriptor)
    try:
        _await_launch_ready(
            process=process,
            bundle_root=bundle_root,
            code_root=code_root,
            controller=controller,
            executor_uid=uid,
            root_uid=0,
            root_gid=wheel,
        )
    except BaseException:
        _terminate_child(process)
        raise
    return process.pid


def _install_and_launch_locked(
    *,
    package_root: Path,
    bundle_source: Path,
    audit_root: Path,
    credential_path: Path,
    signed_anchor_path: Path,
    anchor_url: str,
) -> int:
    """Preflight and transactionally publish one fresh installation."""

    if os.geteuid() != 0 or platform.system() != "Darwin":
        raise DeploymentError("installation requires macOS root")
    _manifest, controller, _python_receipt, _copy_receipts = validate_inputs(
        package_root=package_root,
        bundle_root=bundle_source,
        audit_root=audit_root,
        credential_path=credential_path,
        signed_anchor_path=signed_anchor_path,
        anchor_url=anchor_url,
    )
    code_root = service.PRODUCTION_CODE_DIR
    bundle_root = service.PRODUCTION_BUNDLE_DIR
    cleanup_directories = (
        code_root.with_name(code_root.name + ".installing"),
        bundle_root.with_name(bundle_root.name + ".installing"),
        code_root,
        bundle_root,
    )
    cleanup_files = (
        install.PRODUCTION_LAUNCHER_PATH,
        install.PRODUCTION_AUTHORIZED_KEYS_PATH,
        install.PRODUCTION_SSHD_DROP_IN_PATH,
    )
    for target in (*cleanup_directories, *cleanup_files):
        if target.exists() or target.is_symlink():
            raise DeploymentError(f"fresh install target already exists: {target}")
    _run(["/usr/sbin/sshd", "-t"])
    _run(_sshd_drop_in_check_command(package_root, bundle_source))
    try:
        return _install_and_launch_unchecked(
            package_root=package_root,
            bundle_source=bundle_source,
            audit_root=audit_root,
            credential_path=credential_path,
            signed_anchor_path=signed_anchor_path,
            anchor_url=anchor_url,
        )
    except BaseException as original:
        try:
            _preserve_launch_failure(
                bundle_root=bundle_root,
                controller=controller,
            )
        except BaseException:
            original.add_note("durable launch failure receipt publication failed")
        for target in reversed(cleanup_files):
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
        for target in cleanup_directories:
            if target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target)
        raise


def install_and_launch(
    *,
    package_root: Path,
    bundle_source: Path,
    audit_root: Path,
    credential_path: Path,
    signed_anchor_path: Path,
    anchor_url: str,
) -> int:
    """Serialize, preflight, publish, and launch one fresh installation."""

    if os.geteuid() != 0 or platform.system() != "Darwin":
        raise DeploymentError("installation requires macOS root")
    with _acquire_install_lock():
        return _install_and_launch_locked(
            package_root=package_root,
            bundle_source=bundle_source,
            audit_root=audit_root,
            credential_path=credential_path,
            signed_anchor_path=signed_anchor_path,
            anchor_url=anchor_url,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--bundle-source", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--credential", type=Path, required=True)
    parser.add_argument("--signed-anchor", type=Path, required=True)
    parser.add_argument("--anchor-url", required=True)
    args = parser.parse_args()
    pid = install_and_launch(
        package_root=args.package_root.resolve(),
        bundle_source=args.bundle_source.resolve(),
        audit_root=args.audit_root.resolve(),
        credential_path=args.credential.resolve(),
        signed_anchor_path=args.signed_anchor.resolve(),
        anchor_url=args.anchor_url,
    )
    print(json.dumps({"installed": True, "launched": True, "pid": pid}, sort_keys=True))


if __name__ == "__main__":
    main()
