#!/usr/bin/env python3
"""Restricted one-request transport for the trusted experiment executor.

The untrusted run account may ask only for the next sealed call or a
content-free status projection.  It cannot submit model-facing fields and it
cannot fetch raw artifacts.  Production construction from an admin-owned
sealed bundle is implemented below this transport boundary.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import resource
import signal
import stat
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

import codex_harness
import experiment_anchor
import experiment_executor as executor_module
import experiment_witness as witness
import trusted_codex_driver


SERVICE_SCHEMA_VERSION = "experiment-executor-service-v1"
SERVICE_REQUEST_KIND = "experiment_executor_service_request"
BUNDLE_SCHEMA_VERSION = "experiment-executor-service-bundle-v1"
ANCHOR_LOCATOR_SCHEMA_VERSION = "experiment-executor-anchor-locator-v1"
MAX_REQUEST_BYTES = 4096
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024
PRODUCTION_BUNDLE_DIR = Path(
    "/Library/Application Support/CoralEHR/experiment-executor"
)
PRODUCTION_SERVICE_PATH = Path(
    "/usr/local/lib/coralehr-experiment-executor/experiment_executor_service.py"
)
PRODUCTION_CODE_DIR = PRODUCTION_SERVICE_PATH.parent
PRODUCTION_BOOTSTRAP_PATH = PRODUCTION_CODE_DIR / "experiment_executor_bootstrap.py"
PRODUCTION_PYTHON_PATH = PRODUCTION_CODE_DIR / "python/bin/python3.14"
PRODUCTION_TMPDIR = PRODUCTION_BUNDLE_DIR / "scratch/service-tmp"
PRODUCTION_ENVIRONMENT = {
    "HOME": str(PRODUCTION_BUNDLE_DIR),
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "TMPDIR": str(PRODUCTION_TMPDIR),
}
_MACOS_TEXT_ENCODING = "__CF_USER_TEXT_ENCODING"
ANCHOR_CHECKER_VERIFIER = {
    "algorithm": "ssh-ed25519",
    "identity": "coralehr-anchor-checker-2026-07",
    "namespace": experiment_anchor.SIGNED_ANCHOR_NAMESPACE,
    "public_key": (
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIBTUvOkYCO6lTyaUb5FCUmBmnG3PwYHlu61xwDylXEql"
    ),
    "key_id": "sha256:e707d1fd1da290d19d67f470c2438e978234f8182217a0dea99e83f1a7bf0abb",
}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_WITNESS_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


class ServiceProtocolError(ValueError):
    """The restricted request or trusted response violated its contract."""


class ServiceBootstrapError(RuntimeError):
    """The private service bundle or its external binding is unsafe."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ServiceProtocolError("duplicate request key")
        value[key] = child
    return value


def _reject_constant(_value: str) -> None:
    raise ServiceProtocolError("non-finite request number")


def canonical_json_line(value: object) -> bytes:
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


def _require_hex64(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise ServiceBootstrapError(f"{label} is invalid")
    return value


def _require_safe_ancestor_chain(path: Path) -> None:
    current = path.resolve()
    while True:
        try:
            status = current.lstat()
        except OSError as exc:
            raise ServiceBootstrapError("sealed bundle ancestor is unavailable") from exc
        if (
            current.is_symlink()
            or not stat.S_ISDIR(status.st_mode)
            or status.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(status.st_mode) & 0o022
        ):
            raise ServiceBootstrapError("sealed bundle ancestor is unsafe")
        if current.parent == current:
            break
        current = current.parent


def _require_private_directory(path: Path, label: str) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise ServiceBootstrapError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o700
    ):
        raise ServiceBootstrapError(f"{label} is not private")


