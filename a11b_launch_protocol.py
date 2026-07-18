"""Canonical, content-free launch handshake shared by installer and runner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


STATUS_VERSION = "a11b-nightly-status-v1"
ACK_VERSION = "a11b-launch-ack-v1"
CONFIRMATION_VERSION = "a11b-launch-confirmation-v1"
COMMIT_VERSION = "a11b-launch-commit-v1"
READY_TIMEOUT_SECONDS = 60
ACK_TIMEOUT_SECONDS = 60
CONTROLLER_PROFILES = {
    "a11b-causal-isolation-v2": 1152,
    "a11b-successor-development-v1": 192,
}
STATUS_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "stage",
        "state",
        "schedule_position",
        "schedule_length",
        "model_calls_reserved",
        "model_calls_closed",
        "updated_at",
    }
)


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


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def readiness_status(
    *, run_id: str, schedule_length: int, updated_at: str
) -> dict[str, Any]:
    return {
        "schema_version": STATUS_VERSION,
        "run_id": run_id,
        "stage": "answers",
        "state": "active",
        "schedule_position": 0,
        "schedule_length": schedule_length,
        "model_calls_reserved": 0,
        "model_calls_closed": 0,
        "updated_at": updated_at,
    }


def validate_readiness(
    value: Mapping[str, Any], *, run_id: str, schedule_length: int
) -> dict[str, Any]:
    expected = readiness_status(
        run_id=run_id,
        schedule_length=schedule_length,
        updated_at=str(value.get("updated_at", "")),
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != STATUS_FIELDS
        or not isinstance(value.get("updated_at"), str)
        or not value["updated_at"].endswith("Z")
        or len(value["updated_at"].encode("utf-8")) > 64
        or dict(value) != expected
    ):
        raise ValueError("launch readiness binding is invalid")
    return dict(value)


def acknowledgement(
    *,
    run_id: str,
    controller_sha256: str,
    schedule_length: int,
    ready_status_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": ACK_VERSION,
        "run_id": run_id,
        "controller_sha256": controller_sha256,
        "schedule_length": schedule_length,
        "ready_status_sha256": ready_status_sha256,
    }


def confirmation(
    *,
    run_id: str,
    controller_sha256: str,
    schedule_length: int,
    acknowledgement_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": CONFIRMATION_VERSION,
        "run_id": run_id,
        "controller_sha256": controller_sha256,
        "schedule_length": schedule_length,
        "acknowledgement_sha256": acknowledgement_sha256,
    }


def launch_commit(
    *,
    run_id: str,
    controller_sha256: str,
    schedule_length: int,
    confirmation_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": COMMIT_VERSION,
        "run_id": run_id,
        "controller_sha256": controller_sha256,
        "schedule_length": schedule_length,
        "confirmation_sha256": confirmation_sha256,
    }


def require_exact(value: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or dict(value) != dict(expected):
        raise ValueError("launch handshake binding is invalid")
    return dict(value)


def validate_controller_profile(value: Mapping[str, Any]) -> None:
    profile = value.get("experiment_profile")
    inputs = value.get("inputs")
    answer_calls = inputs.get("answer_calls") if isinstance(inputs, Mapping) else None
    if CONTROLLER_PROFILES.get(profile) != answer_calls:
        raise ValueError("controller profile and schedule are incompatible")
