#!/usr/bin/env python3
"""Run the exact A11b r3 answer prompts as an unregistered exploratory preview.

This runner deliberately cannot produce or write a registered A11b result. It
keeps preview artifacts outside the official executor paths, labels every
receipt as unregistered, validates exact r3 prompt hashes, and rejects tool use
or schema-invalid answers.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import codex_harness


CONTROLLER_SHA256 = "86f1bf8e3d8500c76504154f1c1c25d5b31afb499006317d9e2deb104bae8caf"
BUNDLE_SHA256 = "21fe8fc13d47aec88339bdaecab14a5fb369a9fa73ec187cf81220e5f527ec64"
EXPECTED_CALLS = 1152
EXPECTED_ARMS = ("t0", "t1", "e1")
TOKEN_KEYS = ("input", "cached", "output", "reasoning", "total")
MAX_ATTEMPTS = 6
SCHEMA_TRANSPORT_PATCH = "structural_transport_full_registered_contract_offline"
NORMALIZABLE_SCHEMA_ERROR = "answer did not match sealed schema"
UNSUPPORTED_TRANSPORT_SCHEMA_KEYS = {
    "$schema",
    "title",
    "oneOf",
    "not",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
}


class PreviewError(RuntimeError):
    """The unregistered preview cannot safely continue."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise PreviewError(f"JSON artifact is not an object: {path.name}")
    return value