def _read_sealed_file(
    path: Path,
    *,
    label: str,
    allowed_modes: frozenset[int],
    byte_cap: int,
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            status = os.fstat(descriptor)
            path_status = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) not in allowed_modes
                or status.st_nlink != 1
                or status.st_size > byte_cap
                or (status.st_dev, status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                raise ServiceBootstrapError(f"{label} metadata is unsafe")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, byte_cap + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > byte_cap:
                    raise ServiceBootstrapError(f"{label} exceeds its byte cap")
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except ServiceBootstrapError:
        raise
    except OSError as exc:
        raise ServiceBootstrapError(f"{label} is unavailable") from exc
    return b"".join(chunks)


def _load_canonical_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceBootstrapError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or canonical_json_line(value) != payload:
        raise ServiceBootstrapError(f"{label} is not canonical")
    return value


def _read_executable_receipt(value: object, *, label: str) -> tuple[Path, bytes]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "bytes"}:
        raise ServiceBootstrapError(f"{label} receipt is invalid")
    path_value = value.get("path")
    size = value.get("bytes")
    digest = _require_hex64(value.get("sha256"), f"{label} digest")
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        raise ServiceBootstrapError(f"{label} path is invalid")
    if type(size) is not int or size < 0 or size > MAX_BUNDLE_BYTES:
        raise ServiceBootstrapError(f"{label} size is invalid")
    path = Path(os.path.abspath(path_value))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            status = os.fstat(descriptor)
            path_status = path.lstat()
            mode = stat.S_IMODE(status.st_mode)
            if (
                path.is_symlink()
                or not stat.S_ISREG(status.st_mode)
                or status.st_uid not in {0, os.geteuid()}
                or mode & 0o022
                or not mode & 0o100
                or status.st_nlink != 1
                or status.st_size != size
                or (status.st_dev, status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                raise ServiceBootstrapError(f"{label} executable is unsafe")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BUNDLE_BYTES:
                    raise ServiceBootstrapError(f"{label} executable is too large")
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except ServiceBootstrapError:
        raise
    except OSError as exc:
        raise ServiceBootstrapError(f"{label} executable is unavailable") from exc
    payload = b"".join(chunks)
    if _sha256(payload) != digest:
        raise ServiceBootstrapError(f"{label} executable digest changed")
    return path, payload


def _read_immutable_code_file(path: Path, *, label: str) -> bytes:
    absolute = Path(os.path.abspath(path))
    if absolute.is_symlink() or absolute.suffix != ".py":
        raise ServiceBootstrapError(f"{label} code path is unsafe")
    current = absolute.parent
    while True:
        try:
            status = current.lstat()
        except OSError as exc:
            raise ServiceBootstrapError(f"{label} code ancestor is unavailable") from exc
        if (
            current.is_symlink()
            or not stat.S_ISDIR(status.st_mode)
            or status.st_uid != 0
            or stat.S_IMODE(status.st_mode) & 0o022
        ):
            raise ServiceBootstrapError(f"{label} code ancestor is unsafe")
        if current.parent == current:
            break
        current = current.parent
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(absolute, flags)
        try:
            status = os.fstat(descriptor)
            path_status = absolute.lstat()
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != 0
                or stat.S_IMODE(status.st_mode) & 0o222
                or status.st_nlink != 1
                or status.st_size > MAX_CONTROL_FILE_BYTES
                or (status.st_dev, status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                raise ServiceBootstrapError(f"{label} code metadata is unsafe")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, MAX_CONTROL_FILE_BYTES + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_CONTROL_FILE_BYTES:
                    raise ServiceBootstrapError(f"{label} code exceeds its byte cap")
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except ServiceBootstrapError:
        raise
    except OSError as exc:
        raise ServiceBootstrapError(f"{label} code is unavailable") from exc
    payload = b"".join(chunks)
    if len(payload) != status.st_size:
        raise ServiceBootstrapError(f"{label} code size changed")
    return payload


def _current_code_subjects() -> list[dict[str, object]]:
    subjects = {
        "a11b_nightly_bootstrap": Path(__file__).with_name(
            "a11b_nightly_bootstrap.py"
        ),
        "a11b_nightly_runner": Path(__file__).with_name("a11b_nightly_runner.py"),
        "anchor": Path(experiment_anchor.__file__),
        "bootstrap": PRODUCTION_BOOTSTRAP_PATH,
        "codex_harness": Path(codex_harness.__file__),
        "driver": Path(trusted_codex_driver.__file__),
        "executor": Path(executor_module.__file__),
        "service": Path(sys.modules[__name__].__file__),
        "witness": Path(witness.__file__),
    }
    result: list[dict[str, object]] = []
    for name, module_path in sorted(subjects.items()):
        if not isinstance(module_path, Path):
            raise ServiceBootstrapError("trusted code subject path is unavailable")
        module = sys.modules.get(
            {
                "anchor": experiment_anchor.__name__,
                "codex_harness": codex_harness.__name__,
                "driver": trusted_codex_driver.__name__,
                "executor": executor_module.__name__,
                "service": __name__,
                "witness": witness.__name__,
            }.get(name, "")
        )
        cached_path = getattr(module, "__cached__", None) if module else None
        if isinstance(cached_path, str) and Path(cached_path).exists():
            raise ServiceBootstrapError("trusted code subject loaded from cache")
        payload = _read_immutable_code_file(module_path, label=name)
        result.append({"name": name, "sha256": _sha256(payload), "bytes": len(payload)})
    return result


def _parse_request(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_REQUEST_BYTES:
        raise ServiceProtocolError("request size is invalid")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceProtocolError("request JSON is invalid") from exc
    if not isinstance(value, dict) or canonical_json_line(value) != payload:
        raise ServiceProtocolError("request is not canonical")
    operation = value.get("operation")
    common_fields = {"kind", "schema_version", "operation", "run_id"}
    expected_fields = {
        "status": common_fields,
        "execute_next": common_fields | {"expected_head"},
    }.get(operation)
    if (
        expected_fields is None
        or set(value) != expected_fields
        or value.get("kind") != SERVICE_REQUEST_KIND
        or value.get("schema_version") != SERVICE_SCHEMA_VERSION
    ):
        raise ServiceProtocolError("request fields are invalid")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or _HEX_64.fullmatch(run_id) is None:
        raise ServiceProtocolError("request run ID is invalid")
    if operation == "execute_next":
        expected_head = value.get("expected_head")
        if (
            not isinstance(expected_head, str)
            or _HEX_64.fullmatch(expected_head) is None
        ):
            raise ServiceProtocolError("request witness head is invalid")
    return value


def _error(code: str) -> bytes:
    return canonical_json_line(
        {
            "error": {"code": code},
            "ok": False,
            "schema_version": SERVICE_SCHEMA_VERSION,
        }
    )


class RestrictedExecutorService:
    """Expose only sealed execution and content-free status."""

    def __init__(self, trusted_executor: object) -> None:
        self._executor = trusted_executor

    @staticmethod
    def _status_result(status: Mapping[str, Any]) -> dict[str, Any]:
        witness_status = status.get("witness")
        if not isinstance(witness_status, Mapping):
            raise ServiceProtocolError("trusted status is invalid")
        result = {
            "kind": "content_free_executor_status",
            "run_id": status.get("run_id"),
            "witness_head": witness_status.get("head"),
            "state": witness_status.get("state"),
            "schedule_position": witness_status.get("schedule_position"),
            "next_attempt_number": witness_status.get("next_attempt_number"),
            "model_calls_reserved": witness_status.get("model_calls_reserved"),
            "model_calls_closed": witness_status.get("model_calls_closed"),
        }
        if (
            _HEX_64.fullmatch(str(result["run_id"] or "")) is None
            or _HEX_64.fullmatch(str(result["witness_head"] or "")) is None
            or result["state"] not in {"active", "open", "aborted", "complete"}
            or any(
                type(result[name]) is not int or result[name] < 0
                for name in (
                    "schedule_position",
                    "next_attempt_number",
                    "model_calls_reserved",
                    "model_calls_closed",
                )
            )
        ):
            raise ServiceProtocolError("trusted status is invalid")
        return result

    @staticmethod
    def _execution_result(result: object) -> dict[str, Any]:
        projected = {
            "kind": "executor_call_result",
            "run_id": getattr(result, "run_id", None),
            "request_head": getattr(result, "request_head", None),
            "witness_head": getattr(result, "witness_head", None),
            "outcome": getattr(result, "outcome", None),
            "token_usage": getattr(result, "token_usage", None),
            "artifact_root_commitment": getattr(
                result, "artifact_root_commitment", None
            ),
            "opened_receipt": getattr(result, "opened_receipt", None),
            "closed_receipt": getattr(result, "closed_receipt", None),
            "reason": getattr(result, "reason", None),
        }
        if (
            any(
                _HEX_64.fullmatch(str(projected[name] or "")) is None
                for name in (
                    "run_id",
                    "request_head",
                    "witness_head",
                    "artifact_root_commitment",
                )
            )
            or projected["outcome"] not in witness.OUTCOMES
            or not isinstance(projected["token_usage"], Mapping)
            or not isinstance(projected["opened_receipt"], Mapping)
            or not isinstance(projected["closed_receipt"], Mapping)
            or not isinstance(projected["reason"], str)
        ):
            raise ServiceProtocolError("trusted execution result is invalid")
        return projected

    def _dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        if operation == "status":
            status = self._executor.status(run_id=request["run_id"])
            result = self._status_result(status)
        else:
            executed = self._executor.execute_next(
                run_id=request["run_id"],
                expected_head=request["expected_head"],
            )
            result = self._execution_result(executed)
        return {
            "ok": True,
            "result": result,
            "schema_version": SERVICE_SCHEMA_VERSION,
        }

    def handle(self, payload: bytes) -> bytes:
        try:
            request = _parse_request(payload)
        except (ServiceProtocolError, TypeError, ValueError):
            return _error("invalid_request")
        try:
            return canonical_json_line(self._dispatch(request))
        except executor_module.ExecutorProtocolError:
            return _error("protocol_error")
        except executor_module.ExecutorIntegrityError:
            return _error("integrity_error")
        except executor_module.ExecutorIndeterminateError as exc:
            if exc.result is None:
                return _error("indeterminate")
            try:
                return canonical_json_line(
                    {
                        "ok": True,
                        "result": self._execution_result(exc.result),
                        "schema_version": SERVICE_SCHEMA_VERSION,
                    }
                )
            except Exception:
                return _error("internal_error")
        except witness.WitnessProtocolError:
            return _error("protocol_error")
        except witness.WitnessIntegrityError:
            return _error("integrity_error")
        except Exception:
            return _error("internal_error")


def _decode_invocations(
    value: object,
    *,
    runtime_path: Path,
    runtime_sha256: str,
) -> tuple[executor_module.SealedInvocation, ...]:
    if not isinstance(value, list) or not value:
        raise ServiceBootstrapError("sealed invocation inventory is invalid")
    invocations: list[executor_module.SealedInvocation] = []
    fields = {
        "phase",
        "schedule_index",
        "prompt_base64",
        "output_schema_base64",
        "model",
        "reasoning_effort",
        "timeout_seconds",
    }
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            raise ServiceBootstrapError("sealed invocation fields are invalid")
        prompt = item.get("prompt_base64")
        schema = item.get("output_schema_base64")
        if not isinstance(prompt, str) or not isinstance(schema, str):
            raise ServiceBootstrapError("sealed invocation encoding is invalid")
        try:
            prompt_bytes = base64.b64decode(prompt, validate=True)
            schema_bytes = base64.b64decode(schema, validate=True)
        except (ValueError, TypeError) as exc:
            raise ServiceBootstrapError("sealed invocation encoding is invalid") from exc
        try:
            invocation = executor_module.SealedInvocation(
                phase=item["phase"],
                schedule_index=item["schedule_index"],
                prompt=prompt_bytes,
                output_schema=schema_bytes,
                model=item["model"],
                reasoning_effort=item["reasoning_effort"],
                runtime_path=str(runtime_path),
                runtime_sha256=runtime_sha256,
                timeout_seconds=item["timeout_seconds"],
            )
        except (KeyError, executor_module.ExecutorProtocolError) as exc:
            raise ServiceBootstrapError("sealed invocation is invalid") from exc
        invocations.append(invocation)
    return tuple(invocations)


def _decode_schedule(
    value: object,
) -> tuple[witness.ScheduleItem, ...]:
    if not isinstance(value, list) or not value:
        raise ServiceBootstrapError("sealed witness schedule is invalid")
    schedule: list[witness.ScheduleItem] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "phase",
            "schedule_index",
            "call_commitment",
            "max_attempts",
        }:
            raise ServiceBootstrapError("sealed witness schedule fields are invalid")
        try:
            schedule.append(
                witness.ScheduleItem(
                    phase=item["phase"],
                    schedule_index=item["schedule_index"],
                    call_commitment=item["call_commitment"],
                    max_attempts=item["max_attempts"],
                )
            )
        except KeyError as exc:
            raise ServiceBootstrapError("sealed witness schedule is invalid") from exc
    return tuple(schedule)


def _public_binding(
    bundle: Mapping[str, Any], *, bundle_commitment: str
) -> dict[str, Any]:
    return {
        "bundle_commitment": bundle_commitment,
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "service_protocol_version": SERVICE_SCHEMA_VERSION,
        "run_id": bundle["run_id"],
        "witness": bundle["witness"],
        "runtime": bundle["runtime"],
        "sandbox": bundle["sandbox"],
        "executables": bundle["executables"],
        "code_subjects": bundle["code_subjects"],
        "model_configuration": bundle["model_configuration"],
        "anchor_verifier": bundle["anchor_verifier"],
    }


def _validate_recorded_service_anchor(
    *,
    controller_bytes: bytes,
    anchor_url: str,
    receipt_bytes: bytes,
    expected_controller_sha256: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="experiment-service-anchor-"
    ) as directory:
        controller_path = Path(directory) / "controller.json"
        controller_path.write_bytes(controller_bytes)
        experiment_anchor.verify_signed_external_anchor_receipt(
            controller_path,
            anchor_url,
            receipt_bytes,
            expected_controller_sha256=expected_controller_sha256,
            expected_verifier=ANCHOR_CHECKER_VERIFIER,
        )
        request = experiment_anchor.build_anchor_request(controller_path)
    binding = request.get("trusted_executor")
    if not isinstance(binding, dict):
        raise ServiceBootstrapError(
            "external anchor does not bind the trusted executor"
        )
    subjects = request.get("subjects")
    native_codex = subjects.get("native_codex") if isinstance(subjects, dict) else None
    model_configuration = request.get("model_configuration")
    if not isinstance(native_codex, dict) or not isinstance(
        model_configuration, dict
    ):
        raise ServiceBootstrapError("external anchor execution binding is incomplete")
    return {
        "trusted_executor": binding,
        "model_configuration": model_configuration,
        "native_codex": native_codex,
    }


def _verify_bound_installation(bundle_dir: Path, controller_bytes: bytes) -> None:
    """Recompute the signed install-manifest and complete Python-tree bindings."""

    try:
        controller = json.loads(controller_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceBootstrapError("sealed controller is invalid") from exc
    inputs = controller.get("inputs") if isinstance(controller, dict) else None
    snapshots = controller.get("snapshots") if isinstance(controller, dict) else None
    if not isinstance(inputs, Mapping) or not isinstance(snapshots, Mapping):
        return
    install_sha = inputs.get("install_manifest_sha256")
    python_sha = inputs.get("python_tree_receipt_sha256")
    if install_sha is None and python_sha is None:
        return
    install_sha = _require_hex64(install_sha, "install manifest digest")
    python_sha = _require_hex64(python_sha, "Python tree receipt digest")

    bound_files: dict[str, bytes] = {}
    for snapshot_name, filename, expected_sha in (
        ("install_manifest", "install-manifest.json", install_sha),
        ("python_tree", "python-tree-receipt.json", python_sha),
    ):
        snapshot = snapshots.get(snapshot_name)
        if not isinstance(snapshot, Mapping):
            raise ServiceBootstrapError("controller install snapshot is missing")
        payload = _read_sealed_file(
            bundle_dir / filename,
            label=f"sealed {snapshot_name}",
            allowed_modes=frozenset({0o400}),
            byte_cap=MAX_CONTROL_FILE_BYTES,
        )
        if {
            "sha256": _sha256(payload),
            "bytes": len(payload),
        } != {
            "sha256": snapshot.get("sha256"),
            "bytes": snapshot.get("bytes"),
        } or _sha256(payload) != expected_sha:
            raise ServiceBootstrapError("controller install snapshot changed")
        bound_files[snapshot_name] = payload

    python_tree = _load_canonical_object(
        bound_files["python_tree"], label="Python tree receipt"
    )
    entries = python_tree.get("entries")
    python_root = PRODUCTION_CODE_DIR / "python"
    if (
        set(python_tree) != {
            "schema_version",
            "root",
            "executable",
            "tree_sha256",
            "files",
            "bytes",
            "version",
            "entries",
        }
        or python_tree.get("schema_version") != "experiment-python-tree-v1"
        or python_tree.get("root") != str(python_root)
        or python_tree.get("executable") != str(PRODUCTION_PYTHON_PATH)
        or not isinstance(entries, list)
        or type(python_tree.get("files")) is not int
        or python_tree.get("files") != len(entries)
    ):
        raise ServiceBootstrapError("Python tree receipt changed")
    expected_paths: set[str] = set()
    expected_bytes = 0
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "sha256",
            "bytes",
            "mode",
            "owner",
            "group",
            "links",
            "format",
            "dependencies",
        }:
            raise ServiceBootstrapError("Python tree entry changed")
        relative = entry.get("path")
        parts = Path(str(relative)).parts
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in parts)
            or relative in expected_paths
            or _require_hex64(entry.get("sha256"), "Python entry digest")
            != entry.get("sha256")
            or type(entry.get("bytes")) is not int
            or int(entry["bytes"]) <= 0
            or entry.get("mode") not in {"0444", "0555"}
            or entry.get("owner") != "root"
            or entry.get("group") != "wheel"
            or entry.get("links") != 1
        ):
            raise ServiceBootstrapError("Python tree entry changed")
        path = python_root / relative
        try:
            status = path.lstat()
        except OSError as exc:
            raise ServiceBootstrapError("installed Python entry is unavailable") from exc
        payload = path.read_bytes()
        if (
            path.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or status.st_uid != 0
            or status.st_gid != 0
            or status.st_nlink != 1
            or stat.S_IMODE(status.st_mode) != int(str(entry["mode"]), 8)
            or _sha256(payload) != entry["sha256"]
            or len(payload) != entry["bytes"]
        ):
            raise ServiceBootstrapError("installed Python entry changed")
        expected_paths.add(relative)
        expected_bytes += len(payload)
    observed_paths: set[str] = set()
    for path in python_root.rglob("*"):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode) or (
            not stat.S_ISDIR(status.st_mode) and not stat.S_ISREG(status.st_mode)
        ):
            raise ServiceBootstrapError("installed Python inventory is unsafe")
        if stat.S_ISREG(status.st_mode):
            observed_paths.add(path.relative_to(python_root).as_posix())
    tree_preimage = canonical_json_line(
        {
            "schema_version": "experiment-python-tree-v1",
            "root": str(python_root),
            "entries": entries,
        }
    )
    if (
        observed_paths != expected_paths
        or python_tree.get("bytes") != expected_bytes
        or python_tree.get("tree_sha256") != _sha256(tree_preimage)
    ):
        raise ServiceBootstrapError("installed Python tree changed")


