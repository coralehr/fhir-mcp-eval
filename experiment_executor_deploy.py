#!/usr/bin/env python3
"""Install and launch one fresh, externally anchored A11b executor on macOS."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import platform
import pwd
import resource
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping

import a11b_postprocess
import experiment_anchor
import experiment_executor_install as install
import experiment_executor_service as service


class DeploymentError(ValueError):
    """A fresh executor installation cannot be proven safe."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_canonical(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"deployment input is unavailable: {path.name}")
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"deployment input is invalid: {path.name}") from exc
    if not isinstance(value, dict) or service.canonical_json_line(value) != payload:
        raise DeploymentError(f"deployment input is noncanonical: {path.name}")
    return value


def _verify_file(path: Path, receipt: Mapping[str, Any]) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise DeploymentError(f"deployment payload is unsafe: {path.name}")
    payload = path.read_bytes()
    if {"sha256": _sha256(payload), "bytes": len(payload)} != {
        "sha256": receipt.get("sha256"),
        "bytes": receipt.get("bytes"),
    }:
        raise DeploymentError(f"deployment payload changed: {path.name}")


def validate_inputs(
    *,
    package_root: Path,
    bundle_root: Path,
    audit_root: Path,
    credential_path: Path,
    signed_anchor_path: Path,
    anchor_url: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        manifest = install.validate_install_manifest(
            _read_canonical(package_root / "install-manifest.json")
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
        _verify_file(package_root / subject["package_path"], subject)
    transport = manifest.get("transport")
    if not isinstance(transport, dict):
        raise DeploymentError("install transport inventory changed")
    for name, package_name in (
        ("launcher", "run-experiment-executor-service"),
        ("authorized_key", "authorized_keys.entry"),
        ("sshd_drop_in", "sshd_config.drop-in"),
    ):
        _verify_file(package_root / "payload" / package_name, transport[name])

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
        _verify_file(python_root / str(entry["path"]), entry)

    controller_path = bundle_root / "controller.json"
    controller_bytes = controller_path.read_bytes()
    controller_sha = _sha256(controller_bytes)
    if (bundle_root / "controller.sha256").read_text(encoding="ascii") != controller_sha + "\n":
        raise DeploymentError("controller sidecar changed")
    controller = json.loads(controller_bytes)
    if (
        controller.get("experiment_profile") != "a11b-causal-isolation-v2"
        or controller.get("inputs", {}).get("answer_calls") != 1152
    ):
        raise DeploymentError("controller identity changed")
    install_manifest_receipt = {
        "sha256": _sha256((package_root / "install-manifest.json").read_bytes()),
        "bytes": (package_root / "install-manifest.json").stat().st_size,
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
    _verify_file(bundle_root / "install-manifest.json", install_manifest_receipt)
    _verify_file(bundle_root / "python-tree-receipt.json", python_tree_receipt)
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
    for snapshot_name, package_name in (
        ("a11_grading", "a11b_grading"),
        ("a11b_postprocess", "a11b_postprocess"),
        ("paired_stats", "paired_stats"),
        ("panel_grade", "panel_grade"),
        ("run_a11_panel", "run_a11b_panel"),
        ("run_lock", "run_lock"),
        ("a11b_nightly_bootstrap", "a11b_nightly_bootstrap"),
        ("a11b_nightly_runner", "a11b_nightly_runner"),
    ):
        snapshot = controller["snapshots"][snapshot_name]
        if package_receipts.get(package_name) != {
            "sha256": snapshot["sha256"],
            "bytes": snapshot["bytes"],
        }:
            raise DeploymentError("package differs from anchored postprocess code")
    runtime = controller["execution"]["trusted_executor"]["runtime"]
    _verify_file(bundle_root / "codex", runtime)
    a11b_postprocess._verify_audit_tree(
        audit_root,
        controller["inputs"]["audit_manifest_sha256"],
    )
    signed_bytes = signed_anchor_path.read_bytes()
    experiment_anchor.verify_signed_external_anchor_receipt(
        controller_path,
        anchor_url,
        signed_bytes,
        expected_controller_sha256=controller_sha,
        expected_verifier=service.ANCHOR_CHECKER_VERIFIER,
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
    return manifest, controller, python_receipt


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
        timeout=60,
    )


def _ensure_executor_account() -> tuple[int, int]:
    try:
        account = pwd.getpwnam(install.EXECUTOR_ACCOUNT)
    except KeyError:
        listing = _run(["/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"])
        used = {
            int(line.rsplit(None, 1)[1])
            for line in listing.stdout.splitlines()
            if line.rsplit(None, 1)[-1].isdigit()
        }
        uid = next((candidate for candidate in range(499, 400, -1) if candidate not in used), None)
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
        _run(["/usr/bin/dscl", ".", "-create", f"/Users/{install.EXECUTOR_ACCOUNT}"])
        for field, value in records.items():
            _run(
                [
                    "/usr/bin/dscl",
                    ".",
                    "-create",
                    f"/Users/{install.EXECUTOR_ACCOUNT}",
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
        raise DeploymentError("executor account differs from the sealed principal")
    return account.pw_uid, account.pw_gid


def _mkdir(path: Path, *, mode: int, uid: int, gid: int) -> None:
    path.mkdir(mode=mode)
    os.chown(path, uid, gid)
    path.chmod(mode)


def _copy(path: Path, target: Path, *, mode: int, uid: int, gid: int) -> None:
    payload = path.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        sent = 0
        while sent < len(payload):
            sent += os.write(descriptor, payload[sent:])
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


def _install_and_launch_unchecked(
    *,
    package_root: Path,
    bundle_source: Path,
    audit_root: Path,
    credential_path: Path,
    signed_anchor_path: Path,
    anchor_url: str,
) -> int:
    manifest, controller, python_receipt = validate_inputs(
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
        )
    _copy(bundle_source / "codex", code_stage / "codex", mode=0o500, uid=uid, gid=gid)

    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    bundle_stage = bundle_root.with_name(bundle_root.name + ".installing")
    _mkdir(bundle_stage, mode=0o700, uid=uid, gid=gid)
    for relative, mode in {
        "controller.json": 0o400,
        "controller.sha256": 0o400,
        "bundle.json": 0o400,
        "commitment.key": 0o600,
        "witness_ed25519": 0o600,
        "python-tree-receipt.json": 0o400,
        "install-manifest.json": 0o400,
    }.items():
        _copy(bundle_source / relative, bundle_stage / relative, mode=mode, uid=uid, gid=gid)
    snapshots = bundle_stage / "snapshots"
    _mkdir(snapshots, mode=0o700, uid=uid, gid=gid)
    for source in sorted((bundle_source / "snapshots").iterdir()):
        _copy(source, snapshots / source.name, mode=0o400, uid=uid, gid=gid)
    installed_audit = bundle_stage / "audit-input"
    _mkdir(installed_audit, mode=0o700, uid=uid, gid=gid)
    for source in sorted(audit_root.rglob("*")):
        relative = source.relative_to(audit_root)
        target = installed_audit / relative
        if source.is_dir():
            if not target.exists():
                _mkdir(target, mode=0o700, uid=uid, gid=gid)
        else:
            _copy(source, target, mode=0o400, uid=uid, gid=gid)
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
    _copy(credential_path, bundle_stage / "codex-home/auth.json", mode=0o600, uid=uid, gid=gid)
    controller_sha = _sha256((bundle_source / "controller.json").read_bytes())
    locator = {
        "kind": "experiment_executor_anchor_locator",
        "schema_version": service.ANCHOR_LOCATOR_SCHEMA_VERSION,
        "anchor_url": anchor_url,
        "controller_sha256": controller_sha,
        "bundle_commitment": controller["execution"]["trusted_executor"]["bundle_commitment"],
    }
    locator_path = bundle_stage / "anchor-locator.json"
    locator_payload = service.canonical_json_line(locator)
    temporary_locator = bundle_source / ".anchor-locator.installing"
    temporary_locator.write_bytes(locator_payload)
    try:
        _copy(temporary_locator, locator_path, mode=0o400, uid=uid, gid=gid)
    finally:
        temporary_locator.unlink(missing_ok=True)
    _copy(
        signed_anchor_path,
        bundle_stage / "external-anchor-verification.json",
        mode=0o400,
        uid=uid,
        gid=gid,
    )
    os.rename(code_stage, code_root)
    os.rename(bundle_stage, bundle_root)

    launcher = manifest["transport"]["launcher"]
    authorized = manifest["transport"]["authorized_key"]
    drop_in = manifest["transport"]["sshd_drop_in"]
    for target in (Path(launcher["path"]), Path(authorized["path"]), Path(drop_in["path"])):
        if target.exists() or target.is_symlink():
            raise DeploymentError(f"transport target already exists: {target}")
    _copy(
        package_root / "payload/run-experiment-executor-service",
        Path(launcher["path"]),
        mode=0o555,
        uid=0,
        gid=wheel,
    )
    Path(authorized["path"]).parent.mkdir(parents=True, mode=0o755)
    _copy(
        package_root / "payload/authorized_keys.entry",
        Path(authorized["path"]),
        mode=0o600,
        uid=0,
        gid=wheel,
    )
    _copy(
        package_root / "payload/sshd_config.drop-in",
        Path(drop_in["path"]),
        mode=0o644,
        uid=0,
        gid=wheel,
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
    return process.pid


def install_and_launch(
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
    validate_inputs(
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
    _run(
        [
            "/usr/sbin/sshd",
            "-t",
            "-f",
            str(package_root / "payload/sshd_config.drop-in"),
        ]
    )
    try:
        return _install_and_launch_unchecked(
            package_root=package_root,
            bundle_source=bundle_source,
            audit_root=audit_root,
            credential_path=credential_path,
            signed_anchor_path=signed_anchor_path,
            anchor_url=anchor_url,
        )
    except BaseException:
        for target in reversed(cleanup_files):
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
        for target in cleanup_directories:
            if target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target)
        raise


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
