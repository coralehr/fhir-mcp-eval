#!/usr/bin/env python3
"""Deterministically compile the zero-model executor installation surface.

This module does not create accounts, edit sshd, install credentials, or invoke
the model.  It emits an immutable review package whose receipts can be bound by
a successor controller before a separately authorized root installation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import experiment_executor_service as service


INSTALL_SCHEMA_VERSION = "experiment-executor-install-package-v1"
PYTHON_TREE_SCHEMA_VERSION = "experiment-python-tree-v1"
EXECUTOR_ACCOUNT = "_coralexp"
RUN_ACCOUNT = "cory"
PRODUCTION_PYTHON_ROOT = service.PRODUCTION_CODE_DIR / "python"
PINNED_PYTHON_VERSION = "Python 3.14.5"
PRODUCTION_LAUNCHER_PATH = Path(
    "/usr/local/libexec/coralehr-experiment-executor"
)
PRODUCTION_AUTHORIZED_KEYS_PATH = Path(
    "/etc/ssh/coralehr-experiment-executor/authorized_keys"
)
PRODUCTION_SSHD_DROP_IN_PATH = Path(
    "/etc/ssh/sshd_config.d/200-coralehr-experiment-executor.conf"
)
_CODE_FILES = {
    "anchor": "experiment_anchor.py",
    "bootstrap": "experiment_executor_bootstrap.py",
    "codex_harness": "codex_harness.py",
    "driver": "trusted_codex_driver.py",
    "executor": "experiment_executor.py",
    "service": "experiment_executor_service.py",
    "witness": "experiment_witness.py",
}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_PYTHON_ENTRY_FIELDS = {
    "path",
    "sha256",
    "bytes",
    "mode",
    "owner",
    "group",
    "links",
    "format",
    "dependencies",
}


class InstallProtocolError(ValueError):
    """The zero-model installation package is unsafe or noncanonical."""


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _receipt(payload: bytes) -> dict[str, object]:
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def _normalize_runner_public_key(value: object) -> str:
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise InstallProtocolError("runner public key is invalid")
    fields = value.split()
    if len(fields) not in {2, 3} or fields[0] != "ssh-ed25519":
        raise InstallProtocolError("runner public key is invalid")
    if len(fields) == 3 and any(character in fields[2] for character in '"\\'):
        raise InstallProtocolError("runner public key comment is invalid")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as exc:
        raise InstallProtocolError("runner public key is invalid") from exc
    prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if len(blob) != len(prefix) + 32 or not blob.startswith(prefix):
        raise InstallProtocolError("runner public key is invalid")
    return " ".join(fields[:2])


def render_launcher() -> bytes:
    lines = [
        "#!/bin/sh",
        "exec 2>/dev/null",
        "set -efu",
        "umask 077",
        "ulimit -S -c 0 || exit 111",
        "ulimit -H -c 0 || exit 111",
        f"cd '{service.PRODUCTION_BUNDLE_DIR}' || exit 111",
        "exec /usr/bin/env -i \\",
    ]
    for key, value in service.PRODUCTION_ENVIRONMENT.items():
        lines.append(f"  {key}='{value}' \\")
    lines.extend(
        [
            f"  '{service.PRODUCTION_PYTHON_PATH}' -I -B -S \\",
            f"  '{service.PRODUCTION_BOOTSTRAP_PATH}'",
        ]
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def render_authorized_key(runner_public_key: object) -> bytes:
    public_key = _normalize_runner_public_key(runner_public_key)
    return (
        f'from="127.0.0.1,::1",restrict,command="{PRODUCTION_LAUNCHER_PATH}" '
        f"{public_key} coralehr-experiment-rpc\n"
    ).encode("ascii")


def render_sshd_drop_in() -> bytes:
    return (
        f"Match User {EXECUTOR_ACCOUNT}\n"
        "    AuthenticationMethods publickey\n"
        "    PasswordAuthentication no\n"
        "    KbdInteractiveAuthentication no\n"
        "    PubkeyAuthentication yes\n"
        f"    AuthorizedKeysFile {PRODUCTION_AUTHORIZED_KEYS_PATH}\n"
        f"    ForceCommand {PRODUCTION_LAUNCHER_PATH}\n"
        "    DisableForwarding yes\n"
        "    PermitTTY no\n"
        "    PermitTunnel no\n"
        "    PermitUserRC no\n"
        "    MaxSessions 1\n"
        "Match all\n"
    ).encode("ascii")


def _python_tree_digest(entries: list[dict[str, object]]) -> str:
    return _sha256(
        _canonical_json_bytes(
            {
                "schema_version": PYTHON_TREE_SCHEMA_VERSION,
                "root": str(PRODUCTION_PYTHON_ROOT),
                "entries": entries,
            }
        )
    )


def _python_runtime_receipt(value: object) -> dict[str, object]:
    fields = {
        "schema_version",
        "root",
        "executable",
        "tree_sha256",
        "files",
        "bytes",
        "version",
        "entries",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise InstallProtocolError("Python tree receipt fields are invalid")
    root = value.get("root")
    executable = value.get("executable")
    files = value.get("files")
    size = value.get("bytes")
    version = value.get("version")
    entries = value.get("entries")
    if (
        value.get("schema_version") != PYTHON_TREE_SCHEMA_VERSION
        or root != str(PRODUCTION_PYTHON_ROOT)
        or executable != str(service.PRODUCTION_PYTHON_PATH)
        or _HEX_64.fullmatch(str(value.get("tree_sha256") or "")) is None
        or type(files) is not int
        or files <= 0
        or type(size) is not int
        or size <= 0
        or version != PINNED_PYTHON_VERSION
        or not isinstance(entries, list)
        or len(entries) != files
    ):
        raise InstallProtocolError("Python tree receipt is invalid")

    normalized_entries: list[dict[str, object]] = []
    prior_path = ""
    entry_paths: set[str] = set()
    collapsed_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != _PYTHON_ENTRY_FIELDS:
            raise InstallProtocolError("Python tree entry fields are invalid")
        path = entry.get("path")
        path_parts = PurePosixPath(str(path)).parts
        dependencies = entry.get("dependencies")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or path_parts in {(), (".",)}
            or any(part in {"", ".", ".."} for part in path_parts)
            or PurePosixPath(path).as_posix() != path
            or path <= prior_path
            or _HEX_64.fullmatch(str(entry.get("sha256") or "")) is None
            or type(entry.get("bytes")) is not int
            or int(entry["bytes"]) < 0
            or entry.get("mode") not in {"0444", "0555"}
            or entry.get("owner") != "root"
            or entry.get("group") != "wheel"
            or entry.get("links") != 1
            or entry.get("format") not in {"data", "macho"}
            or not isinstance(dependencies, list)
            or not all(isinstance(item, str) for item in dependencies)
            or dependencies != sorted(set(dependencies))
        ):
            raise InstallProtocolError("Python tree entry is invalid")
        # The install target is case- and normalization-insensitive APFS, so
        # two distinct-but-canonical strings (Lib/x vs lib/x, or NFC vs NFD)
        # would still resolve to one on-disk path with contradictory reviewed
        # receipts. Reject any entry that collapses onto an already-seen path.
        collapsed = unicodedata.normalize("NFC", path).casefold()
        if collapsed in collapsed_paths:
            raise InstallProtocolError("Python tree entry path collides")
        collapsed_paths.add(collapsed)
        if entry.get("format") == "data" and dependencies:
            raise InstallProtocolError("Python data entry has dependencies")
        for dependency in dependencies:
            dependency_path = PurePosixPath(dependency)
            if (
                not dependency.startswith("/")
                or str(dependency_path) != dependency
                or any(part in {".", ".."} for part in dependency_path.parts)
            ):
                raise InstallProtocolError("Python dependency is invalid")
            if not (
                dependency.startswith("/usr/lib/")
                or dependency.startswith("/System/Library/")
                or dependency.startswith(str(PRODUCTION_PYTHON_ROOT) + "/")
            ):
                raise InstallProtocolError("Python dependency escapes closure")
        normalized = dict(entry)
        normalized_entries.append(normalized)
        prior_path = path
        entry_paths.add(path)

    executable_relative = service.PRODUCTION_PYTHON_PATH.relative_to(
        PRODUCTION_PYTHON_ROOT
    ).as_posix()
    by_path = {str(entry["path"]): entry for entry in normalized_entries}
    executable_entry = by_path.get(executable_relative)
    if (
        executable_entry is None
        or executable_entry["format"] != "macho"
        or executable_entry["mode"] != "0555"
        or not executable_entry["dependencies"]
        or sum(int(entry["bytes"]) for entry in normalized_entries) != size
        or _python_tree_digest(normalized_entries) != value.get("tree_sha256")
    ):
        raise InstallProtocolError("Python tree receipt is invalid")
    prefix = str(PRODUCTION_PYTHON_ROOT) + "/"
    for entry in normalized_entries:
        for dependency in entry["dependencies"]:
            if dependency.startswith(prefix):
                relative_dependency = dependency[len(prefix) :]
                if relative_dependency not in entry_paths:
                    raise InstallProtocolError(
                        "Python dependency is absent from closure"
                    )
    return dict(value)


def _write_sealed(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)


def build_install_package(
    source_root: Path,
    output_root: Path,
    *,
    runner_public_key: object,
    python_tree_receipt: object,
) -> dict[str, Any]:
    """Emit one deterministic, content-free package for external review."""

    source_root = Path(os.path.abspath(source_root))
    output_root = Path(os.path.abspath(output_root))
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(output_root)
    if source_root.is_symlink() or not source_root.is_dir():
        raise InstallProtocolError("source root is unavailable")
    public_key = _normalize_runner_public_key(runner_public_key)
    python_runtime = _python_runtime_receipt(python_tree_receipt)
    launcher = render_launcher()
    authorized_key = render_authorized_key(public_key)
    sshd_drop_in = render_sshd_drop_in()
    code_payloads: dict[str, tuple[str, bytes]] = {}
    for name, filename in _CODE_FILES.items():
        source = source_root / filename
        if source.is_symlink() or not source.is_file():
            raise InstallProtocolError(f"trusted source is unavailable: {name}")
        code_payloads[name] = (filename, source.read_bytes())

    code_subjects = {
        name: {
            **_receipt(payload),
            "install_path": str(service.PRODUCTION_CODE_DIR / filename),
            "package_path": f"payload/code/{filename}",
            "owner": "root",
            "group": "wheel",
            "mode": "0444",
        }
        for name, (filename, payload) in sorted(code_payloads.items())
    }
    manifest = {
        "kind": "experiment_executor_install_package",
        "schema_version": INSTALL_SCHEMA_VERSION,
        "executor_account": EXECUTOR_ACCOUNT,
        "run_account": RUN_ACCOUNT,
        "executor_principal": {
            "account": EXECUTOR_ACCOUNT,
            "admin": False,
            "hidden": True,
            "home": str(service.PRODUCTION_BUNDLE_DIR),
            "password_authentication": False,
            "shell": "/bin/sh",
        },
        "fixed_process": {
            "argv": [
                str(service.PRODUCTION_PYTHON_PATH),
                "-I",
                "-B",
                "-S",
                str(service.PRODUCTION_BOOTSTRAP_PATH),
            ],
            "cwd": str(service.PRODUCTION_BUNDLE_DIR),
            "environment": service.PRODUCTION_ENVIRONMENT,
            "rlimit_core": [0, 0],
            "umask": "0077",
        },
        "python_runtime": python_runtime,
        "code_subjects": code_subjects,
        "transport": {
            "launcher": {
                **_receipt(launcher),
                "path": str(PRODUCTION_LAUNCHER_PATH),
                "owner": "root",
                "group": "wheel",
                "mode": "0555",
            },
            "authorized_key": {
                **_receipt(authorized_key),
                "path": str(PRODUCTION_AUTHORIZED_KEYS_PATH),
                "owner": "root",
                "group": "wheel",
                "mode": "0600",
            },
            "sshd_drop_in": {
                **_receipt(sshd_drop_in),
                "path": str(PRODUCTION_SSHD_DROP_IN_PATH),
                "owner": "root",
                "group": "wheel",
                "mode": "0644",
            },
        },
        "model_calls": 0,
    }

    created = False
    try:
        output_root.mkdir(mode=0o700)
        created = True
        _write_sealed(
            output_root / "payload/run-experiment-executor-service",
            launcher,
            0o500,
        )
        _write_sealed(
            output_root / "payload/authorized_keys.entry",
            authorized_key,
            0o400,
        )
        _write_sealed(
            output_root / "payload/sshd_config.drop-in",
            sshd_drop_in,
            0o400,
        )
        for _name, (filename, payload) in sorted(code_payloads.items()):
            _write_sealed(output_root / f"payload/code/{filename}", payload, 0o400)
        _write_sealed(
            output_root / "install-manifest.json",
            _canonical_json_bytes(manifest),
            0o400,
        )
    except BaseException:
        if created:
            shutil.rmtree(output_root)
        raise
    return manifest