def _load_sealed_service(
    bundle_dir: Path,
    *,
    anchor_validator: Callable[..., dict[str, Any]],
    driver_factory: Callable[..., object],
    code_subject_provider: Callable[[], list[dict[str, object]]],
    clock: Callable[[], str],
) -> RestrictedExecutorService:
    supplied_bundle_dir = Path(os.path.abspath(bundle_dir))
    if supplied_bundle_dir.is_symlink():
        raise ServiceBootstrapError("sealed bundle directory is a symlink")
    bundle_dir = supplied_bundle_dir.resolve()
    _require_safe_ancestor_chain(bundle_dir)
    _require_private_directory(bundle_dir, "sealed bundle directory")
    for relative, label in (
        ("state", "sealed state directory"),
        ("state/witness", "witness state directory"),
        ("state/executor", "executor state directory"),
        ("codex-home", "trusted Codex home"),
        ("scratch", "trusted scratch directory"),
        ("scratch/service-tmp", "trusted service temporary directory"),
    ):
        _require_private_directory(bundle_dir / relative, label)

    commitment_key = _read_sealed_file(
        bundle_dir / "commitment.key",
        label="commitment key",
        allowed_modes=frozenset({0o600}),
        byte_cap=32,
    )
    if len(commitment_key) != 32:
        raise ServiceBootstrapError("commitment key must contain exactly 32 bytes")
    _read_sealed_file(
        bundle_dir / "witness_ed25519",
        label="witness private key",
        allowed_modes=frozenset({0o600}),
        byte_cap=MAX_CONTROL_FILE_BYTES,
    )
    bundle_bytes = _read_sealed_file(
        bundle_dir / "bundle.json",
        label="sealed service bundle",
        allowed_modes=frozenset({0o400}),
        byte_cap=MAX_BUNDLE_BYTES,
    )
    bundle = _load_canonical_object(bundle_bytes, label="sealed service bundle")
    controller_bytes = _read_sealed_file(
        bundle_dir / "controller.json",
        label="sealed A11b controller",
        allowed_modes=frozenset({0o400}),
        byte_cap=MAX_CONTROL_FILE_BYTES,
    )
    controller_sha256 = _sha256(controller_bytes)
    locator_bytes = _read_sealed_file(
        bundle_dir / "anchor-locator.json",
        label="external anchor locator",
        allowed_modes=frozenset({0o400}),
        byte_cap=MAX_CONTROL_FILE_BYTES,
    )
    locator = _load_canonical_object(locator_bytes, label="external anchor locator")
    receipt_bytes = _read_sealed_file(
        bundle_dir / "external-anchor-verification.json",
        label="external anchor verification receipt",
        allowed_modes=frozenset({0o400}),
        byte_cap=MAX_CONTROL_FILE_BYTES,
    )

    expected_bundle_fields = {
        "kind",
        "schema_version",
        "service_protocol_version",
        "run_id",
        "witness",
        "runtime",
        "sandbox",
        "executables",
        "code_subjects",
        "model_configuration",
        "anchor_verifier",
        "invocations",
    }
    if (
        set(bundle) != expected_bundle_fields
        or bundle.get("kind") != "experiment_executor_service_bundle"
        or bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION
        or bundle.get("service_protocol_version") != SERVICE_SCHEMA_VERSION
    ):
        raise ServiceBootstrapError("sealed service bundle identity changed")
    run_id = _require_hex64(bundle.get("run_id"), "sealed run ID")
    if set(locator) != {
        "kind",
        "schema_version",
        "anchor_url",
        "controller_sha256",
        "bundle_commitment",
    } or (
        locator.get("kind") != "experiment_executor_anchor_locator"
        or locator.get("schema_version") != ANCHOR_LOCATOR_SCHEMA_VERSION
    ):
        raise ServiceBootstrapError("external anchor locator identity changed")
    anchor_url = locator.get("anchor_url")
    if not isinstance(anchor_url, str):
        raise ServiceBootstrapError("external anchor URL is invalid")
    experiment_anchor.parse_github_anchor_url(anchor_url)
    bundle_commitment = witness.keyed_commitment(
        commitment_key,
        domain="executor-bundle",
        payload=bundle_bytes,
    )
    if locator.get("bundle_commitment") != bundle_commitment:
        raise ServiceBootstrapError("sealed service bundle commitment changed")

    if locator.get("controller_sha256") != controller_sha256:
        raise ServiceBootstrapError("external anchor controller binding changed")

    runtime = bundle.get("runtime")
    sandbox = bundle.get("sandbox")
    executables = bundle.get("executables")
    if not isinstance(runtime, Mapping) or set(runtime) != {
        "path",
        "sha256",
        "bytes",
        "version",
    }:
        raise ServiceBootstrapError("sealed runtime receipt is invalid")
    runtime_version = runtime.get("version")
    if not isinstance(runtime_version, str) or not runtime_version.startswith(
        "codex-cli "
    ):
        raise ServiceBootstrapError("sealed runtime version is invalid")
    runtime_path, _runtime_bytes = _read_executable_receipt(
        {key: runtime[key] for key in ("path", "sha256", "bytes")},
        label="sealed native runtime",
    )
    runtime_sha256 = _require_hex64(runtime.get("sha256"), "sealed runtime digest")
    if not isinstance(sandbox, Mapping) or set(sandbox) != {
        "path",
        "sha256",
        "bytes",
        "profile",
    }:
        raise ServiceBootstrapError("sealed sandbox receipt is invalid")
    if sandbox.get("profile") != trusted_codex_driver.TrustedCodexDriver._SANDBOX_PROFILE:
        raise ServiceBootstrapError("sealed sandbox profile changed")
    sandbox_path, _sandbox_bytes = _read_executable_receipt(
        {key: sandbox[key] for key in ("path", "sha256", "bytes")},
        label="sealed sandbox executable",
    )
    sandbox_sha256 = _require_hex64(sandbox.get("sha256"), "sandbox digest")
    if not isinstance(executables, Mapping) or set(executables) != {
        "python",
        "ssh_keygen",
    }:
        raise ServiceBootstrapError("trusted executable inventory is invalid")
    python_path, _python_bytes = _read_executable_receipt(
        executables["python"], label="trusted Python"
    )
    ssh_keygen_path, _ssh_keygen_bytes = _read_executable_receipt(
        executables["ssh_keygen"], label="trusted ssh-keygen"
    )
    if python_path.resolve() != Path(sys.executable).resolve():
        raise ServiceBootstrapError("trusted Python path changed")
    if ssh_keygen_path.resolve() != witness.SSH_KEYGEN_PATH.resolve():
        raise ServiceBootstrapError("trusted ssh-keygen path changed")
    if bundle.get("code_subjects") != code_subject_provider():
        raise ServiceBootstrapError("trusted service code identity changed")
    if bundle.get("anchor_verifier") != ANCHOR_CHECKER_VERIFIER:
        raise ServiceBootstrapError("independent anchor verifier changed")

    witness_value = bundle.get("witness")
    if not isinstance(witness_value, Mapping) or set(witness_value) != {
        "identity",
        "public_key",
        "key_id",
        "schedule",
    }:
        raise ServiceBootstrapError("sealed witness identity is invalid")
    identity = witness_value.get("identity")
    if not isinstance(identity, str) or _WITNESS_IDENTITY.fullmatch(identity) is None:
        raise ServiceBootstrapError("sealed witness identity is invalid")
    authenticator = witness.SshEd25519Authenticator(
        private_key_path=bundle_dir / "witness_ed25519",
        identity=identity,
    )
    if (
        witness_value.get("public_key") != authenticator.public_key
        or witness_value.get("key_id") != authenticator.key_id
    ):
        raise ServiceBootstrapError("sealed witness key identity changed")
    schedule = _decode_schedule(witness_value.get("schedule"))
    invocations = _decode_invocations(
        bundle.get("invocations"),
        runtime_path=runtime_path,
        runtime_sha256=runtime_sha256,
    )
    if len(schedule) != len(invocations) or any(
        item.phase != invocation.phase
        or item.schedule_index != invocation.schedule_index
        or item.call_commitment != invocation.call_commitment(commitment_key)
        for item, invocation in zip(schedule, invocations, strict=True)
    ):
        raise ServiceBootstrapError("sealed invocation schedule commitment changed")

    try:
        model_configuration = experiment_anchor._model_configuration(
            bundle.get("model_configuration")
        )
    except ValueError as exc:
        raise ServiceBootstrapError("sealed model configuration is invalid") from exc
    if bundle.get("model_configuration") != model_configuration:
        raise ServiceBootstrapError("sealed model configuration is noncanonical")
    for invocation in invocations:
        phase_configuration = model_configuration[invocation.phase]
        if (
            invocation.model != phase_configuration["model"]
            or invocation.reasoning_effort
            != phase_configuration["reasoning_effort"]
            or invocation.timeout_seconds
            != phase_configuration["timeout_seconds"]
        ):
            raise ServiceBootstrapError(
                "sealed invocation differs from model configuration"
            )

    expected_binding = _public_binding(bundle, bundle_commitment=bundle_commitment)
    anchored_execution = anchor_validator(
        controller_bytes=controller_bytes,
        anchor_url=anchor_url,
        receipt_bytes=receipt_bytes,
        expected_controller_sha256=controller_sha256,
    )
    if not isinstance(anchored_execution, Mapping) or set(anchored_execution) != {
        "trusted_executor",
        "model_configuration",
        "native_codex",
    }:
        raise ServiceBootstrapError("external execution binding is invalid")
    if canonical_json_line(
        anchored_execution["trusted_executor"]
    ) != canonical_json_line(expected_binding):
        raise ServiceBootstrapError("external trusted-executor binding changed")
    if anchored_execution["model_configuration"] != model_configuration:
        raise ServiceBootstrapError("external model configuration changed")
    if anchored_execution["native_codex"] != {
        "sha256": runtime["sha256"],
        "bytes": runtime["bytes"],
    }:
        raise ServiceBootstrapError("external native runtime changed")

    _verify_bound_installation(bundle_dir, controller_bytes)

    model_driver = driver_factory(
        account_home=bundle_dir,
        codex_home=bundle_dir / "codex-home",
        scratch_root=bundle_dir / "scratch",
        sandbox_exec_path=sandbox_path,
        sandbox_exec_sha256=sandbox_sha256,
    )
    ledger = witness.WitnessLedger(
        bundle_dir / "state/witness",
        run_id=run_id,
        schedule=schedule,
        authenticator=authenticator,
        clock=clock,
    )
    trusted_executor = executor_module.ExperimentExecutor(
        bundle_dir / "state/executor",
        ledger=ledger,
        invocations=invocations,
        commitment_key=commitment_key,
        driver=model_driver,
    )
    return RestrictedExecutorService(trusted_executor)


