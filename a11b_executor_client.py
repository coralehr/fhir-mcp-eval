#!/usr/bin/env python3
"""Content-free localhost client for the sealed A11b executor service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import experiment_executor_service as service


def canonical_request(value: dict[str, Any]) -> bytes:
    return service.canonical_json_line(
        {
            "kind": service.SERVICE_REQUEST_KIND,
            "schema_version": service.SERVICE_SCHEMA_VERSION,
            **value,
        }
    )


def _call_service(*, key: Path, request: bytes, timeout: int) -> dict[str, Any]:
    process = subprocess.run(
        [
            "/usr/bin/ssh",
            "-T",
            "-i",
            str(key),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ConnectTimeout=20",
            "_coralexp@127.0.0.1",
        ],
        input=request,
        capture_output=True,
        check=False,
        timeout=timeout,
        env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if process.returncode != 0 or process.stderr:
        raise RuntimeError("restricted executor transport failed")
    try:
        response = json.loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("restricted executor response is invalid") from exc
    if not isinstance(response, dict) or service.canonical_json_line(response) != process.stdout:
        raise RuntimeError("restricted executor response is noncanonical")
    return response


def _write_exclusive(path: Path, value: object) -> None:
    payload = service.canonical_json_line(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        sent = 0
        while sent < len(payload):
            sent += os.write(descriptor, payload[sent:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_canonical(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("local executor receipt is invalid") from exc
    if not isinstance(value, dict) or service.canonical_json_line(value) != payload:
        raise RuntimeError("local executor receipt is noncanonical")
    return value


def _validated_execution_result(
    response: dict[str, Any],
    *,
    run_id: str,
    expected_head: str,
) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise RuntimeError("restricted executor stopped on a protocol error")
    result = response.get("result")
    if (
        not isinstance(result, dict)
        or result.get("kind") != "executor_call_result"
        or result.get("run_id") != run_id
        or result.get("request_head") != expected_head
        or not isinstance(result.get("witness_head"), str)
        or len(result["witness_head"]) != 64
    ):
        raise RuntimeError("restricted executor result changed")
    attempt = result.get("closed_receipt", {}).get("body", {}).get(
        "attempt_number"
    )
    if type(attempt) is not int or attempt <= 0:
        raise RuntimeError("restricted executor attempt identity changed")
    return result


def _execute_with_intent(
    *,
    key: Path,
    output: Path,
    run_id: str,
    position: int,
    expected_head: str,
) -> dict[str, Any]:
    """Persist the request head before transport and replay it after lost ACKs."""

    stem = f"execute-{position:04d}-{expected_head}"
    intent_path = output / f"{stem}.intent.json"
    result_path = output / f"{stem}.result.json"
    intent = {
        "kind": "a11b_executor_intent",
        "schema_version": "a11b-executor-intent-v1",
        "run_id": run_id,
        "schedule_position": position,
        "expected_head": expected_head,
    }
    if intent_path.exists():
        if _read_canonical(intent_path) != intent:
            raise RuntimeError("local executor intent changed")
    else:
        _write_exclusive(intent_path, intent)
    if result_path.exists():
        persisted = _read_canonical(result_path)
        return _validated_execution_result(
            persisted,
            run_id=run_id,
            expected_head=expected_head,
        )
    response = _call_service(
        key=key,
        request=canonical_request(
            {
                "operation": "execute_next",
                "run_id": run_id,
                "expected_head": expected_head,
            }
        ),
        timeout=960,
    )
    result = _validated_execution_result(
        response,
        run_id=run_id,
        expected_head=expected_head,
    )
    _write_exclusive(result_path, response)
    return result


def _reconcile_pending_intents(*, key: Path, output: Path, run_id: str) -> None:
    for intent_path in sorted(output.glob("execute-*.intent.json")):
        intent = _read_canonical(intent_path)
        if (
            intent.get("kind") != "a11b_executor_intent"
            or intent.get("schema_version") != "a11b-executor-intent-v1"
            or intent.get("run_id") != run_id
            or type(intent.get("schedule_position")) is not int
            or not isinstance(intent.get("expected_head"), str)
        ):
            raise RuntimeError("local executor intent inventory changed")
        result_path = intent_path.with_name(
            intent_path.name.removesuffix(".intent.json") + ".result.json"
        )
        if not result_path.exists():
            _execute_with_intent(
                key=key,
                output=output,
                run_id=run_id,
                position=intent["schedule_position"],
                expected_head=intent["expected_head"],
            )


def run(*, controller: Path, key: Path, output: Path) -> None:
    controller_bytes = controller.read_bytes()
    manifest = json.loads(controller_bytes)
    run_id = manifest.get("run_id")
    schedule = manifest.get("schedule", {}).get("items")
    if (
        manifest.get("experiment_profile") != "a11b-causal-isolation-v2"
        or not isinstance(run_id, str)
        or len(run_id) != 64
        or not isinstance(schedule, list)
        or len(schedule) != 1152
    ):
        raise ValueError("A11b controller is invalid")
    sidecar = controller.with_suffix(".sha256")
    if sidecar.read_text(encoding="ascii") != hashlib.sha256(controller_bytes).hexdigest() + "\n":
        raise ValueError("A11b controller sidecar changed")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reconcile_pending_intents(key=key, output=output, run_id=run_id)
    status = _call_service(
        key=key,
        request=canonical_request({"operation": "status", "run_id": run_id}),
        timeout=60,
    )
    if status.get("ok") is not True:
        raise RuntimeError("restricted executor status failed")
    current = status["result"]
    position = current.get("schedule_position")
    head = current.get("witness_head")
    if type(position) is not int or not 0 <= position <= len(schedule) or not isinstance(head, str):
        raise RuntimeError("restricted executor status changed")
    while position < len(schedule):
        result = _execute_with_intent(
            key=key,
            output=output,
            run_id=run_id,
            position=position,
            expected_head=head,
        )
        head = result.get("witness_head")
        if not isinstance(head, str) or len(head) != 64:
            raise RuntimeError("restricted executor witness head changed")
        status = _call_service(
            key=key,
            request=canonical_request({"operation": "status", "run_id": run_id}),
            timeout=60,
        )
        if status.get("ok") is not True:
            raise RuntimeError("restricted executor status failed after execution")
        status_result = status["result"]
        next_position = status_result.get("schedule_position")
        if type(next_position) is not int or not position <= next_position <= position + 1:
            raise RuntimeError("restricted executor schedule position changed unexpectedly")
        if status_result.get("witness_head") != head:
            raise RuntimeError("restricted executor status witness head diverged")
        state = status_result.get("state")
        if state == "aborted" or result.get("outcome") in {
            "contaminated",
            "indeterminate",
        }:
            raise RuntimeError("restricted executor reached a terminal unsafe state")
        position = next_position


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(controller=args.controller, key=args.key, output=args.output)


if __name__ == "__main__":
    main()
