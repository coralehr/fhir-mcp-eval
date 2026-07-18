#!/usr/bin/env python3
"""Run the sealed A11b answer, grading, panel, and finalization workflow."""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import a11b_postprocess
import a11b_launch_protocol as launch_protocol
import experiment_executor_service as service
import run_lock


BUNDLE_ROOT = service.PRODUCTION_BUNDLE_DIR
AUDIT_ROOT = BUNDLE_ROOT / "audit-input"
STATUS_PATH = BUNDLE_ROOT / "nightly-status.json"
LOCK_PATH = BUNDLE_ROOT / "nightly-runner.lock"
LAUNCH_ACK_PATH = service.PRODUCTION_CODE_DIR / "launch-ack.json"
LAUNCH_CONFIRMATION_PATH = BUNDLE_ROOT / "launch-confirmation.json"
LAUNCH_COMMIT_PATH = service.PRODUCTION_CODE_DIR / "launch-commit.json"


def _clock() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write_status(value: dict[str, Any]) -> str:
    payload = launch_protocol.canonical_json_line(value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".nightly-status.", dir=BUNDLE_ROOT
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        sent = 0
        while sent < len(payload):
            sent += os.write(descriptor, payload[sent:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, STATUS_PATH)
        directory = os.open(BUNDLE_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return launch_protocol.sha256(payload)


def _read_canonical_control(
    path: Path, *, expected_uid: int, expected_mode: int, label: str
) -> tuple[dict[str, Any], bytes]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != expected_uid
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_size > service.MAX_CONTROL_FILE_BYTES
        ):
            raise RuntimeError(f"{label} metadata is unsafe")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
            if len(payload) > service.MAX_CONTROL_FILE_BYTES:
                raise RuntimeError(f"{label} is oversized")
        after = os.fstat(descriptor)
        path_status = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (path_status.st_dev, path_status.st_ino)
            != (after.st_dev, after.st_ino)
            or len(payload) != before.st_size
        ):
            raise RuntimeError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is invalid") from exc
    if not isinstance(value, dict) or launch_protocol.canonical_json_line(value) != raw:
        raise RuntimeError(f"{label} is noncanonical")
    return value, raw


def _read_launch_ack(*, expected_uid: int) -> tuple[dict[str, Any], bytes]:
    return _read_canonical_control(
        LAUNCH_ACK_PATH,
        expected_uid=expected_uid,
        expected_mode=0o444,
        label="launch acknowledgement",
    )


def _read_launch_commit(*, expected_uid: int) -> tuple[dict[str, Any], bytes]:
    return _read_canonical_control(
        LAUNCH_COMMIT_PATH,
        expected_uid=expected_uid,
        expected_mode=0o444,
        label="launch commit",
    )


def _publish_launch_confirmation(value: Mapping[str, Any]) -> None:
    payload = launch_protocol.canonical_json_line(dict(value))
    if LAUNCH_CONFIRMATION_PATH.exists():
        _existing_value, existing = _read_canonical_control(
            LAUNCH_CONFIRMATION_PATH,
            expected_uid=os.geteuid(),
            expected_mode=0o600,
            label="launch confirmation",
        )
        if existing != payload:
            raise RuntimeError("launch confirmation binding is invalid")
        return
    try:
        descriptor = os.open(
            LAUNCH_CONFIRMATION_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        _existing_value, existing = _read_canonical_control(
            LAUNCH_CONFIRMATION_PATH,
            expected_uid=os.geteuid(),
            expected_mode=0o600,
            label="launch confirmation",
        )
        if existing != payload:
            raise RuntimeError("launch confirmation binding is invalid")
        return
    try:
        sent = 0
        while sent < len(payload):
            sent += os.write(descriptor, payload[sent:])
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(BUNDLE_ROOT, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _await_launch_ack(
    *,
    run_id: str,
    controller_sha256: str,
    schedule_length: int,
    ready_status_sha256: str,
    expected_uid: int = 0,
    timeout_seconds: int = launch_protocol.ACK_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait for the root-owned acknowledgement of exact zero-call readiness."""

    expected = launch_protocol.acknowledgement(
        run_id=run_id,
        controller_sha256=controller_sha256,
        schedule_length=schedule_length,
        ready_status_sha256=ready_status_sha256,
    )
    deadline = clock() + timeout_seconds
    while True:
        if LAUNCH_ACK_PATH.exists():
            observed, payload = _read_launch_ack(expected_uid=expected_uid)
            try:
                launch_protocol.require_exact(observed, expected)
            except ValueError as exc:
                raise RuntimeError("launch acknowledgement binding is invalid") from exc
            confirmation = launch_protocol.confirmation(
                run_id=run_id,
                controller_sha256=controller_sha256,
                schedule_length=schedule_length,
                acknowledgement_sha256=launch_protocol.sha256(payload),
            )
            _publish_launch_confirmation(confirmation)
            confirmation_payload = launch_protocol.canonical_json_line(confirmation)
            expected_commit = launch_protocol.launch_commit(
                run_id=run_id,
                controller_sha256=controller_sha256,
                schedule_length=schedule_length,
                confirmation_sha256=launch_protocol.sha256(confirmation_payload),
            )
            commit_deadline = clock() + timeout_seconds
            while True:
                if LAUNCH_COMMIT_PATH.exists():
                    observed_commit, _commit_payload = _read_launch_commit(
                        expected_uid=expected_uid
                    )
                    try:
                        launch_protocol.require_exact(observed_commit, expected_commit)
                    except ValueError as exc:
                        raise RuntimeError("launch commit binding is invalid") from exc
                    return observed
                if clock() >= commit_deadline:
                    raise RuntimeError("launch commit timed out")
                sleeper(0.1)
        if clock() >= deadline:
            raise RuntimeError("launch acknowledgement timed out")
        sleeper(0.1)


def _write_terminal_failure(
    *,
    run_id: str,
    schedule_length: int,
    stage: str,
    witness: Mapping[str, Any],
) -> None:
    """Persist content-free progress when the sealed workflow terminates."""

    _write_status(
        {
            "schema_version": launch_protocol.STATUS_VERSION,
            "run_id": run_id,
            "stage": stage,
            "state": "failed",
            "schedule_position": witness["schedule_position"],
            "schedule_length": schedule_length,
            "model_calls_reserved": witness["model_calls_reserved"],
            "model_calls_closed": witness["model_calls_closed"],
            "updated_at": _clock(),
        }
    )


def _validated_progress(value: Mapping[str, Any]) -> dict[str, int]:
    fields = ("schedule_position", "model_calls_reserved", "model_calls_closed")
    if not isinstance(value, Mapping):
        raise RuntimeError("sealed witness progress is invalid")
    progress: dict[str, int] = {}
    for field in fields:
        observed = value.get(field)
        if type(observed) is not int or observed < 0:
            raise RuntimeError("sealed witness progress is invalid")
        progress[field] = observed
    if progress["model_calls_closed"] > progress["model_calls_reserved"]:
        raise RuntimeError("sealed witness progress is invalid")
    return progress


def _run_locked() -> None:
    controller_path = BUNDLE_ROOT / "controller.json"
    controller, controller_sha = a11b_postprocess._load_controller(controller_path)
    a11b_postprocess._verify_installed_postprocess_sources(
        controller=controller,
        bundle_root=BUNDLE_ROOT,
    )
    restricted = service.load_sealed_service(BUNDLE_ROOT, clock=_clock)
    executor = restricted._executor
    run_id = controller["run_id"]
    schedule_length = int(controller["inputs"]["answer_calls"])
    stage = "answers"
    last_progress: Mapping[str, Any] = {
        "schedule_position": 0,
        "model_calls_reserved": 0,
        "model_calls_closed": 0,
    }
    status: Mapping[str, Any] = dict(last_progress)
    try:
        while True:
            candidate = executor.status(run_id=run_id)["witness"]
            last_progress = _validated_progress(candidate)
            status = candidate
            ready_status_sha = _write_status(
                {
                    "schema_version": launch_protocol.STATUS_VERSION,
                    "run_id": run_id,
                    "stage": "answers",
                    "state": status["state"],
                    "schedule_position": status["schedule_position"],
                    "schedule_length": schedule_length,
                    "model_calls_reserved": status["model_calls_reserved"],
                    "model_calls_closed": status["model_calls_closed"],
                    "updated_at": _clock(),
                }
            )
            if (
                status["state"] == "active"
                and status["schedule_position"] == 0
                and status["model_calls_reserved"] == 0
                and status["model_calls_closed"] == 0
            ):
                _await_launch_ack(
                    run_id=run_id,
                    controller_sha256=controller_sha,
                    schedule_length=schedule_length,
                    ready_status_sha256=ready_status_sha,
                )
            if status["state"] == "complete":
                break
            if status["state"] != "active":
                raise RuntimeError("sealed answer witness reached an unsafe terminal state")
            result = executor.execute_next(run_id=run_id, expected_head=status["head"])
            if result.outcome not in {"accepted", "provider_failure"}:
                raise RuntimeError("sealed answer executor produced an unsafe outcome")

        stage = "postprocess"
        _write_status(
            {
                "schema_version": launch_protocol.STATUS_VERSION,
                "run_id": run_id,
                "stage": "postprocess",
                "state": "running",
                "schedule_position": schedule_length,
                "schedule_length": schedule_length,
                "model_calls_reserved": status["model_calls_reserved"],
                "model_calls_closed": status["model_calls_closed"],
                "updated_at": _clock(),
            }
        )
        final_manifest = a11b_postprocess.run_all(
            bundle_root=BUNDLE_ROOT,
            audit_root=AUDIT_ROOT,
            trusted_executor=executor,
        )
        _write_status(
            {
                "schema_version": launch_protocol.STATUS_VERSION,
                "run_id": run_id,
                "stage": "complete",
                "state": "complete",
                "schedule_position": schedule_length,
                "schedule_length": schedule_length,
                "model_calls_reserved": status["model_calls_reserved"],
                "model_calls_closed": status["model_calls_closed"],
                "promotion": final_manifest["promotion"],
                "updated_at": _clock(),
            }
        )
    except BaseException as original:
        try:
            candidate = executor.status(run_id=run_id)["witness"]
            last_progress = _validated_progress(candidate)
        except BaseException:
            pass
        try:
            _write_terminal_failure(
                run_id=run_id,
                schedule_length=schedule_length,
                stage=stage,
                witness=last_progress,
            )
        except BaseException:
            original.add_note("terminal failure receipt publication also failed")
        raise


def run() -> None:
    with run_lock.acquire_single_instance(LOCK_PATH):
        try:
            _run_locked()
        except BaseException as original:
            terminal_failure_exists = False
            try:
                status, _payload = _read_canonical_control(
                    STATUS_PATH,
                    expected_uid=os.geteuid(),
                    expected_mode=0o600,
                    label="nightly status",
                )
                terminal_failure_exists = status.get("state") == "failed"
            except BaseException:
                pass
            if not terminal_failure_exists:
                try:
                    _write_terminal_failure(
                        run_id="unavailable",
                        schedule_length=0,
                        stage="bootstrap",
                        witness={
                            "schedule_position": 0,
                            "model_calls_reserved": 0,
                            "model_calls_closed": 0,
                        },
                    )
                except BaseException:
                    original.add_note(
                        "bootstrap failure receipt publication also failed"
                    )
            raise


if __name__ == "__main__":
    run()