def load_sealed_service(
    bundle_dir: Path,
    *,
    anchor_validator: Callable[..., dict[str, Any]] = _validate_recorded_service_anchor,
    driver_factory: Callable[..., object] = trusted_codex_driver.TrustedCodexDriver,
    code_subject_provider: Callable[
        [], list[dict[str, object]]
    ] = _current_code_subjects,
    clock: Callable[[], str],
) -> RestrictedExecutorService:
    """Construct the service solely from one fixed admin-owned bundle root."""

    if threading.active_count() != 1:
        raise ServiceBootstrapError("trusted service process is not single-threaded")
    try:
        return _load_sealed_service(
            bundle_dir,
            anchor_validator=anchor_validator,
            driver_factory=driver_factory,
            code_subject_provider=code_subject_provider,
            clock=clock,
        )
    except ServiceBootstrapError:
        raise
    except Exception as exc:
        raise ServiceBootstrapError("sealed service bootstrap failed") from exc


def serve_once(
    service: RestrictedExecutorService,
    stdin: BinaryIO,
    stdout: BinaryIO,
) -> int:
    """Read one bounded request, emit one canonical response, then exit."""

    payload = stdin.read(MAX_REQUEST_BYTES + 1)
    response = service.handle(payload)
    stdout.write(response)
    stdout.flush()
    return 0