def _atomic_write(path: Path, value: object) -> None:
    payload = _canonical(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _transport_schema(original: bytes) -> bytes:
    """Reduce the model transport to its supported structural subset.

    Answers are still validated offline against the original full schema, so
    only documents satisfying the registered contract can be accepted.
    """

    schema = json.loads(original)
    if (
        not isinstance(schema, dict)
        or "oneOf" not in schema
        or not isinstance(schema["oneOf"], list)
        or len(schema["oneOf"]) != 2
    ):
        raise PreviewError("answer schema is not the registered oneOf contract")

    def structural(value: object, *, property_map: bool = False) -> object:
        if isinstance(value, list):
            return [structural(item) for item in value]
        if not isinstance(value, dict):
            return value
        if property_map:
            return {key: structural(child) for key, child in value.items()}
        return {
            key: structural(child, property_map=key == "properties")
            for key, child in value.items()
            if key not in UNSUPPORTED_TRANSPORT_SCHEMA_KEYS
        }

    schema = structural(schema)
    if not isinstance(schema, dict):
        raise PreviewError("structural transport schema is not an object")
    return _canonical(schema)


def _normalize_substantive_answer(
    *, answer_path: Path, schema_path: Path, normalized_path: Path
) -> dict[str, Any] | None:
    try:
        answer_payload = answer_path.read_bytes()
        answer = json.loads(answer_payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(answer, dict)
        or not isinstance(answer.get("answer"), str)
        or not answer["answer"]
        or answer["answer"] == "Insufficient evidence."
        or not isinstance(answer.get("insufficiency_reason"), str)
        or not answer["insufficiency_reason"]
    ):
        return None
    normalized = dict(answer)
    normalized["insufficiency_reason"] = None
    normalized_payload = _canonical(normalized)
    if normalized_path.exists():
        if normalized_path.read_bytes() != normalized_payload:
            raise PreviewError("normalized answer artifact changed")
    else:
        normalized_path.write_bytes(normalized_payload)
        normalized_path.chmod(0o400)
    if not codex_harness.answer_matches_schema(normalized_path, schema_path):
        normalized_path.unlink(missing_ok=True)
        return None
    return {
        "kind": "a11b_unregistered_deterministic_normalization",
        "schema_version": "a11b-unregistered-normalization-v1",
        "registered": False,
        "transformation": "substantive_nonnull_reason_to_null",
        "raw_answer_sha256": _sha256(answer_payload),
        "normalized_answer_sha256": _sha256(normalized_payload),
        "registered_schema_sha256": _sha256(schema_path.read_bytes()),
        "answer_content_in_receipt": False,
    }


def _write_immutable_json(path: Path, value: object) -> None:
    payload = _canonical(value)
    if path.exists():
        if path.read_bytes() != payload:
            raise PreviewError(f"immutable artifact changed: {path.name}")
        return
    _atomic_write(path, value)


def _acceptance_marker(
    *,
    index: int,
    attempt_number: int,
    prompt_sha256: str,
    registered_schema: bytes,
    transport_schema: bytes,
    acceptance_mode: str,
    normalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "kind": "a11b_unregistered_exploratory_acceptance",
        "schema_version": "a11b-unregistered-preview-acceptance-v1",
        "registered": False,
        "schedule_index": index,
        "attempt_number": attempt_number,
        "controller_sha256": CONTROLLER_SHA256,
        "bundle_sha256": BUNDLE_SHA256,
        "prompt_sha256": prompt_sha256,
        "registered_schema_sha256": _sha256(registered_schema),
        "transport_schema_sha256": _sha256(transport_schema),
        "schema_transport_patch": SCHEMA_TRANSPORT_PATCH,
        "acceptance_mode": acceptance_mode,
        "answer_artifact": "answer.json",
    }
    if acceptance_mode == "deterministic_normalization":
        if normalization is None:
            raise PreviewError("normalization receipt is required")
        marker["answer_artifact"] = "normalized-answer.json"
        marker["normalized_answer_sha256"] = normalization[
            "normalized_answer_sha256"
        ]
    elif acceptance_mode != "direct":
        raise PreviewError("unknown acceptance mode")
    return marker


def _normalize_attempt(
    *, attempt_dir: Path, registered_schema_path: Path
) -> dict[str, Any] | None:
    normalization = _normalize_substantive_answer(
        answer_path=attempt_dir / "answer.json",
        schema_path=registered_schema_path,
        normalized_path=attempt_dir / "normalized-answer.json",
    )
    if normalization is None:
        return None
    _write_immutable_json(attempt_dir / "normalization.json", normalization)
    return normalization


def _recover_normalized_attempt(
    *,
    slot_dir: Path,
    attempt_dir: Path,
    receipt: dict[str, Any],
    index: int,
    attempt_number: int,
    prompt_sha256: str,
    registered_schema: bytes,
    transport_schema: bytes,
) -> bool:
    """Accept a prior model result only through the narrow offline adapter."""

    if (
        receipt.get("outcome") != "provider_failure"
        or receipt.get("error") != NORMALIZABLE_SCHEMA_ERROR
    ):
        return False
    registered_schema_path = attempt_dir / "registered-schema.json"
    transport_schema_path = attempt_dir / "transport-schema.json"
    answer_path = attempt_dir / "answer.json"
    event_path = attempt_dir / "events.jsonl"
    stderr_path = attempt_dir / "stderr.log"
    for name, expected in (
        ("answer.json", None),
        ("events.jsonl", None),
        ("stderr.log", None),
        ("registered-schema.json", _sha256(registered_schema)),
        ("transport-schema.json", _sha256(transport_schema)),
    ):
        path = attempt_dir / name
        payload = path.read_bytes()
        observed = {"sha256": _sha256(payload), "bytes": len(payload)}
        recorded = receipt.get(name)
        if recorded is None and expected is None:
            raise PreviewError(f"prior attempt receipt omitted: {name}")
        if recorded is not None and recorded != observed:
            raise PreviewError(f"prior attempt artifact changed: {name}")
        if expected is not None and observed["sha256"] != expected:
            raise PreviewError(f"prior attempt schema changed: {name}")
    if codex_harness.answer_matches_schema(answer_path, registered_schema_path):
        raise PreviewError("normalization recovery found an already-valid answer")
    codex_harness.enforce_packet_event_integrity(
        event_log_path=event_path, answer_path=answer_path
    )
    if stderr_path.read_bytes():
        raise PreviewError("normalization recovery found nonempty stderr")
    normalization = _normalize_attempt(
        attempt_dir=attempt_dir,
        registered_schema_path=registered_schema_path,
    )
    if normalization is None:
        return False
    marker = _acceptance_marker(
        index=index,
        attempt_number=attempt_number,
        prompt_sha256=prompt_sha256,
        registered_schema=registered_schema,
        transport_schema=transport_schema,
        acceptance_mode="deterministic_normalization",
        normalization=normalization,
    )
    _write_immutable_json(slot_dir / "accepted.json", marker)
    return True


def _load_inputs(
    controller_path: Path, bundle_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    controller_payload = controller_path.read_bytes()
    bundle_payload = bundle_path.read_bytes()
    if _sha256(controller_payload) != CONTROLLER_SHA256:
        raise PreviewError("controller is not exact r3")
    sidecar = controller_path.with_suffix(".sha256").read_text(encoding="ascii")
    if sidecar != CONTROLLER_SHA256 + "\n":
        raise PreviewError("controller sidecar is not exact r3")
    if _sha256(bundle_payload) != BUNDLE_SHA256:
        raise PreviewError("bundle is not exact r3")
    controller = json.loads(controller_payload)
    bundle = json.loads(bundle_payload)
    schedule = controller.get("schedule", {}).get("items") if isinstance(controller, dict) else None
    invocations = bundle.get("invocations") if isinstance(bundle, dict) else None
    if (
        not isinstance(controller, dict)
        or controller.get("schema_version") != "a11-controller-v4"
        or controller.get("experiment_profile") != "a11b-causal-isolation-v2"
        or not isinstance(schedule, list)
        or not isinstance(invocations, list)
        or len(schedule) != EXPECTED_CALLS
        or len(invocations) != EXPECTED_CALLS
    ):
        raise PreviewError("r3 schedule coverage changed")
    seen: set[tuple[str, str]] = set()
    for index, (host, invocation) in enumerate(zip(schedule, invocations, strict=True)):
        if not isinstance(host, dict) or not isinstance(invocation, dict):
            raise PreviewError("r3 schedule entry changed")
        arm = host.get("arm")
        question_id = host.get("question_id")
        if (
            host.get("schedule_index") != index
            or invocation.get("schedule_index") != index
            or arm not in EXPECTED_ARMS
            or not isinstance(question_id, str)
            or (arm, question_id) in seen
        ):
            raise PreviewError("r3 schedule identity changed")
        seen.add((arm, question_id))
        try:
            prompt = base64.b64decode(invocation["prompt_base64"], validate=True)
            schema = base64.b64decode(
                invocation["output_schema_base64"], validate=True
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PreviewError("r3 invocation encoding changed") from exc
        if _sha256(prompt) != host.get("prompt_sha256"):
            raise PreviewError("r3 prompt receipt changed")
        if _sha256(schema) != controller.get("grading", {}).get(
            "answer_schema_sha256"
        ):
            raise PreviewError("r3 answer schema receipt changed")
        _transport_schema(schema)
    return controller, invocations


def _usage_from_event(path: Path) -> dict[str, int] | None:
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    completions = [event for event in events if event.get("type") == "turn.completed"]
    if len(completions) != 1:
        return None
    value = completions[0].get("usage")
    if not isinstance(value, Mapping):
        return None
    aliases = {
        "input": "input_tokens",
        "cached": "cached_input_tokens",
        "output": "output_tokens",
        "reasoning": "reasoning_output_tokens",
    }
    usage = {target: value.get(source) for target, source in aliases.items()}
    if any(type(item) is not int or item < 0 for item in usage.values()):
        return None
    usage["total"] = usage["input"] + usage["output"]
    return usage


def _receipt_for_attempt(
    *, index: int, attempt_number: int, attempt_dir: Path, outcome: str, error: str | None
) -> dict[str, Any]:
    usage = _usage_from_event(attempt_dir / "events.jsonl")
    result: dict[str, Any] = {
        "kind": "a11b_unregistered_exploratory_attempt",
        "schema_version": "a11b-unregistered-preview-attempt-v1",
        "registered": False,
        "schedule_index": index,
        "attempt_number": attempt_number,
        "outcome": outcome,
        "token_usage": usage,
    }
    if error is not None:
        result["error"] = error
    for name in (
        "answer.json",
        "events.jsonl",
        "stderr.log",
        "registered-schema.json",
        "transport-schema.json",
        "normalized-answer.json",
        "normalization.json",
    ):
        path = attempt_dir / name
        if path.is_file():
            payload = path.read_bytes()
            result[name] = {"sha256": _sha256(payload), "bytes": len(payload)}
    return result


def _accepted_marker_valid(
    slot_dir: Path, index: int, prompt_sha256: str
) -> bool:
    marker_path = slot_dir / "accepted.json"
    if not marker_path.is_file():
        return False
    try:
        marker = _read_object(marker_path)
        attempt_dir = slot_dir / f"attempt-{marker['attempt_number']}"
        if (
            marker.get("kind") != "a11b_unregistered_exploratory_acceptance"
            or marker.get("registered") is not False
            or marker.get("schedule_index") != index
            or marker.get("controller_sha256") != CONTROLLER_SHA256
            or marker.get("bundle_sha256") != BUNDLE_SHA256
            or marker.get("prompt_sha256") != prompt_sha256
            or marker.get("schema_transport_patch") != SCHEMA_TRANSPORT_PATCH
        ):
            return False
        receipt = _read_object(attempt_dir / "receipt.json")
        acceptance_mode = marker.get("acceptance_mode", "direct")
        if acceptance_mode == "direct":
            if (
                receipt.get("outcome") != "accepted"
                or marker.get("answer_artifact", "answer.json") != "answer.json"
            ):
                return False
        elif acceptance_mode == "deterministic_normalization":
            if receipt.get("outcome") not in {"accepted", "provider_failure"}:
                return False
            if (
                receipt.get("outcome") == "provider_failure"
                and receipt.get("error") != NORMALIZABLE_SCHEMA_ERROR
            ):
                return False
            normalization = _read_object(attempt_dir / "normalization.json")
            raw_answer = _read_object(attempt_dir / "answer.json")
            normalized_payload = (attempt_dir / "normalized-answer.json").read_bytes()
            expected_normalized = dict(raw_answer)
            expected_normalized["insufficiency_reason"] = None
            if (
                marker.get("answer_artifact") != "normalized-answer.json"
                or normalization.get("kind")
                != "a11b_unregistered_deterministic_normalization"
                or normalization.get("registered") is not False
                or normalization.get("transformation")
                != "substantive_nonnull_reason_to_null"
                or normalization.get("raw_answer_sha256")
                != receipt.get("answer.json", {}).get("sha256")
                or normalization.get("normalized_answer_sha256")
                != _sha256(normalized_payload)
                or normalization.get("registered_schema_sha256")
                != marker.get("registered_schema_sha256")
                or marker.get("normalized_answer_sha256")
                != _sha256(normalized_payload)
                or normalized_payload != _canonical(expected_normalized)
                or not codex_harness.answer_matches_schema(
                    attempt_dir / "normalized-answer.json",
                    attempt_dir / "registered-schema.json",
                )
            ):
                return False
        else:
            return False
        for name in ("answer.json", "events.jsonl", "stderr.log"):
            payload = (attempt_dir / name).read_bytes()
            if receipt.get(name) != {"sha256": _sha256(payload), "bytes": len(payload)}:
                return False
        schema_receipts = {
            "registered-schema.json": marker.get("registered_schema_sha256"),
            "transport-schema.json": marker.get("transport_schema_sha256"),
        }
        for name, expected_sha in schema_receipts.items():
            payload = (attempt_dir / name).read_bytes()
            observed = {"sha256": _sha256(payload), "bytes": len(payload)}
            if (
                receipt.get(name, observed) != observed
                or observed["sha256"] != expected_sha
            ):
                return False
        if acceptance_mode == "deterministic_normalization":
            for name in ("normalized-answer.json", "normalization.json"):
                payload = (attempt_dir / name).read_bytes()
                observed = {"sha256": _sha256(payload), "bytes": len(payload)}
                recorded = receipt.get(name)
                if recorded is not None and recorded != observed:
                    return False
        return True
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def _status(root: Path, schedule: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = 0
    attempts = 0
    accepted_by_arm = {arm: 0 for arm in EXPECTED_ARMS}
    deterministic_normalizations = 0
    accepted_tokens = {key: 0 for key in TOKEN_KEYS}
    all_attempt_tokens = {key: 0 for key in TOKEN_KEYS}
    unknown_usage_attempts = 0
    for index, host in enumerate(schedule):
        slot_dir = root / "slots" / f"{index:04d}"
        for receipt_path in sorted(slot_dir.glob("attempt-*/receipt.json")):
            receipt = _read_object(receipt_path)
            attempts += 1
            usage = receipt.get("token_usage")
            if not isinstance(usage, dict) or any(
                type(usage.get(key)) is not int for key in TOKEN_KEYS
            ):
                unknown_usage_attempts += 1
            else:
                for key in TOKEN_KEYS:
                    all_attempt_tokens[key] += usage[key]
                if receipt.get("outcome") == "accepted":
                    for key in TOKEN_KEYS:
                        accepted_tokens[key] += usage[key]
        if _accepted_marker_valid(slot_dir, index, host["prompt_sha256"]):
            accepted += 1
            accepted_by_arm[host["arm"]] += 1
            marker = _read_object(slot_dir / "accepted.json")
            if marker.get("acceptance_mode") == "deterministic_normalization":
                deterministic_normalizations += 1
                receipt = _read_object(
                    slot_dir
                    / f"attempt-{marker['attempt_number']}"
                    / "receipt.json"
                )
                if receipt.get("outcome") != "accepted":
                    usage = receipt.get("token_usage")
                    if isinstance(usage, dict) and all(
                        type(usage.get(key)) is int for key in TOKEN_KEYS
                    ):
                        for key in TOKEN_KEYS:
                            accepted_tokens[key] += usage[key]
    return {
        "kind": "a11b_unregistered_exploratory_status",
        "schema_version": "a11b-unregistered-preview-status-v2",
        "registered": False,
        "controller_sha256": CONTROLLER_SHA256,
        "bundle_sha256": BUNDLE_SHA256,
        "scheduled_calls": EXPECTED_CALLS,
        "accepted_calls": accepted,
        "remaining_calls": EXPECTED_CALLS - accepted,
        "attempts": attempts,
        "accepted_by_arm": accepted_by_arm,
        "deterministic_normalizations": deterministic_normalizations,
        "accepted_tokens": accepted_tokens,
        "all_attempt_tokens": all_attempt_tokens,
        "unknown_usage_attempts": unknown_usage_attempts,
        "complete": accepted == EXPECTED_CALLS,
        "off_channel_answer_exposure": "not_measured",
    }


def run(
    *, controller_path: Path, bundle_path: Path, codex_bin: Path, root: Path
) -> None:
    controller, invocations = _load_inputs(controller_path, bundle_path)
    schedule = controller["schedule"]["items"]
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root_status = root.lstat()
    if root.is_symlink() or not root.is_dir() or root_status.st_mode & 0o077:
        raise PreviewError("preview root is not a private directory")
    (root / "slots").mkdir(exist_ok=True, mode=0o700)
    sentinel = {
        "kind": "a11b_unregistered_exploratory_preview",
        "schema_version": "a11b-unregistered-preview-v1",
        "registered": False,
        "confirmatory_use_prohibited": True,
        "retroactive_approval_prohibited": True,
        "schema_transport_patch": SCHEMA_TRANSPORT_PATCH,
        "controller_sha256": CONTROLLER_SHA256,
        "bundle_sha256": BUNDLE_SHA256,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    sentinel_path = root / "UNREGISTERED_EXPLORATORY_PREVIEW.json"
    if sentinel_path.exists():
        existing = _read_object(sentinel_path)
        if {key: existing.get(key) for key in sentinel if key != "created_at"} != {
            key: value for key, value in sentinel.items() if key != "created_at"
        }:
            raise PreviewError("preview sentinel changed")
    else:
        _atomic_write(sentinel_path, sentinel)
    _write_immutable_json(
        root / "UNREGISTERED_DETERMINISTIC_NORMALIZATION.json",
        {
            "kind": "a11b_unregistered_deterministic_normalization_policy",
            "schema_version": "a11b-unregistered-normalization-policy-v1",
            "registered": False,
            "confirmatory_use_prohibited": True,
            "source_answer_mutated": False,
            "changed_field": "insufficiency_reason",
            "replacement": None,
            "eligibility": (
                "substantive answer with a nonempty insufficiency reason whose "
                "one-field normalization satisfies the full registered schema"
            ),
            "all_other_schema_failures": "rejected",
        },
    )
    lock_path = root / "runner.lock"
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PreviewError("another preview runner is already active") from exc
        for index, (host, invocation) in enumerate(zip(schedule, invocations, strict=True)):
            slot_dir = root / "slots" / f"{index:04d}"
            slot_dir.mkdir(exist_ok=True, mode=0o700)
            if _accepted_marker_valid(slot_dir, index, host["prompt_sha256"]):
                continue
            prompt = base64.b64decode(invocation["prompt_base64"], validate=True).decode("utf-8")
            schema = base64.b64decode(invocation["output_schema_base64"], validate=True)
            transport_schema = _transport_schema(schema)
            if _sha256(prompt.encode()) != host["prompt_sha256"]:
                raise PreviewError("prompt changed immediately before execution")
            accepted = False
            for attempt_number in range(1, MAX_ATTEMPTS + 1):
                attempt_dir = slot_dir / f"attempt-{attempt_number}"
                receipt_path = attempt_dir / "receipt.json"
                if receipt_path.exists():
                    receipt = _read_object(receipt_path)
                    if receipt.get("outcome") == "accepted":
                        raise PreviewError(
                            "accepted receipt exists without a valid acceptance marker"
                        )
                    if _recover_normalized_attempt(
                        slot_dir=slot_dir,
                        attempt_dir=attempt_dir,
                        receipt=receipt,
                        index=index,
                        attempt_number=attempt_number,
                        prompt_sha256=host["prompt_sha256"],
                        registered_schema=schema,
                        transport_schema=transport_schema,
                    ):
                        accepted = True
                        break
                    continue
                if attempt_dir.exists():
                    receipt = _receipt_for_attempt(
                        index=index,
                        attempt_number=attempt_number,
                        attempt_dir=attempt_dir,
                        outcome="provider_failure",
                        error="interrupted attempt recovered without rerunning",
                    )
                    _atomic_write(receipt_path, receipt)
                    continue
                attempt_dir.mkdir(mode=0o700)
                validation_schema_path = attempt_dir / "registered-schema.json"
                validation_schema_path.write_bytes(schema)
                validation_schema_path.chmod(0o400)
                transport_schema_path = attempt_dir / "transport-schema.json"
                transport_schema_path.write_bytes(transport_schema)
                transport_schema_path.chmod(0o400)
                answer_path = attempt_dir / "answer.json"
                event_path = attempt_dir / "events.jsonl"
                error: str | None = None
                outcome = "provider_failure"
                acceptance_mode = "direct"
                normalization: dict[str, Any] | None = None
                with tempfile.TemporaryDirectory(prefix="a11b-preview-") as directory:
                    command = codex_harness.build_codex_command(
                        prompt=prompt,
                        schema_path=transport_schema_path,
                        output_path=answer_path,
                        event_log_path=event_path,
                        cwd=Path(directory),
                        codex_bin=str(codex_bin),
                        model=invocation["model"],
                        reasoning_effort=invocation["reasoning_effort"],
                        sandbox="read-only",
                        approval="never",
                    )
                    result = codex_harness.run_question(
                        command,
                        prompt,
                        timeout=invocation["timeout_seconds"],
                        dry_run=False,
                    )
                try:
                    if result.get("status") != "ok":
                        raise PreviewError(str(result.get("error", result.get("status"))))
                    if not codex_harness.answer_matches_schema(
                        answer_path, validation_schema_path
                    ):
                        normalization = _normalize_attempt(
                            attempt_dir=attempt_dir,
                            registered_schema_path=validation_schema_path,
                        )
                        if normalization is None:
                            raise PreviewError(NORMALIZABLE_SCHEMA_ERROR)
                        acceptance_mode = "deterministic_normalization"
                    codex_harness.enforce_packet_event_integrity(
                        event_log_path=event_path, answer_path=answer_path
                    )
                    if (attempt_dir / "stderr.log").read_bytes():
                        raise PreviewError("accepted stderr was not empty")
                    outcome = "accepted"
                except (OSError, ValueError, PreviewError) as exc:
                    error = str(exc)
                receipt = _receipt_for_attempt(
                    index=index,
                    attempt_number=attempt_number,
                    attempt_dir=attempt_dir,
                    outcome=outcome,
                    error=error,
                )
                _atomic_write(receipt_path, receipt)
                if outcome == "accepted":
                    marker = _acceptance_marker(
                        index=index,
                        attempt_number=attempt_number,
                        prompt_sha256=host["prompt_sha256"],
                        registered_schema=schema,
                        transport_schema=transport_schema,
                        acceptance_mode=acceptance_mode,
                        normalization=normalization,
                    )
                    _write_immutable_json(slot_dir / "accepted.json", marker)
                    accepted = True
                    break
            status = _status(root, schedule)
            _atomic_write(root / "status.json", status)
            print(
                json.dumps(
                    {
                        "accepted_calls": status["accepted_calls"],
                        "remaining_calls": status["remaining_calls"],
                        "attempts": status["attempts"],
                        "registered": False,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if not accepted:
                raise PreviewError(
                    f"slot {index} exhausted {MAX_ATTEMPTS} attempts"
                )
    finally:
        os.close(lock_descriptor)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--codex-bin", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    controller, _invocations = _load_inputs(args.controller, args.bundle)
    if args.status:
        print(json.dumps(_status(args.root, controller["schedule"]["items"]), sort_keys=True))
        return
    run(
        controller_path=args.controller,
        bundle_path=args.bundle,
        codex_bin=args.codex_bin,
        root=args.root,
    )


if __name__ == "__main__":
    try:
        main()
    except PreviewError as exc:
        print(json.dumps({"error": str(exc), "registered": False}), file=sys.stderr)
        raise SystemExit(1)
