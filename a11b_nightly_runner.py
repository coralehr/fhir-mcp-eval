#!/usr/bin/env python3
"""Run the sealed A11b answer, grading, panel, and finalization workflow."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path
from typing import Any

import a11b_postprocess
import experiment_executor_service as service
import run_lock


BUNDLE_ROOT = service.PRODUCTION_BUNDLE_DIR
AUDIT_ROOT = BUNDLE_ROOT / "audit-input"
STATUS_PATH = BUNDLE_ROOT / "nightly-status.json"
LOCK_PATH = BUNDLE_ROOT / "nightly-runner.lock"


def _clock() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write_status(value: dict[str, Any]) -> None:
    payload = service.canonical_json_line(value)
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


def _run_locked() -> None:
    controller_path = BUNDLE_ROOT / "controller.json"
    controller, _controller_sha = a11b_postprocess._load_controller(controller_path)
    a11b_postprocess._verify_installed_postprocess_sources(
        controller=controller,
        bundle_root=BUNDLE_ROOT,
    )
    restricted = service.load_sealed_service(BUNDLE_ROOT, clock=_clock)
    executor = restricted._executor
    run_id = controller["run_id"]
    while True:
        status = executor.status(run_id=run_id)["witness"]
        _write_status(
            {
                "schema_version": "a11b-nightly-status-v1",
                "run_id": run_id,
                "stage": "answers",
                "state": status["state"],
                "schedule_position": status["schedule_position"],
                "schedule_length": 1152,
                "model_calls_reserved": status["model_calls_reserved"],
                "model_calls_closed": status["model_calls_closed"],
                "updated_at": _clock(),
            }
        )
        if status["state"] == "complete":
            break
        if status["state"] != "active":
            raise RuntimeError("sealed answer witness reached an unsafe terminal state")
        result = executor.execute_next(run_id=run_id, expected_head=status["head"])
        if result.outcome not in {"accepted", "provider_failure"}:
            raise RuntimeError("sealed answer executor produced an unsafe outcome")

    _write_status(
        {
            "schema_version": "a11b-nightly-status-v1",
            "run_id": run_id,
            "stage": "postprocess",
            "state": "running",
            "schedule_position": 1152,
            "schedule_length": 1152,
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
            "schema_version": "a11b-nightly-status-v1",
            "run_id": run_id,
            "stage": "complete",
            "state": "complete",
            "schedule_position": 1152,
            "schedule_length": 1152,
            "model_calls_reserved": status["model_calls_reserved"],
            "model_calls_closed": status["model_calls_closed"],
            "promotion": final_manifest["promotion"],
            "updated_at": _clock(),
        }
    )


def run() -> None:
    with run_lock.acquire_single_instance(LOCK_PATH):
        _run_locked()


if __name__ == "__main__":
    run()
