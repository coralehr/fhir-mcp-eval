#!/usr/bin/env python3
"""Fail-closed grading, registered contrasts, and economics for QT-4.

This module deliberately performs no model calls. ``grade_qt4.py`` uses it to
apply the deterministic grader once to each sealed arm and create one queue for
``panel_grade.py``. ``final_qt4_result.py`` then verifies the fully-voted,
opaque-ID panel cache and assembles the two registered contrasts.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import codex_harness
import grade_a6a_confirmatory
import paired_stats
import panel_grade


ANALYSIS_VERSION = "qt4-three-arm-analysis-v3"
ARM_NAMES = ("a6a", "qt4v", "qt4t")
REGISTERED_CONTRASTS = (
    ("qt4v_minus_a6a", "qt4v", "a6a"),
    ("qt4t_minus_qt4v", "qt4t", "qt4v"),
)
CONTROLLER_KIND = "qt4_interleaved_controller_manifest"
CONTROLLER_SCHEMA_VERSION = "qt4-controller-v3"
COMPLETION_KIND = "qt4_attempt_completion"
COMPLETION_SCHEMA_VERSION = "qt4-attempt-v3"
REGISTERED_TRANSPORT_PROTOCOL = "separated-stdout-jsonl-stderr-v2"
MAX_ATTEMPTS_PER_ITEM = 3
REGISTERED_PANEL_VOTES = 3
REGISTERED_PANEL_MODEL = "gpt-5.6-sol"
REGISTERED_PANEL_EFFORT = "high"
REGISTERED_PANEL_BATCH_SIZE = 20
REGISTERED_PANEL_TIMEOUT = 600
EXPERIMENT_PROFILES = {
    "micro42": {
        "spec_kind": "qt4_micro_question_spec",
        "spec_version": "qt4-micro42-v1",
        "order_method": "ascending sha256('qt4-micro42-20260713:' + question_id)",
        "expected_question_count": 42,
        "expected_dispatched_count": 42,
        "expected_negative_control_count": 0,
        "result_status": "exploratory_test_set_result",
    },
    "valid374": {
        "spec_kind": "qt4_holdout_question_spec",
        "spec_version": "qt4-valid374-v1",
        "question_spec_sha256": (
            "eadea93c7e0bb7f2cfaed411dbe58f74999c16cdf3f10747227b16f286897e44"
        ),
        "input_sha256": (
            "22e914e410ab2cc8eb0c1df2bf2286f42a88e86683117263d7cc0f17a7b402b6"
        ),
        "order_method": (
            "ascending sha256('qt4-valid374-20260713:' + question_id), "
            "then question_id"
        ),
        "expected_question_count": 374,
        "expected_dispatched_count": 44,
        "expected_negative_control_count": 330,
        "result_status": "confirmatory_holdout_result",
    },
}
REQUIRED_SNAPSHOTS = {
    "spec",
    "gate_report",
    "input",
    "schema",
    "harness",
    "runner",
    "run_lock",
    "gate_code",
    "packet_a6a",
    "packet_qt4v",
    "packet_qt4t",
}


@dataclass(frozen=True)
class ArmArtifacts:
    name: str
    packet_path: Path
    run_dir: Path


@dataclass(frozen=True)
class AcceptedCompletion:
    arm: str
    question_id: str
    question_dir: Path
    answer_path: Path
    event_log_path: Path
    prompt_path: Path
    receipt_path: Path


@dataclass
class ValidatedRun:
    experiment_profile: str
    question_ids: list[str]
    strata: dict[str, list[str]]
    gold: dict[str, dict[str, str]]
    packet_records: dict[str, dict[str, dict[str, Any]]]
    controller: dict[str, Any]
    controller_sha256: str
    accepted: dict[str, list[AcceptedCompletion]]
    failed_attempts: dict[str, list[dict[str, Any]]]
    completion_summary: dict[str, Any]
    input_hashes: dict[str, Any]


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def registered_experiment_profile(
    controller_manifest: Path,
) -> tuple[str, dict[str, Any]]:
    """Return the exact registered profile bound to a v3 controller."""
    controller = _read_json(controller_manifest)
    if (
        not isinstance(controller, dict)
        or controller.get("kind") != CONTROLLER_KIND
        or controller.get("schema_version") != CONTROLLER_SCHEMA_VERSION
        or controller.get("transport_protocol") != REGISTERED_TRANSPORT_PROTOCOL
    ):
        raise ValueError("controller manifest is not the sealed QT-4 v3 transport")
    profile = controller.get("experiment_profile")
    config = EXPERIMENT_PROFILES.get(str(profile))
    if config is None:
        raise ValueError("controller manifest has no registered QT-4 experiment profile")
    return str(profile), config


def registered_question_count(controller_manifest: Path) -> int:
    """Return the exact schedule size bound to a registered v3 controller."""
    _profile, config = registered_experiment_profile(controller_manifest)
    return int(config["expected_question_count"])


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_json(path: Path, value: object) -> None:
    _write_text_atomic(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _implementation_hashes() -> dict[str, str]:
    modules = {
        "qt4_analysis": Path(__file__),
        "codex_harness": Path(codex_harness.__file__),
        "deterministic_grader": Path(grade_a6a_confirmatory.__file__),
        "paired_stats": Path(paired_stats.__file__),
        "panel_grade": Path(panel_grade.__file__),
    }
    return {name: sha256_file(path.resolve()) for name, path in modules.items()}


def load_question_spec(path: Path, *, expected_count: int) -> list[str]:
    value = _read_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("question_ids"), list):
        raise ValueError("QT-4 question spec must be an object with question_ids")
    question_ids = [str(item) for item in value["question_ids"]]
    if (
        len(question_ids) != expected_count
        or len(set(question_ids)) != expected_count
        or any(not item for item in question_ids)
    ):
        raise ValueError(
            f"QT-4 question spec must contain exactly {expected_count} unique IDs"
        )
    recorded_count = value.get("expected_question_count")
    if recorded_count is None or int(recorded_count) != expected_count:
        raise ValueError("question spec expected_question_count is not exact")
    return question_ids


def load_question_strata(
    path: Path,
    *,
    profile: str,
    question_ids: list[str],
) -> dict[str, list[str]]:
    """Validate the frozen profile metadata and return strata in schedule order."""
    config = EXPERIMENT_PROFILES[profile]
    value = _read_json(path)
    expected_metadata = {
        "kind": config["spec_kind"],
        "version": config["spec_version"],
        "order_method": config["order_method"],
        "expected_question_count": config["expected_question_count"],
    }
    if any(value.get(key) != expected for key, expected in expected_metadata.items()):
        raise ValueError("question spec metadata does not match the controller profile")

    expected_dispatched = int(config["expected_dispatched_count"])
    expected_negative = int(config["expected_negative_control_count"])
    if profile == "valid374":
        dispatcher = value.get("micro_dispatcher")
        raw_dispatched = value.get("microbiology_question_ids")
        if (
            value.get("expected_microbiology_question_count") != expected_dispatched
            or value.get("expected_non_microbiology_question_count") != expected_negative
            or not isinstance(dispatcher, dict)
            or dispatcher.get("version") != "micro-dispatch-v1"
            or not isinstance(raw_dispatched, list)
        ):
            raise ValueError("valid374 question spec has invalid stratum metadata")
        dispatched_set = {str(item) for item in raw_dispatched}
        if (
            len(raw_dispatched) != expected_dispatched
            or len(dispatched_set) != expected_dispatched
            or not dispatched_set.issubset(question_ids)
        ):
            raise ValueError("valid374 dispatched question inventory is not exact")
        dispatched = [qid for qid in question_ids if qid in dispatched_set]
    else:
        dispatched = list(question_ids)
    dispatched_set = set(dispatched)
    negative_control = [qid for qid in question_ids if qid not in dispatched_set]
    if (
        len(dispatched) != expected_dispatched
        or len(negative_control) != expected_negative
    ):
        raise ValueError("question strata do not match the registered profile counts")
    return {
        "dispatched": dispatched,
        "negative_control": negative_control,
        "pooled": list(question_ids),
    }


def load_gold_rows(input_path: Path, question_ids: list[str]) -> dict[str, dict[str, str]]:
    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    keyed: dict[str, dict[str, str]] = {}
    duplicates: list[str] = []
    for row in rows:
        question_id = str(row.get("question_id") or "")
        if question_id in keyed:
            duplicates.append(question_id)
        keyed[question_id] = row
    if duplicates:
        raise ValueError(f"input contains duplicate question IDs: {sorted(set(duplicates))}")
    missing = [question_id for question_id in question_ids if question_id not in keyed]
    if missing:
        raise ValueError(f"input is missing scheduled question IDs: {missing}")
    selected = {question_id: keyed[question_id] for question_id in question_ids}
    if any(not str(row.get("patient_fhir_id") or "") for row in selected.values()):
        raise ValueError("every scheduled question requires a patient_fhir_id cluster")
    return selected


def load_packet_records(
    packet_path: Path, question_ids: list[str]
) -> dict[str, dict[str, Any]]:
    requested = set(question_ids)
    seen: set[str] = set()
    selected: dict[str, dict[str, Any]] = {}
    with packet_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or "question_id" not in value:
                raise ValueError(f"{packet_path}:{line_number} has no question_id")
            question_id = str(value["question_id"])
            if question_id in seen:
                raise ValueError(f"duplicate packet question_id {question_id}: {packet_path}")
            seen.add(question_id)
            if question_id in requested:
                if not isinstance(value.get("packet"), dict):
                    raise ValueError(
                        f"scheduled packet {question_id} has no packet object: {packet_path}"
                    )
                selected[question_id] = value
    missing = [question_id for question_id in question_ids if question_id not in selected]
    if missing:
        raise ValueError(f"packet file is missing scheduled question IDs: {missing}")
    return {question_id: selected[question_id] for question_id in question_ids}


def canonical_model_packet_bytes(packet: dict[str, Any]) -> int:
    """UTF-8 bytes of the exact pretty-JSON packet embedded in the prompt."""
    rendered = codex_harness.render_model_visible_packet(packet)
    return len(rendered.encode("utf-8"))


def _validate_arms(arms: Mapping[str, ArmArtifacts]) -> None:
    if set(arms) != set(ARM_NAMES):
        raise ValueError(f"QT-4 analysis requires exactly these arms: {ARM_NAMES}")
    for name in ARM_NAMES:
        if arms[name].name != name:
            raise ValueError(f"arm key/name mismatch for {name}")


def _validate_controller(
    *,
    controller_path: Path,
    question_spec: Path,
    input_path: Path,
    question_ids: list[str],
    arms: Mapping[str, ArmArtifacts],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    controller = _read_json(controller_path)
    if not isinstance(controller, dict):
        raise ValueError("controller manifest must be a JSON object")
    if (
        controller.get("kind") != CONTROLLER_KIND
        or controller.get("schema_version") != CONTROLLER_SCHEMA_VERSION
        or controller.get("transport_protocol") != REGISTERED_TRANSPORT_PROTOCOL
    ):
        raise ValueError("controller manifest is not the sealed QT-4 v3 transport")
    profile = str(controller.get("experiment_profile") or "")
    config = EXPERIMENT_PROFILES.get(profile)
    if config is None:
        raise ValueError("controller manifest has no registered QT-4 experiment profile")
    if len(question_ids) != int(config["expected_question_count"]):
        raise ValueError("controller profile does not match the frozen question count")
    if controller.get("question_ids") != question_ids:
        raise ValueError("controller question IDs/order do not match the frozen spec")

    schedule = controller.get("schedule")
    if not isinstance(schedule, list) or len(schedule) != len(question_ids) * len(
        ARM_NAMES
    ):
        raise ValueError("controller schedule is not the exact three-arm queue")
    for index, question_id in enumerate(question_ids):
        group = schedule[index * len(ARM_NAMES) : (index + 1) * len(ARM_NAMES)]
        expected_order = [
            ARM_NAMES[(index + offset) % len(ARM_NAMES)]
            for offset in range(len(ARM_NAMES))
        ]
        if [
            (item.get("question_id"), item.get("arm")) for item in group
        ] != [(question_id, arm) for arm in expected_order]:
            raise ValueError("controller schedule is not the registered rotating arm order")

    outputs = controller.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(ARM_NAMES):
        raise ValueError("controller outputs are not the exact three-arm set")
    for arm in ARM_NAMES:
        if Path(str(outputs[arm])).resolve() != arms[arm].run_dir.resolve():
            raise ValueError(f"{arm} run directory does not match the sealed controller")

    execution = controller.get("execution")
    if (
        not isinstance(execution, dict)
        or execution.get("model") != REGISTERED_PANEL_MODEL
        or execution.get("reasoning_effort") != REGISTERED_PANEL_EFFORT
    ):
        raise ValueError("controller execution is not pinned to gpt-5.6-sol/high")

    snapshots = controller.get("snapshots")
    if not isinstance(snapshots, dict) or set(snapshots) != REQUIRED_SNAPSHOTS:
        missing = sorted(REQUIRED_SNAPSHOTS - set(snapshots or {}))
        extra = sorted(set(snapshots or {}) - REQUIRED_SNAPSHOTS)
        raise ValueError(
            "controller immutable snapshot inventory is not exact; "
            f"missing={missing} extra={extra}"
        )
    for name, entry in snapshots.items():
        if not isinstance(entry, dict):
            raise ValueError(f"controller snapshot is malformed: {name}")
        snapshot_path = Path(str(entry.get("snapshot_path") or ""))
        expected_sha = str(entry.get("sha256") or "")
        if not snapshot_path.exists() or not expected_sha:
            raise ValueError(f"controller snapshot is missing: {name}")
        if sha256_file(snapshot_path) != expected_sha:
            raise ValueError(f"controller snapshot hash changed: {name}")

    bound_files = {
        "spec": question_spec,
        "input": input_path,
        **{f"packet_{arm}": arms[arm].packet_path for arm in ARM_NAMES},
    }
    for name, path in bound_files.items():
        if snapshots[name]["sha256"] != sha256_file(path):
            raise ValueError(f"analysis input no longer matches controller snapshot: {name}")

    # Packet visibility is measured using the exact renderer implementation
    # snapshotted for the answer run, not whichever helper happens to be newer.
    current_harness_sha = sha256_file(Path(codex_harness.__file__).resolve())
    if snapshots["harness"]["sha256"] != current_harness_sha:
        raise ValueError(
            "current codex_harness does not match the controller snapshot; "
            "cannot claim model-visible packet byte counts"
        )

    controller_sha = sha256_file(controller_path)
    hashes = {
        "controller_manifest": controller_sha,
        "question_spec": sha256_file(question_spec),
        "input": sha256_file(input_path),
        "packets": {
            arm: sha256_file(arms[arm].packet_path) for arm in ARM_NAMES
        },
        "controller_snapshots": {
            name: str(entry["sha256"]) for name, entry in sorted(snapshots.items())
        },
    }
    return controller, controller_sha, hashes


def _answer_shape_is_valid(path: Path) -> bool:
    try:
        answer = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(answer, dict)
        and isinstance(answer.get("answer"), str)
        and isinstance(answer.get("source_resource_ids"), list)
        and all(isinstance(item, str) for item in answer["source_resource_ids"])
        and isinstance(answer.get("evidence_summary"), str)
        and (
            answer.get("insufficiency_reason") is None
            or isinstance(answer.get("insufficiency_reason"), str)
        )
    )


def _load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed accepted event log {path}:{line_number}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"non-object accepted event {path}:{line_number}")
        events.append(event)
    if not events or not any(event.get("type") == "turn.completed" for event in events):
        raise ValueError(f"accepted event log has no turn.completed event: {path}")
    return events


def _archived_event_usage(path: Path) -> dict[str, int | float]:
    """Recompute the numeric usage runner v2 records for one failed attempt."""
    completed: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed archived event log {path}:{line_number}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"non-object archived event {path}:{line_number}")
        if event.get("type") == "turn.completed":
            completed.append(event)
    if len(completed) > 1:
        raise ValueError(f"archived event log has multiple completed turns: {path}")
    if not completed or not isinstance(completed[0].get("usage"), dict):
        return {}
    return {
        str(key): value
        for key, value in completed[0]["usage"].items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _validate_failed_attempt_ledgers(
    *,
    question_ids: list[str],
    controller_sha: str,
    controller: Mapping[str, Any],
    arms: Mapping[str, ArmArtifacts],
) -> dict[str, list[dict[str, Any]]]:
    execution = controller["execution"]
    schema_sha = controller["snapshots"]["schema"]["sha256"]
    packet_hashes = {arm: sha256_file(arms[arm].packet_path) for arm in ARM_NAMES}
    result: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARM_NAMES}
    for arm in ARM_NAMES:
        for question_id in question_ids:
            question_dir = arms[arm].run_dir / "questions" / question_id
            attempts_dir = question_dir / "attempts"
            receipt_paths = sorted(attempts_dir.glob("attempt-*/attempt.json"))
            ledger_path = question_dir / "attempts.jsonl"
            if not receipt_paths:
                if ledger_path.exists():
                    raise ValueError(f"{arm}/{question_id} has an orphan attempt ledger")
                continue
            if not ledger_path.exists():
                raise ValueError(f"{arm}/{question_id} has no mirrored attempt ledger")
            ledger: list[dict[str, Any]] = []
            for line_number, line in enumerate(
                ledger_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{arm}/{question_id} malformed attempt ledger line {line_number}"
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(f"{arm}/{question_id} attempt ledger is not object JSONL")
                ledger.append(value)
            receipts = [_read_json(path) for path in receipt_paths]
            if receipts != ledger:
                raise ValueError(f"{arm}/{question_id} attempt ledger/archive mismatch")
            if len(receipts) >= MAX_ATTEMPTS_PER_ITEM:
                raise ValueError(f"{arm}/{question_id} exhausted attempts before acceptance")
            for index, (path, receipt) in enumerate(
                zip(receipt_paths, receipts), start=1
            ):
                expected_identity = {
                    "kind": COMPLETION_KIND,
                    "schema_version": COMPLETION_SCHEMA_VERSION,
                    "controller_manifest_sha256": controller_sha,
                    "arm": arm,
                    "question_id": question_id,
                    "packet_sha256": packet_hashes[arm],
                    "schema_sha256": schema_sha,
                    "model": execution["model"],
                    "reasoning_effort": execution["reasoning_effort"],
                    "attempt_number": index,
                    "status": "transient_failure",
                }
                if not isinstance(receipt, dict) or any(
                    receipt.get(key) != value
                    for key, value in expected_identity.items()
                ):
                    raise ValueError(f"{arm}/{question_id} failed attempt is misbound")
                if Path(str(receipt.get("attempt_receipt_path") or "")).resolve() != path.resolve():
                    raise ValueError(f"{arm}/{question_id} attempt receipt path is stale")
                archived_files = receipt.get("archived_files")
                if not isinstance(archived_files, dict):
                    raise ValueError(f"{arm}/{question_id} attempt file inventory missing")
                for metadata in archived_files.values():
                    if not isinstance(metadata, dict):
                        raise ValueError(f"{arm}/{question_id} attempt file metadata malformed")
                    archived_path = Path(str(metadata.get("path") or ""))
                    if (
                        archived_path.resolve().parent != path.parent.resolve()
                        or not archived_path.is_file()
                        or metadata.get("sha256") != sha256_file(archived_path)
                    ):
                        raise ValueError(f"{arm}/{question_id} archived attempt file changed")
                events_metadata = archived_files.get("events.jsonl")
                if not isinstance(events_metadata, dict):
                    raise ValueError(
                        f"{arm}/{question_id} attempt event log is missing"
                    )
                archived_event_path = Path(str(events_metadata["path"]))
                recomputed_audit = codex_harness.audit_event_log(
                    archived_event_path
                )
                recorded_audit = receipt.get("event_integrity")
                if recorded_audit != recomputed_audit:
                    raise ValueError(
                        f"{arm}/{question_id} archived attempt audit changed"
                    )
                if recomputed_audit.get("contaminated") and not (
                    codex_harness.is_retryable_incomplete_packet_audit(
                        recomputed_audit
                    )
                    and receipt.get("harness_exit_code") not in (None, 0)
                    and receipt.get("answer_sha256") is None
                ):
                    raise ValueError(
                        f"{arm}/{question_id} transient attempt was contaminated"
                    )
                if codex_harness.is_retryable_incomplete_packet_audit(
                    recomputed_audit
                ):
                    marker_metadata = archived_files.get("contamination.json")
                    if not isinstance(marker_metadata, dict):
                        raise ValueError(
                            f"{arm}/{question_id} retryable marker is missing"
                        )
                    try:
                        marker = _read_json(Path(str(marker_metadata["path"])))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"{arm}/{question_id} retryable marker is malformed"
                        ) from exc
                    if not codex_harness.retryable_incomplete_packet_marker_matches(
                        marker,
                        recomputed_audit,
                    ):
                        raise ValueError(
                            f"{arm}/{question_id} retryable marker changed"
                        )
                recomputed_usage = (
                    _archived_event_usage(archived_event_path)
                )
                if receipt.get("usage") != recomputed_usage:
                    raise ValueError(
                        f"{arm}/{question_id} archived attempt usage changed"
                    )
                stderr_metadata = archived_files.get("stderr.log")
                if not isinstance(stderr_metadata, dict):
                    raise ValueError(
                        f"{arm}/{question_id} attempt stderr log is missing"
                    )
                archived_stderr_path = Path(str(stderr_metadata["path"]))
                recomputed_stderr = codex_harness.audit_stderr(
                    archived_stderr_path
                )
                if receipt.get("stderr_integrity") != recomputed_stderr:
                    raise ValueError(
                        f"{arm}/{question_id} archived attempt stderr audit changed"
                    )
                if receipt.get("stderr_log_sha256") != sha256_file(
                    archived_stderr_path
                ):
                    raise ValueError(
                        f"{arm}/{question_id} archived attempt stderr hash changed"
                    )
                result[arm].append(receipt)
    return result


def _validate_completions(
    *,
    question_ids: list[str],
    controller_sha: str,
    arms: Mapping[str, ArmArtifacts],
    input_rows: Mapping[str, dict[str, str]],
    packet_records: Mapping[str, Mapping[str, dict[str, Any]]],
    controller: Mapping[str, Any],
    failed_attempts: Mapping[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[AcceptedCompletion]], dict[str, Any]]:
    expected_set = set(question_ids)
    accepted: dict[str, list[AcceptedCompletion]] = {}
    receipt_bindings: list[dict[str, str]] = []
    for arm in ARM_NAMES:
        questions_dir = arms[arm].run_dir / "questions"
        actual_dirs = (
            {path.name for path in questions_dir.iterdir() if path.is_dir()}
            if questions_dir.exists()
            else set()
        )
        extra = sorted(actual_dirs - expected_set)
        if extra:
            raise ValueError(f"{arm} has unexpected question directories: {extra}")
        if actual_dirs != expected_set:
            missing = sorted(expected_set - actual_dirs)
            raise ValueError(
                f"{arm} does not have the exact sealed completion set; missing={missing}"
            )

        arm_accepted: list[AcceptedCompletion] = []
        for question_id in question_ids:
            question_dir = questions_dir / question_id
            paths = {
                "answer_sha256": question_dir / "answer.json",
                "event_log_sha256": question_dir / "events.jsonl",
                "prompt_sha256": question_dir / "prompt.txt",
                "stderr_log_sha256": question_dir / "stderr.log",
            }
            receipt_path = question_dir / "completion.json"
            if not receipt_path.exists() or any(not path.exists() for path in paths.values()):
                raise ValueError(
                    f"{arm}/{question_id} has no exact sealed completion receipt/files"
                )
            receipt = _read_json(receipt_path)
            expected_identity = {
                "kind": COMPLETION_KIND,
                "schema_version": COMPLETION_SCHEMA_VERSION,
                "controller_manifest_sha256": controller_sha,
                "arm": arm,
                "question_id": question_id,
                "packet_sha256": sha256_file(arms[arm].packet_path),
                "schema_sha256": controller["snapshots"]["schema"]["sha256"],
                "model": controller["execution"]["model"],
                "reasoning_effort": controller["execution"]["reasoning_effort"],
                "attempt_number": 1
                + sum(
                    item.get("question_id") == question_id
                    for item in failed_attempts[arm]
                ),
                "harness_exit_code": 0,
                "returncode": 0,
                "status": "answered",
            }
            if not isinstance(receipt, dict) or any(
                receipt.get(key) != value for key, value in expected_identity.items()
            ):
                raise ValueError(f"{arm}/{question_id} completion receipt is not accepted")
            if any(receipt.get(key) != sha256_file(path) for key, path in paths.items()):
                raise ValueError(f"{arm}/{question_id} sealed artifact hash changed")
            stderr_audit = codex_harness.audit_stderr(paths["stderr_log_sha256"])
            if (
                stderr_audit.get("empty") is not True
                or receipt.get("stderr_integrity") != stderr_audit
            ):
                raise ValueError(
                    f"{arm}/{question_id} accepted stderr integrity is not empty"
                )
            expected_prompt = codex_harness.build_prompt(
                {**input_rows[question_id], **packet_records[arm][question_id]},
                mode="packet",
            )
            if paths["prompt_sha256"].read_text(encoding="utf-8") != expected_prompt:
                raise ValueError(
                    f"{arm}/{question_id} prompt does not match sealed input and packet"
                )
            if (
                (question_dir / "contamination.json").exists()
                or (question_dir / "answer.contaminated.json").exists()
                or not _answer_shape_is_valid(paths["answer_sha256"])
            ):
                raise ValueError(f"{arm}/{question_id} answer is invalid or contaminated")
            events = _load_events(paths["event_log_sha256"])
            if sum(event.get("type") == "turn.completed" for event in events) != 1:
                raise ValueError(
                    f"{arm}/{question_id} must have exactly one completed answer turn"
                )
            audit = codex_harness.audit_event_log(paths["event_log_sha256"])
            if audit.get("contaminated") or receipt.get("event_integrity") != audit:
                raise ValueError(f"{arm}/{question_id} event integrity is not accepted")
            raw_completed_usage = next(
                (
                    event.get("usage")
                    for event in events
                    if event.get("type") == "turn.completed"
                ),
                None,
            )
            completed_usage = (
                {
                    str(key): value
                    for key, value in raw_completed_usage.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                }
                if isinstance(raw_completed_usage, dict)
                else None
            )
            if receipt.get("usage") != completed_usage:
                raise ValueError(f"{arm}/{question_id} receipt usage changed")

            arm_accepted.append(
                AcceptedCompletion(
                    arm=arm,
                    question_id=question_id,
                    question_dir=question_dir,
                    answer_path=paths["answer_sha256"],
                    event_log_path=paths["event_log_sha256"],
                    prompt_path=paths["prompt_sha256"],
                    receipt_path=receipt_path,
                )
            )
            receipt_bindings.append(
                {
                    "arm": arm,
                    "question_id": question_id,
                    "completion_sha256": sha256_file(receipt_path),
                }
            )
        accepted[arm] = arm_accepted

    expected_answers = len(question_ids) * len(ARM_NAMES)
    accepted_answers = sum(len(items) for items in accepted.values())
    if accepted_answers != expected_answers:
        raise ValueError("analysis requires the exact sealed completion/answer count")
    summary = {
        "expected_questions_per_arm": len(question_ids),
        "expected_answers": expected_answers,
        "accepted_answers": accepted_answers,
        "accepted_by_arm": {arm: len(accepted[arm]) for arm in ARM_NAMES},
        "completion_receipt_set_sha256": sha256_json(receipt_bindings),
        "archived_failed_attempts": sum(
            len(items) for items in failed_attempts.values()
        ),
        "archived_failed_attempt_receipts_sha256": sha256_json(failed_attempts),
        "invalid_current_receipts": 0,
    }
    return accepted, summary


def validate_sealed_run(
    *,
    controller_manifest: Path,
    question_spec: Path,
    input_path: Path,
    arms: Mapping[str, ArmArtifacts],
    expected_question_count: int | None = None,
) -> ValidatedRun:
    _validate_arms(arms)
    experiment_profile, profile_config = registered_experiment_profile(
        controller_manifest
    )
    registered_count = int(profile_config["expected_question_count"])
    if (
        expected_question_count is not None
        and expected_question_count != registered_count
    ):
        raise ValueError(
            "explicit question count conflicts with the registered controller profile"
        )
    registered_spec_sha = profile_config.get("question_spec_sha256")
    registered_input_sha = profile_config.get("input_sha256")
    if registered_spec_sha and sha256_file(question_spec) != registered_spec_sha:
        raise ValueError("question spec does not match the preregistered frozen bytes")
    if registered_input_sha and sha256_file(input_path) != registered_input_sha:
        raise ValueError("input does not match the preregistered frozen holdout bytes")
    question_ids = load_question_spec(
        question_spec, expected_count=registered_count
    )
    strata = load_question_strata(
        question_spec,
        profile=experiment_profile,
        question_ids=question_ids,
    )
    gold = load_gold_rows(input_path, question_ids)
    if experiment_profile == "valid374":
        labeled_dispatched = {
            question_id
            for question_id, row in gold.items()
            if str(row.get("main_table_name") or "").strip().lower()
            == "microbiologyevents"
        }
        if labeled_dispatched != set(strata["dispatched"]):
            raise ValueError(
                "valid374 frozen dispatched IDs do not match the source stratum"
            )
    controller, controller_sha, input_hashes = _validate_controller(
        controller_path=controller_manifest,
        question_spec=question_spec,
        input_path=input_path,
        question_ids=question_ids,
        arms=arms,
    )
    packet_records = {
        arm: load_packet_records(arms[arm].packet_path, question_ids)
        for arm in ARM_NAMES
    }
    failed_attempts = _validate_failed_attempt_ledgers(
        question_ids=question_ids,
        controller_sha=controller_sha,
        controller=controller,
        arms=arms,
    )
    accepted, completion_summary = _validate_completions(
        question_ids=question_ids,
        controller_sha=controller_sha,
        arms=arms,
        input_rows=gold,
        packet_records=packet_records,
        controller=controller,
        failed_attempts=failed_attempts,
    )
    input_hashes["implementations"] = _implementation_hashes()
    return ValidatedRun(
        experiment_profile=experiment_profile,
        question_ids=question_ids,
        strata=strata,
        gold=gold,
        packet_records=packet_records,
        controller=controller,
        controller_sha256=controller_sha,
        accepted=accepted,
        failed_attempts=failed_attempts,
        completion_summary=completion_summary,
        input_hashes=input_hashes,
    )


def prepare_grading(
    *,
    controller_manifest: Path,
    question_spec: Path,
    input_path: Path,
    arms: Mapping[str, ArmArtifacts],
    out_dir: Path,
    expected_question_count: int | None = None,
) -> dict[str, Any]:
    """Apply deterministic rules once per arm and write one panel queue."""
    validated = validate_sealed_run(
        controller_manifest=controller_manifest,
        question_spec=question_spec,
        input_path=input_path,
        arms=arms,
        expected_question_count=expected_question_count,
    )
    mechanism_outcomes = _sealed_mechanism_outcomes(validated)
    deterministic: dict[str, dict[str, int]] = {}
    panel_items: list[dict[str, Any]] = []
    partition: dict[str, dict[str, int]] = {}
    invocations: dict[str, int] = {}
    for arm in ARM_NAMES:
        verdicts, queued = grade_a6a_confirmatory.grade_arm(
            arms[arm].run_dir,
            validated.gold,
            answered_only=False,
        )
        invocations[arm] = 1
        if set(verdicts) & {str(item["question_id"]) for item in queued}:
            raise ValueError(f"{arm} grader produced overlapping deterministic/panel labels")
        if set(verdicts) | {str(item["question_id"]) for item in queued} != set(
            validated.question_ids
        ):
            raise ValueError(f"{arm} grader did not partition the exact frozen schedule")
        deterministic[arm] = {
            question_id: int(verdicts[question_id])
            for question_id in validated.question_ids
            if question_id in verdicts
        }
        queued_by_id = {str(item["question_id"]): item for item in queued}
        panel_items.extend(
            {"arm": arm, **queued_by_id[question_id]}
            for question_id in validated.question_ids
            if question_id in queued_by_id
        )
        partition[arm] = {
            "scheduled": len(validated.question_ids),
            "deterministic": len(verdicts),
            "panel": len(queued),
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    det_path = out_dir / "det_verdicts.json"
    queue_path = out_dir / "panel_queue.jsonl"
    _write_json(det_path, deterministic)
    queue_text = "".join(canonical_json(item) + "\n" for item in panel_items)
    _write_text_atomic(queue_path, queue_text)
    manifest = {
        "analysis_version": ANALYSIS_VERSION,
        "arms": list(ARM_NAMES),
        "question_ids": validated.question_ids,
        "input_hashes": validated.input_hashes,
        "sealed_completion": validated.completion_summary,
        "mechanism_gate_report_sha256": mechanism_outcomes[
            "gate_report_sha256"
        ],
        "deterministic_grader_version": grade_a6a_confirmatory.GRADER_VERSION,
        "deterministic_grader_invocations": invocations,
        "partition": partition,
        "panel_protocol": {
            "script": "panel_grade.py",
            "opaque_id_version": panel_grade.OPAQUE_ID_VERSION,
            "judge_protocol_version": panel_grade.JUDGE_PROTOCOL_VERSION,
            "single_arm_blind_queue": True,
        },
        "outputs": {
            "det_verdicts_sha256": sha256_file(det_path),
            "panel_queue_sha256": sha256_file(queue_path),
            "panel_queue_count": len(panel_items),
        },
    }
    _write_json(out_dir / "grading_manifest.json", manifest)
    return manifest


def _verify_grading_artifacts(
    validated: ValidatedRun, grading_dir: Path
) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]], dict[str, Any]]:
    det_path = grading_dir / "det_verdicts.json"
    queue_path = grading_dir / "panel_queue.jsonl"
    manifest_path = grading_dir / "grading_manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("analysis_version") != ANALYSIS_VERSION:
        raise ValueError("grading manifest version mismatch")
    exact_fields = {
        "arms": list(ARM_NAMES),
        "question_ids": validated.question_ids,
        "input_hashes": validated.input_hashes,
        "sealed_completion": validated.completion_summary,
        "mechanism_gate_report_sha256": _sealed_mechanism_outcomes(validated)[
            "gate_report_sha256"
        ],
        "deterministic_grader_version": grade_a6a_confirmatory.GRADER_VERSION,
        "deterministic_grader_invocations": {arm: 1 for arm in ARM_NAMES},
    }
    if any(manifest.get(key) != value for key, value in exact_fields.items()):
        raise ValueError("grading manifest no longer matches sealed inputs")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or outputs != {
        "det_verdicts_sha256": sha256_file(det_path),
        "panel_queue_sha256": sha256_file(queue_path),
        "panel_queue_count": len(panel_grade.load_queue(queue_path)),
    }:
        raise ValueError("grading outputs do not match their manifest")
    protocol = manifest.get("panel_protocol")
    if not isinstance(protocol, dict) or protocol != {
        "script": "panel_grade.py",
        "opaque_id_version": panel_grade.OPAQUE_ID_VERSION,
        "judge_protocol_version": panel_grade.JUDGE_PROTOCOL_VERSION,
        "single_arm_blind_queue": True,
    }:
        raise ValueError("panel protocol changed after deterministic grading")

    deterministic = _read_json(det_path)
    if not isinstance(deterministic, dict) or set(deterministic) != set(ARM_NAMES):
        raise ValueError("deterministic verdict arms are not exact")
    queue = panel_grade.load_queue(queue_path)
    queue_hosts: list[tuple[str, str]] = []
    for item in queue:
        arm = item.get("arm")
        question_id = item.get("question_id")
        if arm not in ARM_NAMES or question_id not in validated.question_ids:
            raise ValueError("panel queue contains an unexpected host identity")
        queue_hosts.append((str(arm), str(question_id)))
    if len(queue_hosts) != len(set(queue_hosts)):
        raise ValueError("panel queue contains duplicate arm/question items")

    partition = manifest.get("partition")
    if not isinstance(partition, dict) or set(partition) != set(ARM_NAMES):
        raise ValueError("grading partition is malformed")
    host_set = set(queue_hosts)
    for arm in ARM_NAMES:
        arm_det = deterministic[arm]
        if not isinstance(arm_det, dict) or any(
            question_id not in validated.question_ids
            or type(verdict) is not int
            or verdict not in (0, 1)
            for question_id, verdict in arm_det.items()
        ):
            raise ValueError(f"{arm} deterministic verdicts are malformed")
        panel_qids = {
            question_id for queue_arm, question_id in host_set if queue_arm == arm
        }
        if set(arm_det) & panel_qids or set(arm_det) | panel_qids != set(
            validated.question_ids
        ):
            raise ValueError(f"{arm} deterministic/panel partition is incomplete")
        expected_partition = {
            "scheduled": len(validated.question_ids),
            "deterministic": len(arm_det),
            "panel": len(panel_qids),
        }
        if partition[arm] != expected_partition:
            raise ValueError(f"{arm} grading partition count changed")
    return deterministic, queue, manifest


def _verify_panel(
    *,
    grading_dir: Path,
    queue: list[dict[str, Any]],
    controller: Mapping[str, Any],
) -> tuple[dict[str, int], dict[str, Any], dict[str, Any]]:
    cache_path = grading_dir / "panel_votes.json"
    verdict_path = grading_dir / "panel_verdicts.json"
    verdict_manifest_path = grading_dir / "panel_verdicts.manifest.json"
    cache = _read_json(cache_path)
    if not isinstance(cache, dict):
        raise ValueError("panel cache is malformed")
    cache_manifest = cache.get("manifest")
    judge_config = (
        cache_manifest.get("judge_config")
        if isinstance(cache_manifest, dict)
        else None
    )
    if not isinstance(judge_config, dict):
        raise ValueError("panel cache has no bound judge configuration")
    execution = controller["execution"]
    registered_config = panel_grade.build_judge_config(
        model=REGISTERED_PANEL_MODEL,
        effort=REGISTERED_PANEL_EFFORT,
        batch_size=REGISTERED_PANEL_BATCH_SIZE,
        votes=REGISTERED_PANEL_VOTES,
        timeout=REGISTERED_PANEL_TIMEOUT,
        codex_bin=str(execution["codex_bin"]),
        codex_version=str(execution["codex_version"]),
    )
    if judge_config != registered_config:
        raise ValueError("panel judge configuration is not the registered QT-4 config")
    blinded = panel_grade.prepare_blinded_items(queue, judge_config)
    expected_manifest = panel_grade.build_cache_manifest(blinded, judge_config)
    validated_cache = panel_grade.load_or_initialize_cache(
        cache_path, expected_manifest, blinded
    )
    required_votes = int(judge_config["requested_votes"])
    if any(
        len(item["votes"]) != required_votes
        for item in validated_cache["items"].values()
    ):
        raise ValueError("every panel item must be fully voted before assembly")
    majority = panel_grade.majority_verdicts(
        validated_cache, required_votes=required_votes
    )
    verdicts = _read_json(verdict_path)
    if (
        not isinstance(verdicts, dict)
        or any(type(value) is not int or value not in (0, 1) for value in verdicts.values())
        or verdicts != majority
    ):
        raise ValueError("panel verdicts do not equal the bound cache majority")
    expected_verdict_manifest = {
        "cache_manifest": expected_manifest,
        "cache_sha256": sha256_json(validated_cache),
        "verdicts_sha256": sha256_json(majority),
        "verdict_count": len(majority),
        "panel_token_usage": panel_grade.panel_token_summary(validated_cache),
    }
    if _read_json(verdict_manifest_path) != expected_verdict_manifest:
        raise ValueError("panel verdict manifest is incomplete or stale")
    return (
        majority,
        expected_verdict_manifest["panel_token_usage"],
        registered_config,
    )


def _labels_from_artifacts(
    *,
    question_ids: list[str],
    deterministic: dict[str, dict[str, int]],
    queue: list[dict[str, Any]],
    panel_verdicts: dict[str, int],
) -> dict[str, dict[str, int]]:
    expected_panel_hosts = {
        f"{item['arm']}|{item['question_id']}" for item in queue
    }
    if set(panel_verdicts) != expected_panel_hosts:
        raise ValueError("panel verdict host coverage is not exact")
    labels: dict[str, dict[str, int]] = {}
    for arm in ARM_NAMES:
        arm_labels: dict[str, int] = {}
        for question_id in question_ids:
            host = f"{arm}|{question_id}"
            has_det = question_id in deterministic[arm]
            has_panel = host in panel_verdicts
            if has_det == has_panel:
                raise ValueError(f"{host} must have exactly one final label source")
            arm_labels[question_id] = (
                deterministic[arm][question_id]
                if has_det
                else int(panel_verdicts[host])
            )
        labels[arm] = arm_labels
    return labels


def _contrast(
    *,
    name: str,
    treatment: str,
    reference: str,
    question_ids: list[str],
    gold: dict[str, dict[str, str]],
    labels: dict[str, dict[str, int]],
    n_boot: int,
) -> dict[str, Any]:
    pairs = [
        (
            str(gold[question_id]["patient_fhir_id"]),
            labels[treatment][question_id],
            labels[reference][question_id],
        )
        for question_id in question_ids
    ]
    paired = paired_stats.paired_summary(pairs, n_boot=n_boot)
    discordant = paired["discordant_a_only"] + paired["discordant_b_only"]
    return {
        "name": name,
        "orientation": "treatment_minus_reference",
        "treatment": treatment,
        "reference": reference,
        "n": paired["n"],
        "treatment_accuracy": paired["acc_a"],
        "reference_accuracy": paired["acc_b"],
        "accuracy_difference": paired["diff"],
        "discordant_treatment_only": paired["discordant_a_only"],
        "discordant_reference_only": paired["discordant_b_only"],
        "mcnemar": {
            "estimable": discordant > 0,
            "discordant_pairs": discordant,
            "exact_two_sided_p": paired["mcnemar_p"] if discordant else None,
        },
        "patient_cluster_bootstrap": paired["cluster_bootstrap"],
    }


def _safety_accuracy(
    *,
    treatment: str,
    reference: str,
    question_ids: list[str],
    labels: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    n = len(question_ids)
    if n < 1:
        raise ValueError("safety accuracy requires at least one question")
    treatment_correct = sum(labels[treatment][qid] for qid in question_ids)
    reference_correct = sum(labels[reference][qid] for qid in question_ids)
    difference = (treatment_correct - reference_correct) / n
    return {
        "n": n,
        "treatment_correct": treatment_correct,
        "reference_correct": reference_correct,
        "treatment_accuracy": treatment_correct / n,
        "reference_accuracy": reference_correct / n,
        "accuracy_difference": difference,
        "maximum_allowed_degradation": 0.01,
        "passed": 100 * (treatment_correct - reference_correct) >= -n,
    }


def _profile_contrasts(
    *,
    profile: str,
    strata: Mapping[str, list[str]],
    gold: dict[str, dict[str, str]],
    labels: dict[str, dict[str, int]],
    n_boot: int,
) -> dict[str, dict[str, Any]]:
    analysis_ids = (
        strata["dispatched"] if profile == "valid374" else strata["pooled"]
    )
    result: dict[str, dict[str, Any]] = {}
    for name, treatment, reference in REGISTERED_CONTRASTS:
        contrast = _contrast(
            name=name,
            treatment=treatment,
            reference=reference,
            question_ids=analysis_ids,
            gold=gold,
            labels=labels,
            n_boot=n_boot,
        )
        contrast["analysis_stratum"] = (
            "dispatched" if profile == "valid374" else "pooled"
        )
        if profile == "valid374":
            contrast["safety"] = {
                "pooled": _safety_accuracy(
                    treatment=treatment,
                    reference=reference,
                    question_ids=strata["pooled"],
                    labels=labels,
                ),
                "negative_control": _safety_accuracy(
                    treatment=treatment,
                    reference=reference,
                    question_ids=strata["negative_control"],
                    labels=labels,
                ),
            }
        result[name] = contrast
    return result


def _coerce_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _first_metric(mapping: Mapping[str, Any], keys: Iterable[str]) -> int | None:
    for key in keys:
        if key in mapping:
            value = _coerce_nonnegative_int(mapping[key])
            if value is not None:
                return value
    return None


def _nested_metric(
    mapping: Mapping[str, Any], containers: Iterable[str], keys: Iterable[str]
) -> int | None:
    for container in containers:
        value = mapping.get(container)
        if isinstance(value, dict):
            metric = _first_metric(value, keys)
            if metric is not None:
                return metric
    return None


def _parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _wall_time_seconds(events: list[dict[str, Any]]) -> float | None:
    completed = [event for event in events if event.get("type") == "turn.completed"]
    for event in completed:
        for key in ("wall_time_seconds", "duration_seconds", "elapsed_seconds"):
            value = event.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                return float(value)
        for key in ("wall_time_ms", "duration_ms", "elapsed_ms"):
            value = event.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                return float(value) / 1000.0
    timestamps = [
        parsed
        for event in events
        for parsed in [_parse_timestamp(event.get("timestamp"))]
        if parsed is not None
    ]
    if len(timestamps) >= 2:
        return (max(timestamps) - min(timestamps)).total_seconds()
    return None


def _usage_metrics(
    *, question_id: str, usage: Mapping[str, Any], wall_time_seconds: float | None
) -> dict[str, Any]:
    input_tokens = _first_metric(usage, ("input_tokens", "prompt_tokens"))
    output_tokens = _first_metric(usage, ("output_tokens", "completion_tokens"))
    cached_tokens = _first_metric(
        usage,
        ("cached_input_tokens", "cache_read_input_tokens", "cached_tokens"),
    )
    if cached_tokens is None:
        cached_tokens = _nested_metric(
            usage,
            ("input_tokens_details", "prompt_tokens_details"),
            ("cached_tokens", "cache_read_tokens"),
        )
    reasoning_tokens = _first_metric(
        usage, ("reasoning_output_tokens", "reasoning_tokens")
    )
    if reasoning_tokens is None:
        reasoning_tokens = _nested_metric(
            usage,
            ("output_tokens_details", "completion_tokens_details"),
            ("reasoning_tokens",),
        )
    total_tokens = _first_metric(usage, ("total_tokens",))
    total_source = "reported" if total_tokens is not None else None
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
        total_source = "derived_input_plus_output"
    return {
        "question_id": question_id,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
        "total_tokens_source": total_source,
        "wall_time_seconds": wall_time_seconds,
    }


def _completion_metrics(completion: AcceptedCompletion) -> dict[str, Any]:
    events = _load_events(completion.event_log_path)
    usage_events = [
        event
        for event in events
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)
    ]
    usage = usage_events[0]["usage"] if usage_events else {}
    return _usage_metrics(
        question_id=completion.question_id,
        usage=usage,
        wall_time_seconds=_wall_time_seconds(events),
    )


def _failed_attempt_metrics(receipt: Mapping[str, Any]) -> dict[str, Any]:
    usage = receipt.get("usage")
    return _usage_metrics(
        question_id=str(receipt.get("question_id") or ""),
        usage=usage if isinstance(usage, dict) else {},
        wall_time_seconds=None,
    )


def _metric_summary(records: list[dict[str, Any]], name: str) -> dict[str, Any]:
    values = [record[name] for record in records if record.get(name) is not None]
    count = len(records)
    reported = len(values)
    return {
        "available": reported > 0 or count == 0,
        "complete": reported == count,
        "reported_completions": reported,
        "missing_completions": count - reported,
        "total": sum(values) if values else (0 if count == 0 else None),
    }


def _combine_metric_summaries(
    accepted: Mapping[str, Any], failed: Mapping[str, Any]
) -> dict[str, Any]:
    complete = accepted.get("complete") is True and failed.get("complete") is True
    accepted_total = accepted.get("total")
    failed_total = failed.get("total")
    total = (
        accepted_total + failed_total
        if complete and accepted_total is not None and failed_total is not None
        else None
    )
    return {
        "available": total is not None,
        "complete": complete,
        "reported_completions": int(accepted.get("reported_completions", 0))
        + int(failed.get("reported_completions", 0)),
        "missing_completions": int(accepted.get("missing_completions", 0))
        + int(failed.get("missing_completions", 0)),
        "total": total,
    }


def _packet_byte_summary(
    records: Mapping[str, dict[str, Any]],
    question_ids: list[str],
    completions: Iterable[AcceptedCompletion],
) -> dict[str, Any]:
    completion_by_id = {item.question_id: item for item in completions}
    if set(completion_by_id) != set(question_ids):
        raise ValueError("packet-byte accounting requires one accepted prompt per question")
    by_question: dict[str, int] = {}
    for question_id in question_ids:
        rendered = codex_harness.render_model_visible_packet(
            records[question_id]["packet"]
        )
        prompt = completion_by_id[question_id].prompt_path.read_text(encoding="utf-8")
        exact_fragment = (
            "Frozen clinical packet:\n"
            + rendered
            + "\n\nUse this packet as read-only evidence."
        )
        if prompt.count(exact_fragment) != 1:
            raise ValueError(
                f"{question_id} accepted prompt does not contain the exact sealed packet"
            )
        by_question[question_id] = len(rendered.encode("utf-8"))
    values = list(by_question.values())
    return {
        "measurement": (
            "exact indent=2 sorted JSON UTF-8 bytes embedded by "
            "codex_harness.render_model_visible_packet and reverified in prompt.txt"
        ),
        "by_question": by_question,
        "total": sum(values),
        "mean": sum(values) / len(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def _arm_economics(validated: ValidatedRun, arm: str) -> dict[str, Any]:
    records = [_completion_metrics(item) for item in validated.accepted[arm]]
    failed_records = [
        _failed_attempt_metrics(item) for item in validated.failed_attempts[arm]
    ]
    token_names = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    tokens = {name: _metric_summary(records, name) for name in token_names}
    failed_tokens = {
        name: _metric_summary(failed_records, name) for name in token_names
    }
    all_attempt_tokens = {
        name: _combine_metric_summaries(tokens[name], failed_tokens[name])
        for name in token_names
    }
    wall_time = _metric_summary(records, "wall_time_seconds")
    unavailable = [name for name, value in tokens.items() if not value["complete"]]
    if not wall_time["complete"]:
        unavailable.append("wall_time_seconds")
    unavailable.extend(
        f"all_attempt_{name}"
        for name, value in all_attempt_tokens.items()
        if not value["complete"]
    )
    unavailable.append("monetary_cost")
    return {
        "accepted_completion_logs": len(records),
        "tokens": tokens,
        "failed_attempt_tokens": failed_tokens,
        "all_attempt_tokens": all_attempt_tokens,
        "wall_time_seconds": wall_time,
        "attempts": {
            "accepted_completion_receipts": len(records),
            "current_invalid_completion_receipts": 0,
            "retry_history": {
                "available": True,
                "count": len(failed_records),
                "receipt_set_sha256": sha256_json(validated.failed_attempts[arm]),
            },
            "historical_invalid_attempts": {
                "available": True,
                "count": len(failed_records),
                "scope": "append-only qt4-attempt-v3 archives",
            },
        },
        "model_visible_packet_bytes": _packet_byte_summary(
            validated.packet_records[arm],
            validated.question_ids,
            validated.accepted[arm],
        ),
        "unavailable_dimensions": sorted(set(unavailable)),
    }


def _difference_metric(
    treatment: Mapping[str, Any], reference: Mapping[str, Any]
) -> dict[str, Any]:
    complete = treatment.get("complete") is True and reference.get("complete") is True
    treatment_total = treatment.get("total")
    reference_total = reference.get("total")
    if not complete or treatment_total is None or reference_total is None:
        return {
            "available": False,
            "treatment_total": treatment_total,
            "reference_total": reference_total,
            "difference": None,
            "relative_change": None,
            "reason": "one or both arms do not report this dimension for every accepted completion",
        }
    difference = treatment_total - reference_total
    relative = difference / reference_total if reference_total else None
    return {
        "available": True,
        "treatment_total": treatment_total,
        "reference_total": reference_total,
        "difference": difference,
        "relative_change": relative,
    }


def _contrast_economics(
    *, name: str, treatment: str, reference: str, arms: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    token_names = arms[treatment]["tokens"].keys()
    token_differences = {
        metric: _difference_metric(
            arms[treatment]["tokens"][metric], arms[reference]["tokens"][metric]
        )
        for metric in token_names
    }
    all_attempt_token_differences = {
        metric: _difference_metric(
            arms[treatment]["all_attempt_tokens"][metric],
            arms[reference]["all_attempt_tokens"][metric],
        )
        for metric in token_names
    }
    wall = _difference_metric(
        arms[treatment]["wall_time_seconds"], arms[reference]["wall_time_seconds"]
    )
    treatment_bytes = arms[treatment]["model_visible_packet_bytes"]["total"]
    reference_bytes = arms[reference]["model_visible_packet_bytes"]["total"]
    unavailable = [
        metric for metric, value in token_differences.items() if not value["available"]
    ]
    unavailable.extend(
        f"all_attempt_{metric}"
        for metric, value in all_attempt_token_differences.items()
        if not value["available"]
    )
    if not wall["available"]:
        unavailable.append("wall_time_seconds")
    unavailable.append("monetary_cost")
    return {
        "name": name,
        "orientation": "treatment_minus_reference",
        "treatment": treatment,
        "reference": reference,
        "tokens": token_differences,
        "all_attempt_tokens": all_attempt_token_differences,
        "wall_time_seconds": wall,
        "accepted_completion_difference": arms[treatment]["accepted_completion_logs"]
        - arms[reference]["accepted_completion_logs"],
        "failed_attempt_difference": arms[treatment]["attempts"]["retry_history"][
            "count"
        ]
        - arms[reference]["attempts"]["retry_history"]["count"],
        "model_visible_packet_bytes": {
            "treatment_total": treatment_bytes,
            "reference_total": reference_bytes,
            "difference": treatment_bytes - reference_bytes,
            "relative_change": (
                (treatment_bytes - reference_bytes) / reference_bytes
                if reference_bytes
                else None
            ),
        },
        "unavailable_dimensions": sorted(set(unavailable)),
    }


def _sealed_mechanism_outcomes(validated: ValidatedRun) -> dict[str, Any]:
    """Validate and expose the preregistered zero-model mechanism outcomes."""
    snapshot = validated.controller["snapshots"]["gate_report"]
    gate_path = Path(str(snapshot["snapshot_path"]))
    gate = _read_json(gate_path)
    if (
        not isinstance(gate, dict)
        or gate.get("schema_version") != "qt4-zero-model-packet-gate-v1"
        or gate.get("passed") is not True
        or gate.get("failed_gates") != []
    ):
        raise ValueError("sealed gate report is not an accepted QT-4 gate")
    gate_inputs = gate.get("inputs")
    if not isinstance(gate_inputs, dict):
        raise ValueError("sealed gate report has no bound input hashes")
    expected_hashes = {
        # The gate's historical field name is ``question_spec``, but the
        # sealed runner intentionally passes the frozen packet-metadata CSV.
        "question_spec": validated.input_hashes["input"],
        **validated.input_hashes["packets"],
    }
    for name, expected_sha in expected_hashes.items():
        entry = gate_inputs.get(name)
        if not isinstance(entry, dict) or entry.get("sha256") != expected_sha:
            raise ValueError(f"sealed gate {name} hash does not match the analyzed run")

    gold = gate.get("evaluation_only_gold_metrics")
    dispatch = gate.get("dispatch")
    traversal = gate.get("traversal")
    footprint = gate.get("resource_footprint")
    equivalence = gate.get("equivalence")
    if not all(
        isinstance(value, dict)
        for value in (gold, dispatch, traversal, footprint, equivalence)
    ):
        raise ValueError("sealed gate omits registered mechanism outcomes")
    if validated.experiment_profile == "valid374" and (
        gate.get("scheduled_question_count") != len(validated.question_ids)
        # The zero-model gate canonicalizes its CSV-derived inventory by ID;
        # controller execution order remains bound separately to the spec.
        or gate.get("scheduled_question_ids") != sorted(validated.question_ids)
        or dispatch.get("version") != "micro-dispatch-v1"
        or dispatch.get("microbiology_questions")
        != len(validated.strata["dispatched"])
        or dispatch.get("non_microbiology_questions")
        != len(validated.strata["negative_control"])
        or dispatch.get("microbiology_question_ids")
        != sorted(validated.strata["dispatched"])
    ):
        raise ValueError("sealed gate dispatch does not match the frozen strata")
    recall = gold.get("recall")
    vocabulary = gold.get("vocabulary_gold_change")
    traversal_gold = gold.get("traversal_gold_gain")
    targets = traversal.get("target_outcomes")
    footprint_arms = footprint.get("arms")
    if not all(
        isinstance(value, dict)
        for value in (recall, vocabulary, traversal_gold, targets, footprint_arms)
    ):
        raise ValueError("sealed gate mechanism outcome shape is incomplete")
    for stratum in ("microbiology", "overall"):
        if not isinstance(recall.get(stratum), dict) or set(recall[stratum]) != set(
            ARM_NAMES
        ):
            raise ValueError(f"sealed gate recall is incomplete for {stratum}")
    if set(footprint_arms) != set(ARM_NAMES):
        raise ValueError("sealed gate packet-resource counts are incomplete")
    required_targets = {
        "fetched",
        "already_present",
        "missing",
        "resource_capped",
        "byte_capped",
    }
    if not required_targets.issubset(targets):
        raise ValueError("sealed gate traversal target outcomes are incomplete")
    registered_traversal_fields = (
        "raw_path_status_counts",
        "serialized_path_receipt_count",
        "path_receipts_omitted",
        "serialized_path_depth_counts",
        "max_serialized_depth_observed",
        "serialized_path_family_counts",
    )
    if validated.experiment_profile == "valid374" and any(
        field not in traversal for field in registered_traversal_fields
    ):
        raise ValueError("valid374 gate omits registered traversal reporting fields")

    return {
        "gate_report_sha256": sha256_file(gate_path),
        "gold_resource_recall": recall,
        "vocabulary_gold_change": vocabulary,
        "traversal_gold_gain": traversal_gold,
        "traversal": {
            "target_outcomes": targets,
            "fetch_attempt_count": traversal.get("fetch_attempt_count"),
            "added_resource_count": traversal.get("added_resource_count"),
            "added_serialized_bytes": traversal.get("added_serialized_bytes"),
            "path_receipts_omitted": traversal.get("path_receipts_omitted"),
            "questions_with_fetched_target": traversal.get(
                "questions_with_fetched_target"
            ),
            "serialized_path_family_counts": traversal.get(
                "serialized_path_family_counts"
            ),
            "raw_path_status_counts": traversal.get("raw_path_status_counts"),
            "serialized_path_receipt_count": traversal.get(
                "serialized_path_receipt_count"
            ),
            "serialized_path_depth_counts": traversal.get(
                "serialized_path_depth_counts"
            ),
            "max_serialized_depth_observed": traversal.get(
                "max_serialized_depth_observed"
            ),
            "diagnostic_report_path_use": traversal.get(
                "diagnostic_report_path_use"
            ),
        },
        "packet_resource_footprint": footprint,
        "negative_control_equivalence": {
            "packet": equivalence.get("non_micro_packet"),
            "prompt": equivalence.get("non_micro_prompt"),
        },
    }


def _strict_nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _valid374_promotion_assessment(
    contrasts: Mapping[str, dict[str, Any]], mechanisms: Mapping[str, Any]
) -> dict[str, Any]:
    vocab = mechanisms["vocabulary_gold_change"]
    traversal = mechanisms["traversal_gold_gain"]
    vocab_gained = _strict_nonnegative_int(
        vocab.get("gold_id_occurrences_gained")
    )
    vocab_lost = _strict_nonnegative_int(vocab.get("gold_id_occurrences_lost"))
    traversal_gained = _strict_nonnegative_int(
        traversal.get("gold_id_occurrences_gained")
    )
    traversal_lost = _strict_nonnegative_int(
        traversal.get("gold_id_occurrences_lost")
    )
    if None in (vocab_gained, vocab_lost, traversal_gained, traversal_lost):
        raise ValueError("sealed mechanism gains/losses must be nonnegative integers")

    def statistical_gates(contrast: Mapping[str, Any]) -> dict[str, bool]:
        mcnemar = contrast["mcnemar"]
        bootstrap = contrast["patient_cluster_bootstrap"]
        p_value = mcnemar.get("exact_two_sided_p")
        return {
            "favorable_dispatched_point_estimate": (
                contrast["accuracy_difference"] > 0
            ),
            "exact_mcnemar_significant": (
                mcnemar.get("estimable") is True
                and isinstance(p_value, (int, float))
                and not isinstance(p_value, bool)
                and p_value < 0.05
            ),
            "patient_cluster_ci_excludes_zero_in_favorable_direction": (
                bootstrap["ci_low"] > 0
            ),
            "pooled_degradation_within_one_point": (
                contrast["safety"]["pooled"]["passed"] is True
            ),
            "negative_control_degradation_within_one_point": (
                contrast["safety"]["negative_control"]["passed"] is True
            ),
        }

    h1_gates = {
        **statistical_gates(contrasts["qt4v_minus_a6a"]),
        "positive_gold_recall_change": vocab_gained > vocab_lost,
    }
    h1_promoted = all(h1_gates.values())
    h1 = {
        "tested": True,
        "gates": h1_gates,
        "promoted": h1_promoted,
        "decision": "promoted" if h1_promoted else "not_promoted",
    }

    h2_gates = {
        **statistical_gates(contrasts["qt4t_minus_qt4v"]),
        "traversal_gold_occurrence_gate": (
            traversal_gained >= 1 and traversal_lost == 0
        ),
    }
    h2_promoted = h1_promoted and all(h2_gates.values())
    h2 = {
        "tested": h1_promoted,
        "gates": h2_gates,
        "promoted": h2_promoted,
        "decision": (
            "not_tested_h1_failed"
            if not h1_promoted
            else ("promoted" if h2_promoted else "not_promoted")
        ),
    }
    decision = (
        "promote_neither"
        if not h1_promoted
        else (
            "promote_vocabulary_and_traversal"
            if h2_promoted
            else "promote_vocabulary_only"
        )
    )
    return {
        "profile": "valid374",
        "fixed_sequence": True,
        "alpha": 0.05,
        "h1_vocabulary": h1,
        "h2_traversal": h2,
        "vocabulary_promoted": h1_promoted,
        "traversal_promoted": h2_promoted,
        "decision": decision,
        "persistent_graph_storage_claim_supported": False,
    }


def _promotion_assessment(
    profile: str,
    contrasts: Mapping[str, dict[str, Any]],
    mechanisms: Mapping[str, Any],
) -> dict[str, Any]:
    if profile == "valid374":
        return _valid374_promotion_assessment(contrasts, mechanisms)
    vocabulary_gain = _coerce_nonnegative_int(
        mechanisms["vocabulary_gold_change"].get("gold_id_occurrences_gained")
    )
    traversal_gain = _coerce_nonnegative_int(
        mechanisms["traversal_gold_gain"].get("gold_id_occurrences_gained")
    )
    fetched = _coerce_nonnegative_int(
        mechanisms["traversal"]["target_outcomes"].get("fetched")
    )
    if vocabulary_gain is None or traversal_gain is None or fetched is None:
        raise ValueError("sealed mechanism gains must be nonnegative integers")
    vocabulary_changed = vocabulary_gain > 0
    traversal_changed = (
        traversal_gain > 0 and fetched > 0
    )
    mechanism_by_contrast = {
        "qt4v_minus_a6a": vocabulary_changed,
        "qt4t_minus_qt4v": traversal_changed,
    }
    assessments: dict[str, Any] = {}
    for name, mechanism_changed in mechanism_by_contrast.items():
        favorable = contrasts[name]["accuracy_difference"] > 0
        confirmation_candidate = favorable and mechanism_changed
        assessments[name] = {
            "favorable_accuracy_point_estimate": favorable,
            "expected_mechanism_metric_changed": mechanism_changed,
            "pooled_accuracy_degradation_measured": False,
            "confirmatory_run_candidate": confirmation_candidate,
            "promoted": False,
            "decision": (
                "eligible_for_full_set_or_untouched_holdout_confirmation"
                if confirmation_candidate
                else "drop_after_microbiology_screen"
            ),
            "reason": (
                "The 42-question screen cannot satisfy the preregistered pooled "
                "accuracy degradation condition; promotion requires confirmation."
            ),
        }
    return {
        "screen_scope": "42 microbiology questions; 367 controls were not answered",
        "persistent_graph_storage_claim_supported": False,
        "contrasts": assessments,
    }


def _accuracy_by_stratum(
    strata: Mapping[str, list[str]],
    labels: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        stratum: {
            arm: {
                "n": len(question_ids),
                "correct": sum(labels[arm][qid] for qid in question_ids),
                "accuracy": (
                    sum(labels[arm][qid] for qid in question_ids)
                    / len(question_ids)
                    if question_ids
                    else None
                ),
            }
            for arm in ARM_NAMES
        }
        for stratum, question_ids in strata.items()
    }


def _abstention_by_stratum(
    validated: ValidatedRun,
) -> dict[str, dict[str, dict[str, Any]]]:
    abstained: dict[str, set[str]] = {}
    for arm in ARM_NAMES:
        arm_abstained: set[str] = set()
        for completion in validated.accepted[arm]:
            answer = _read_json(completion.answer_path)
            reason = answer.get("insufficiency_reason")
            if isinstance(reason, str) and reason.strip():
                arm_abstained.add(completion.question_id)
        abstained[arm] = arm_abstained
    return {
        stratum: {
            arm: {
                "n": len(question_ids),
                "count": sum(qid in abstained[arm] for qid in question_ids),
                "rate": (
                    sum(qid in abstained[arm] for qid in question_ids)
                    / len(question_ids)
                    if question_ids
                    else None
                ),
            }
            for arm in ARM_NAMES
        }
        for stratum, question_ids in validated.strata.items()
    }


def _routing_by_stratum(
    *,
    strata: Mapping[str, list[str]],
    deterministic: Mapping[str, Mapping[str, int]],
    queue: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, int]]]:
    panel_hosts = {
        (str(item["arm"]), str(item["question_id"])) for item in queue
    }
    return {
        stratum: {
            arm: {
                "scheduled": len(question_ids),
                "deterministic": sum(
                    qid in deterministic[arm] for qid in question_ids
                ),
                "panel": sum((arm, qid) in panel_hosts for qid in question_ids),
            }
            for arm in ARM_NAMES
        }
        for stratum, question_ids in strata.items()
    }


def _text_report(result: dict[str, Any]) -> str:
    lines = [
        "QT-4 THREE-ARM RESULT",
        f"analysis_version: {result['analysis_version']}",
        f"experiment_profile: {result['experiment_profile']}",
        f"status: {result['status']}",
        (
            "sealed answers: "
            f"{result['sealed_completion']['accepted_answers']}/"
            f"{result['sealed_completion']['expected_answers']}"
        ),
        "",
        "ARM ACCURACY (POOLED)",
    ]
    for arm in ARM_NAMES:
        value = result["accuracy_by_arm"][arm]
        label = result["arm_display_labels"][arm]
        lines.append(
            f"{label}: {value['correct']}/{value['n']} "
            f"({value['accuracy']:.3%})"
        )
    if result["experiment_profile"] == "valid374":
        for stratum in ("dispatched", "negative_control"):
            lines.extend(["", f"ARM ACCURACY ({stratum.upper()})"])
            for arm in ARM_NAMES:
                value = result["accuracy_by_stratum"][stratum][arm]
                label = result["arm_display_labels"][arm]
                lines.append(
                    f"{label}: {value['correct']}/{value['n']} "
                    f"({value['accuracy']:.3%})"
                )
    lines.extend(["", "REGISTERED CONTRASTS"])
    for name, _treatment, _reference in REGISTERED_CONTRASTS:
        contrast = result["contrasts"][name]
        mcnemar = contrast["mcnemar"]
        p_text = (
            f"{mcnemar['exact_two_sided_p']:.6g}"
            if mcnemar["estimable"]
            else "unavailable (no discordant pairs)"
        )
        bootstrap = contrast["patient_cluster_bootstrap"]
        h2_gate_closed = (
            result["experiment_profile"] == "valid374"
            and name == "qt4t_minus_qt4v"
            and result["promotion_assessment"]["h2_traversal"]["tested"] is False
        )
        if h2_gate_closed:
            lines.append(
                f"{name}: {contrast['accuracy_difference']:+.3%}; descriptive only; "
                "H2 not tested because the fixed-sequence H1 gate did not pass"
            )
        else:
            lines.append(
                f"{name}: {contrast['accuracy_difference']:+.3%}; "
                f"discordant {contrast['discordant_treatment_only']}/"
                f"{contrast['discordant_reference_only']}; McNemar {p_text}; "
                f"cluster CI [{bootstrap['ci_low']:+.3%}, {bootstrap['ci_high']:+.3%}]"
            )
        if result["experiment_profile"] == "valid374":
            for stratum in ("pooled", "negative_control"):
                safety = contrast["safety"][stratum]
                lines.append(
                    f"  {stratum} safety: {safety['accuracy_difference']:+.3%}; "
                    f"within 1 point={safety['passed']}"
                )
    mechanisms = result["mechanism_outcomes"]
    targets = mechanisms["traversal"]["target_outcomes"]
    lines.extend(
        [
            "",
            "REGISTERED MECHANISM OUTCOMES (sealed zero-model gate)",
            (
                "gold resource recall: "
                + canonical_json(mechanisms["gold_resource_recall"])
            ),
            (
                "packet resource footprint: "
                + canonical_json(mechanisms["packet_resource_footprint"])
            ),
            (
                "vocabulary gold IDs gained/lost: "
                f"{mechanisms['vocabulary_gold_change']['gold_id_occurrences_gained']}/"
                f"{mechanisms['vocabulary_gold_change']['gold_id_occurrences_lost']}"
            ),
            (
                "traversal gold IDs gained/lost: "
                f"{mechanisms['traversal_gold_gain']['gold_id_occurrences_gained']}/"
                f"{mechanisms['traversal_gold_gain']['gold_id_occurrences_lost']}"
            ),
            (
                "traversal targets: "
                + ", ".join(f"{key}={value}" for key, value in targets.items())
            ),
            (
                "serialized path families: "
                + canonical_json(
                    mechanisms["traversal"]["serialized_path_family_counts"]
                )
            ),
            (
                "raw path statuses: "
                + canonical_json(
                    mechanisms["traversal"]["raw_path_status_counts"]
                )
            ),
            (
                "serialized path depths: "
                + canonical_json(
                    mechanisms["traversal"]["serialized_path_depth_counts"]
                )
            ),
        ]
    )
    promotion = result["promotion_assessment"]
    if result["experiment_profile"] == "valid374":
        lines.extend(
            [
                "",
                "FIXED-SEQUENCE PROMOTION",
                f"decision: {promotion['decision']}",
                "H1 gates: " + canonical_json(promotion["h1_vocabulary"]["gates"]),
                (
                    f"H2 tested: {promotion['h2_traversal']['tested']}; gates: "
                    + canonical_json(promotion["h2_traversal"]["gates"])
                ),
                "persistent graph storage claim supported: False",
            ]
        )
    lines.extend(["", "ABSTENTION BY STRATUM"])
    for stratum, arms in result["abstention_by_stratum"].items():
        for arm in ARM_NAMES:
            value = arms[arm]
            label = result["arm_display_labels"][arm]
            rate = "n/a" if value["rate"] is None else f"{value['rate']:.3%}"
            lines.append(
                f"{stratum}/{label}: {value['count']}/{value['n']} ({rate})"
            )
    lines.extend(["", "DETERMINISTIC/PANEL ROUTING BY STRATUM"])
    for stratum, arms in result["grading"]["routing_by_stratum"].items():
        for arm in ARM_NAMES:
            value = arms[arm]
            label = result["arm_display_labels"][arm]
            lines.append(
                f"{stratum}/{label}: deterministic={value['deterministic']} "
                f"panel={value['panel']} scheduled={value['scheduled']}"
            )
    lines.extend(["", "ECONOMICS (accepted completion logs only)"])
    for arm in ARM_NAMES:
        economics = result["economics"]["arms"][arm]
        label = result["arm_display_labels"][arm]
        input_total = economics["tokens"]["input_tokens"]["total"]
        output_total = economics["tokens"]["output_tokens"]["total"]
        total = economics["tokens"]["total_tokens"]["total"]
        all_attempt_total = economics["all_attempt_tokens"]["total_tokens"]["total"]
        retries = economics["attempts"]["retry_history"]["count"]
        packet_bytes = economics["model_visible_packet_bytes"]["total"]
        unavailable = ", ".join(economics["unavailable_dimensions"]) or "none"
        lines.append(
            f"{label}: input={input_total} output={output_total} total={total} "
            f"all_attempt_total={all_attempt_total} retries={retries} "
            f"packet_bytes={packet_bytes}; unavailable={unavailable}"
        )
    panel = result["economics"]["panel_judging"]
    accepted_panel = panel["accepted"]
    all_panel = panel["all_attempts"]
    lines.extend(
        [
            "",
            "PANEL JUDGING ECONOMICS",
            "verified judge config: "
            + canonical_json(result["grading"]["panel_judge_config"]),
            (
                f"accepted_calls={accepted_panel['calls']} "
                f"accepted_total={accepted_panel['tokens']['total_tokens']} "
                f"all_attempt_calls={all_panel['calls']} "
                f"all_attempt_total={all_panel['tokens']['total_tokens']}"
            ),
            "",
            "ECONOMIC CONTRASTS (treatment minus reference)",
        ]
    )
    for name, _treatment, _reference in REGISTERED_CONTRASTS:
        economics = result["economics"]["contrasts"][name]
        input_difference = economics["tokens"]["input_tokens"]["difference"]
        total_difference = economics["tokens"]["total_tokens"]["difference"]
        spend_difference = economics["all_attempt_tokens"]["total_tokens"][
            "difference"
        ]
        packet_difference = economics["model_visible_packet_bytes"]["difference"]
        unavailable = ", ".join(economics["unavailable_dimensions"]) or "none"
        lines.append(
            f"{name}: input_delta={input_difference} total_delta={total_difference} "
            f"all_attempt_total_delta={spend_difference} "
            f"packet_byte_delta={packet_difference}; unavailable={unavailable}"
        )
    lines.extend(["", "INPUT HASHES", canonical_json(result["input_hashes"]), ""])
    return "\n".join(lines)


def assemble_result(
    *,
    controller_manifest: Path,
    question_spec: Path,
    input_path: Path,
    arms: Mapping[str, ArmArtifacts],
    grading_dir: Path,
    expected_question_count: int | None = None,
    n_boot: int = 10_000,
) -> dict[str, Any]:
    """Verify all bound labels, then emit the registered result and economics."""
    if n_boot < 1:
        raise ValueError("n_boot must be positive")
    validated = validate_sealed_run(
        controller_manifest=controller_manifest,
        question_spec=question_spec,
        input_path=input_path,
        arms=arms,
        expected_question_count=expected_question_count,
    )
    if validated.experiment_profile == "valid374" and n_boot != 10_000:
        raise ValueError("valid374 analysis requires exactly 10000 bootstrap replicates")
    deterministic, queue, grading_manifest = _verify_grading_artifacts(
        validated, grading_dir
    )
    panel_verdicts, panel_token_usage, panel_judge_config = _verify_panel(
        grading_dir=grading_dir,
        queue=queue,
        controller=validated.controller,
    )
    labels = _labels_from_artifacts(
        question_ids=validated.question_ids,
        deterministic=deterministic,
        queue=queue,
        panel_verdicts=panel_verdicts,
    )

    accuracy_by_stratum = _accuracy_by_stratum(validated.strata, labels)
    accuracy_by_arm = accuracy_by_stratum["pooled"]
    contrasts = _profile_contrasts(
        profile=validated.experiment_profile,
        strata=validated.strata,
        gold=validated.gold,
        labels=labels,
        n_boot=n_boot,
    )
    arm_economics = {arm: _arm_economics(validated, arm) for arm in ARM_NAMES}
    contrast_economics = {
        name: _contrast_economics(
            name=name,
            treatment=treatment,
            reference=reference,
            arms=arm_economics,
        )
        for name, treatment, reference in REGISTERED_CONTRASTS
    }
    mechanism_outcomes = _sealed_mechanism_outcomes(validated)
    promotion_assessment = _promotion_assessment(
        validated.experiment_profile,
        contrasts,
        mechanism_outcomes,
    )
    abstention_by_stratum = _abstention_by_stratum(validated)
    routing_by_stratum = _routing_by_stratum(
        strata=validated.strata,
        deterministic=deterministic,
        queue=queue,
    )
    result_input_hashes = {
        **validated.input_hashes,
        "grading_manifest": sha256_file(grading_dir / "grading_manifest.json"),
        "det_verdicts": sha256_file(grading_dir / "det_verdicts.json"),
        "panel_queue": sha256_file(grading_dir / "panel_queue.jsonl"),
        "panel_cache": sha256_file(grading_dir / "panel_votes.json"),
        "panel_verdicts": sha256_file(grading_dir / "panel_verdicts.json"),
        "panel_verdict_manifest": sha256_file(
            grading_dir / "panel_verdicts.manifest.json"
        ),
    }
    result = {
        "analysis_version": ANALYSIS_VERSION,
        "status": EXPERIMENT_PROFILES[validated.experiment_profile][
            "result_status"
        ],
        "experiment_profile": validated.experiment_profile,
        "analysis_profile": {
            "name": validated.experiment_profile,
            "fixed_sequence_alpha": (
                0.05 if validated.experiment_profile == "valid374" else None
            ),
            "strata": {
                name: {"n": len(question_ids)}
                for name, question_ids in validated.strata.items()
            },
        },
        "question_ids": validated.question_ids,
        "arms": list(ARM_NAMES),
        "arm_display_labels": {
            "a6a": (
                "A6a-r (qo-v2.1)"
                if validated.experiment_profile == "valid374"
                else "A6a"
            ),
            "qt4v": "QT-4V",
            "qt4t": "QT-4T",
        },
        "registered_contrasts": [name for name, _, _ in REGISTERED_CONTRASTS],
        "grading": {
            "deterministic_grader_version": grade_a6a_confirmatory.GRADER_VERSION,
            "panel_judge_protocol_version": panel_grade.JUDGE_PROTOCOL_VERSION,
            "panel_opaque_id_version": panel_grade.OPAQUE_ID_VERSION,
            "deterministic_plus_panel_labels": len(validated.question_ids)
            * len(ARM_NAMES),
            "grading_manifest_sha256": sha256_file(
                grading_dir / "grading_manifest.json"
            ),
            "panel_queue_count": grading_manifest["outputs"]["panel_queue_count"],
            "partition": grading_manifest["partition"],
            "routing_by_stratum": routing_by_stratum,
            "panel_judge_config": panel_judge_config,
        },
        "sealed_completion": validated.completion_summary,
        "accuracy_by_arm": accuracy_by_arm,
        "accuracy_by_stratum": accuracy_by_stratum,
        "abstention_by_stratum": abstention_by_stratum,
        "contrasts": contrasts,
        "mechanism_outcomes": mechanism_outcomes,
        "promotion_assessment": promotion_assessment,
        "economics": {
            "scope": (
                "answer-arm accepted/all-attempt completion logs, sealed "
                "model-visible packets, and panel-judging call receipts"
            ),
            "arms": arm_economics,
            "contrasts": contrast_economics,
            "panel_judging": panel_token_usage,
        },
        "input_hashes": result_input_hashes,
    }
    _write_json(grading_dir / "final_result.json", result)
    _write_text_atomic(grading_dir / "final_result.txt", _text_report(result))
    return result