def _utc_clock() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class _ServiceShutdown(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__()
        self.signum = signum


def _shutdown_handler(signum: int, _frame: object) -> None:
    raise _ServiceShutdown(signum)


def _install_shutdown_handlers() -> None:
    for name in ("SIGHUP", "SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if isinstance(signum, int):
            signal.signal(signum, _shutdown_handler)


def _require_production_process() -> None:
    actual_environment = dict(os.environ)
    text_encoding = actual_environment.pop(_MACOS_TEXT_ENCODING, None)
    environment_is_exact = actual_environment == PRODUCTION_ENVIRONMENT and (
        text_encoding is None
        or (
            isinstance(text_encoding, str)
            and text_encoding.startswith("0x")
            and len(text_encoding) <= 32
            and all(
                character in "0123456789abcdefABCDEFx:"
                for character in text_encoding
            )
        )
    )
    current_umask = os.umask(0o077)
    os.umask(current_umask)
    process_limits_are_exact = (
        current_umask == 0o077
        and resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
    )
    if (
        sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
        or sys.flags.no_site != 1
        or len(sys.argv) != 1
        or Path(sys.executable).resolve() != PRODUCTION_PYTHON_PATH
        or Path(os.path.abspath(sys.argv[0])) != PRODUCTION_SERVICE_PATH
        or Path.cwd() != PRODUCTION_BUNDLE_DIR
        or not environment_is_exact
        or Path(tempfile.gettempdir()).resolve() != PRODUCTION_TMPDIR
        or not sys.path
        or Path(sys.path[-1]) != PRODUCTION_CODE_DIR
        or sys.path.count(str(PRODUCTION_CODE_DIR)) != 1
        or not process_limits_are_exact
    ):
        raise ServiceBootstrapError("trusted service process isolation changed")


def _write_response(payload: bytes) -> bool:
    try:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    except (_ServiceShutdown, BrokenPipeError, OSError):
        return False
    return True


def main() -> int:
    """Load the one compile-time bundle root, serve once, and exit."""

    try:
        _install_shutdown_handlers()
        _require_production_process()
        service = load_sealed_service(PRODUCTION_BUNDLE_DIR, clock=_utc_clock)
    except _ServiceShutdown as exc:
        return 128 + exc.signum
    except Exception:
        _write_response(_error("bootstrap_failure"))
        return 1
    try:
        return serve_once(service, sys.stdin.buffer, sys.stdout.buffer)
    except _ServiceShutdown as exc:
        return 128 + exc.signum
    except (BrokenPipeError, OSError):
        return 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
