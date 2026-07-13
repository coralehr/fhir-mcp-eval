#!/usr/bin/env python3
"""Run the frozen QT-4 microbiology contrast in sealed, balanced order.

The controller refuses model execution unless the exact full-409 zero-model
gate passed. On first launch it snapshots every model-affecting input into the
controller bundle and writes an immutable manifest while holding a singleton
lock. Resume accepts only audited completion receipts bound to that manifest.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


_BOOTSTRAPPED_ENV = "QT4_IMMUTABLE_BOOTSTRAP"
_PRELOCK_FD_ENV = "QT4_PREIMPORT_LOCK_FD"
_PRELOCK_PATH_ENV = "QT4_PREIMPORT_LOCK_PATH"
_BOOTSTRAP_FILES = (
    "run_qt4_experiment.py",
    "codex_harness.py",
    "qt4_packet_gate.py",
    "run_lock.py",
)


def _cli_path(flag: str, default: str) -> Path:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return Path(default)
    if index + 1 >= len(sys.argv):
        raise SystemExit(f"{flag} requires a path")
    return Path(sys.argv[index + 1])


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verify_bootstrap_bundle(stage_dir: Path) -> Path:
    manifest_path = stage_dir / "bootstrap-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid immutable bootstrap manifest: {manifest_path}") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or set(files) != set(_BOOTSTRAP_FILES):
        raise SystemExit("immutable bootstrap file inventory is incomplete")
    for name in _BOOTSTRAP_FILES:
        path = stage_dir / name
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != files[name]:
            raise SystemExit(f"immutable bootstrap file changed: {path}")
    return stage_dir / "run_qt4_experiment.py"


def _stage_bootstrap_bundle(controller_manifest: Path) -> Path:
    stage_dir = controller_manifest.resolve().parent / "bootstrap"
    if stage_dir.exists():
        return _verify_bootstrap_bundle(stage_dir)
    source_dir = Path(__file__).resolve().parent
    temporary = stage_dir.with_name(f".bootstrap.{os.getpid()}.tmp")
    temporary.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    try:
        for name in _BOOTSTRAP_FILES:
            payload = (source_dir / name).read_bytes()
            (temporary / name).write_bytes(payload)
            files[name] = _sha256_bytes(payload)
        manifest = {
            "kind": "qt4_immutable_preimport_bootstrap",
            "schema_version": "qt4-bootstrap-v1",
            "files": files,
        }
        (temporary / "bootstrap-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        stage_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary.rename(stage_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return _verify_bootstrap_bundle(stage_dir)


def _exec_immutable_bootstrap(*, live: bool) -> None:
    controller = _cli_path(
        "--controller-manifest", "runs/qt4-micro42-controller/manifest.json"
    )
    stage_dir = controller.resolve().parent / "bootstrap"
    if not live and not stage_dir.exists():
        if controller.resolve().exists():
            raise SystemExit(
                "controller manifest exists without its immutable bootstrap"
            )
        return

    lock_fd: int | None = None
    environment = os.environ.copy()
    if live:
        lock_path = _cli_path("--lock", "runs/.run_qt4_micro42.lock").resolve()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = os.read(lock_fd, 256).decode("utf-8", errors="replace").strip()
            os.close(lock_fd)
            print(f"ALREADY_RUNNING: {lock_path} is held by {owner or 'unknown PID'}")
            raise SystemExit(75)
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"pid={os.getpid()}\n".encode("utf-8"))
        os.fsync(lock_fd)
        os.set_inheritable(lock_fd, True)
        environment[_PRELOCK_FD_ENV] = str(lock_fd)
        environment[_PRELOCK_PATH_ENV] = str(lock_path)

    runner = _stage_bootstrap_bundle(controller)
    environment[_BOOTSTRAPPED_ENV] = "1"
    try:
        os.execve(
            sys.executable,
            [sys.executable, str(runner), *sys.argv[1:]],
            environment,
        )
    except BaseException:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        raise


def _bootstrap_before_project_imports() -> None:
    if __name__ != "__main__":
        return
    if os.environ.get(_BOOTSTRAPPED_ENV) == "1":
        _verify_bootstrap_bundle(Path(__file__).resolve().parent)
        return
    if "--live" in sys.argv:
        _exec_immutable_bootstrap(live=True)
    elif "--status" in sys.argv:
        _exec_immutable_bootstrap(live=False)


_bootstrap_before_project_imports()

from codex_harness import (  # noqa: E402 - immutable bootstrap runs first
    answer_matches_schema,
    audit_event_log,
    run_version,
    slugify,
    terminal_question_status,
)
from qt4_packet_gate import GateExpectations, compare_packet_files  # noqa: E402
from run_lock import (  # noqa: E402 - immutable bootstrap runs first
    AlreadyRunning,
    LOCK_BUSY_EXIT,
    acquire_single_instance,
)


REGISTERED_MODEL = "gpt-5.6-sol"
REGISTERED_REASONING_EFFORT = "high"
REGISTERED_SPEC_KIND = "qt4_micro_question_spec"
REGISTERED_SPEC_VERSION = "qt4-micro42-v1"
REGISTERED_ORDER_METHOD = "ascending sha256('qt4-micro42-20260713:' + question_id)"
REGISTERED_ORDER_SALT = "qt4-micro42-20260713:"
GATE_SCHEMA_VERSION = "qt4-zero-model-packet-gate-v1"
EXPECTED_TOTAL = 409
EXPECTED_MICRO = 42
EXPECTED_NON_MICRO = 367
PARTIAL_RUN_EXIT = 3
MAX_ATTEMPTS_PER_ITEM = 3
ATTEMPT_SCHEMA_VERSION = "qt4-attempt-v2"

REQUIRED_GATE_NAMES = {
    "scheduled_question_sets",
    "expected_total_questions",
    "expected_microbiology_questions",
    "expected_non_microbiology_questions",
    "live_packet_count",
    "micro_dispatch_v1_question_text_consistency",
    "micro_dispatch_v1_matches_analysis_stratum",
    "micro_dispatch_v1_feature_application",
    "a6a_dispatch_none",
    "qt4v_dispatch_exact",
    "qt4t_dispatch_exact",
    "effective_prompt_metadata_matches_frozen_input",
    "non_micro_packet_equivalence",
    "non_micro_prompt_equivalence",
    "qt4v_qt4t_micro_search_plan_equivalence",
    "qt4v_qt4t_micro_root_equivalence",
    "qt4v_micro_v1_observation_query_union",
    "qt4t_micro_v1_observation_query_union",
    "packets_exclude_benchmark_answer_keys",
    "query_fetch_receipts_complete_and_error_free",
    "qt4t_traversal_resource_shape",
    "qt4t_traversal_actual_limits",
    "qt4t_traversal_stats_consistency",
    "qt4t_traversal_receipt_integrity",
    "qt4v_minimum_vocab_gold_gain",
    "qt4t_traversal_fetch_observed",
    "qt4t_frozen_traversal_contract",
    "qt4t_minimum_traversal_gold_gain",
}


class _InheritedInstanceLock:
    """Own a bootstrap-acquired flock until the staged runner exits."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: int | None = fd

    def close(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> _InheritedInstanceLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _acquire_live_instance_lock(path: Path) -> Any:
    """Adopt the pre-import lock in a staged child, or acquire it in tests.

    A real ``--live`` CLI invocation is re-executed from the immutable
    bootstrap before project imports. That child must keep the inherited open
    file description instead of opening the lock file again: on macOS, a
    second ``flock`` from the same process conflicts with the first one.
    """

    raw_fd = os.environ.get(_PRELOCK_FD_ENV)
    recorded_path = os.environ.get(_PRELOCK_PATH_ENV)
    if raw_fd is None and recorded_path is None:
        if os.environ.get(_BOOTSTRAPPED_ENV) == "1":
            raise RuntimeError("immutable bootstrap child has no inherited lock")
        return acquire_single_instance(path)
    if raw_fd is None or recorded_path is None:
        raise RuntimeError("incomplete immutable bootstrap lock handoff")

    resolved_path = path.resolve()
    if Path(recorded_path).resolve() != resolved_path:
        raise RuntimeError("immutable bootstrap lock path does not match --lock")
    try:
        fd = int(raw_fd)
        descriptor_stat = os.fstat(fd)
        path_stat = resolved_path.stat()
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("invalid immutable bootstrap lock descriptor") from exc
    if fd < 0 or (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        raise RuntimeError("immutable bootstrap lock descriptor targets another file")

    # The controller owns the descriptor, but model/harness subprocesses must
    # not keep the lock alive after the controller exits.
    os.set_inheritable(fd, False)
    os.environ.pop(_PRELOCK_FD_ENV, None)
    os.environ.pop(_PRELOCK_PATH_ENV, None)
    return _InheritedInstanceLock(resolved_path, fd)


@dataclass(frozen=True)
class Arm:
    name: str
    packet_path: Path
    out_dir: Path


@dataclass(frozen=True)
class ControllerBundle:
    manifest: dict[str, Any]
    manifest_sha256: str
    arms: list[Arm]
    input_path: Path
    schema_path: Path
    harness_path: Path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_question_spec(path: Path) -> list[str]:
    spec = _read_json(path)
    if not isinstance(spec, dict) or not isinstance(spec.get("question_ids"), list):
        raise ValueError("question spec must contain a question_ids list")
    question_ids = [str(value) for value in spec["question_ids"]]
    if not question_ids or any(not value for value in question_ids):
        raise ValueError("question_ids must be non-empty strings")
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("question_ids must be unique")
    expected = spec.get("expected_question_count")
    if expected is None or int(expected) != len(question_ids):
        raise ValueError(
            "expected_question_count must equal the number of scheduled question_ids"
        )
    return question_ids


def validate_registered_question_spec(spec_path: Path, input_path: Path) -> list[str]:
    spec = _read_json(spec_path)
    if not isinstance(spec, dict):
        raise ValueError("registered question spec must be a JSON object")
    expected_metadata = {
        "kind": REGISTERED_SPEC_KIND,
        "version": REGISTERED_SPEC_VERSION,
        "order_method": REGISTERED_ORDER_METHOD,
        "expected_question_count": EXPECTED_MICRO,
    }
    for key, expected in expected_metadata.items():
        if spec.get(key) != expected:
            raise ValueError(f"registered question spec {key} must equal {expected!r}")

    question_ids = load_question_spec(spec_path)
    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    dataset_ids = [str(row.get("question_id") or "") for row in rows]
    if len(rows) != EXPECTED_TOTAL or len(set(dataset_ids)) != EXPECTED_TOTAL:
        raise ValueError("registered input must contain 409 unique question IDs")
    micro_ids = [
        str(row["question_id"])
        for row in rows
        if str(row.get("main_table_name") or "").strip().lower()
        == "microbiologyevents"
    ]
    expected_order = sorted(
        micro_ids,
        key=lambda qid: hashlib.sha256(
            f"{REGISTERED_ORDER_SALT}{qid}".encode("utf-8")
        ).hexdigest(),
    )
    if len(expected_order) != EXPECTED_MICRO or question_ids != expected_order:
        raise ValueError(
            "question spec IDs/order do not match the frozen microbiology selection rule"
        )
    return question_ids


def validate_registered_execution(*, model: str, reasoning_effort: str) -> None:
    if model != REGISTERED_MODEL or reasoning_effort != REGISTERED_REASONING_EFFORT:
        raise ValueError(
            f"QT-4 is pinned to {REGISTERED_MODEL!r} at "
            f"{REGISTERED_REASONING_EFFORT!r} effort"
        )


def _packet_question_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict) or "question_id" not in item:
                raise ValueError(f"{path}:{line_number} has no question_id")
            qid = str(item["question_id"])
            if qid in ids:
                raise ValueError(f"{path} contains duplicate question_id {qid}")
            ids.add(qid)
    return ids


def validate_output_directories(arms: list[Arm]) -> None:
    resolved = [(arm.name, arm.out_dir.resolve()) for arm in arms]
    for index, (left_name, left) in enumerate(resolved):
        for right_name, right in resolved[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError(
                    f"arm output directories must be distinct and non-nested: "
                    f"{left_name}={left}, {right_name}={right}"
                )


def validate_preflight(
    *,
    question_ids: list[str],
    arms: list[Arm],
    gate_report_path: Path,
    input_path: Path,
) -> dict[str, Any]:
    gate = _read_json(gate_report_path)
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        raise ValueError("zero-model packet gate did not pass")
    if gate.get("schema_version") != GATE_SCHEMA_VERSION:
        raise ValueError("zero-model packet gate schema/version is not frozen QT-4 v1")
    if gate.get("failed_gates") != []:
        raise ValueError("zero-model packet gate reports failed gates")
    recomputed = compare_packet_files(
        a6a_path=next(arm.packet_path for arm in arms if arm.name == "a6a"),
        qt4v_path=next(arm.packet_path for arm in arms if arm.name == "qt4v"),
        qt4t_path=next(arm.packet_path for arm in arms if arm.name == "qt4t"),
        question_spec_path=input_path,
        expectations=GateExpectations(
            expected_total=EXPECTED_TOTAL,
            expected_micro=EXPECTED_MICRO,
            expected_non_micro=EXPECTED_NON_MICRO,
        ),
    )
    if gate != recomputed:
        raise ValueError(
            "stored zero-model gate does not equal a fresh deterministic recomputation"
        )

    raw_gates = gate.get("gates")
    if not isinstance(raw_gates, list):
        raise ValueError("zero-model packet gate has no named gate inventory")
    gate_by_name: dict[str, dict[str, Any]] = {}
    for item in raw_gates:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("zero-model packet gate inventory is malformed")
        if item["name"] in gate_by_name:
            raise ValueError(f"duplicate zero-model gate {item['name']}")
        gate_by_name[item["name"]] = item
    missing_gates = REQUIRED_GATE_NAMES - set(gate_by_name)
    if missing_gates or any(
        gate_by_name[name].get("passed") is not True for name in REQUIRED_GATE_NAMES
    ):
        raise ValueError(
            "zero-model packet gate is missing/passing no required gates: "
            + ",".join(sorted(missing_gates))
        )

    gate_ids = [str(value) for value in gate.get("scheduled_question_ids", [])]
    if (
        len(gate_ids) != EXPECTED_TOTAL
        or len(gate_ids) != len(set(gate_ids))
        or gate.get("scheduled_question_count") != EXPECTED_TOTAL
    ):
        raise ValueError("gate report must cover exactly 409 unique questions")
    dispatch = gate.get("dispatch") if isinstance(gate.get("dispatch"), dict) else {}
    gate_micro_ids = [
        str(value) for value in dispatch.get("microbiology_question_ids", [])
    ]
    if (
        dispatch.get("microbiology_questions") != EXPECTED_MICRO
        or dispatch.get("non_microbiology_questions") != EXPECTED_NON_MICRO
        or len(gate_micro_ids) != len(set(gate_micro_ids))
        or set(gate_micro_ids) != set(question_ids)
    ):
        raise ValueError("gate microbiology IDs/counts do not match the frozen spec")

    expectations = gate.get("gate_expectations")
    if not isinstance(expectations, dict) or {
        "expected_total": expectations.get("expected_total"),
        "expected_micro": expectations.get("expected_micro"),
        "expected_non_micro": expectations.get("expected_non_micro"),
    } != {
        "expected_total": EXPECTED_TOTAL,
        "expected_micro": EXPECTED_MICRO,
        "expected_non_micro": EXPECTED_NON_MICRO,
    }:
        raise ValueError("gate report did not enforce the frozen 409/42/367 counts")

    equivalence = gate.get("equivalence")
    if not isinstance(equivalence, dict):
        raise ValueError("gate report has no equivalence results")
    for key in ("non_micro_packet", "non_micro_prompt"):
        result = equivalence.get(key)
        if not isinstance(result, dict) or result.get("matched") != EXPECTED_NON_MICRO or result.get(
            "total"
        ) != EXPECTED_NON_MICRO:
            raise ValueError(f"gate {key} must prove 367/367 negative-control identity")

    gate_inputs = gate.get("inputs") if isinstance(gate.get("inputs"), dict) else {}
    recorded_spec = gate_inputs.get("question_spec")
    if not isinstance(recorded_spec, dict) or recorded_spec.get("sha256") != _sha256_file(
        input_path
    ):
        raise ValueError("gate question_spec hash does not match --input")

    gate_set = set(gate_ids)
    packet_counts: dict[str, int] = {}
    for arm in arms:
        packet_ids = _packet_question_ids(arm.packet_path)
        if packet_ids != gate_set:
            raise ValueError(f"{arm.name} packet question IDs no longer match the gate")
        recorded_input = gate_inputs.get(arm.name)
        if not isinstance(recorded_input, dict) or recorded_input.get(
            "sha256"
        ) != _sha256_file(arm.packet_path):
            raise ValueError(f"{arm.name} packet hash no longer matches the gate")
        packet_counts[arm.name] = len(packet_ids)
    return {
        "scheduled_question_count": len(question_ids),
        "gate_question_count": len(gate_ids),
        "packet_question_counts": packet_counts,
        "gate_report_sha256": _sha256_file(gate_report_path),
    }


def _critical_source_paths(
    *,
    spec_path: Path,
    gate_report_path: Path,
    input_path: Path,
    arms: list[Arm],
    schema_path: Path,
    harness_path: Path,
) -> dict[str, Path]:
    return {
        "spec": spec_path,
        "gate_report": gate_report_path,
        "input": input_path,
        "schema": schema_path,
        "harness": harness_path,
        "runner": Path(__file__).resolve(),
        "run_lock": Path(__file__).resolve().with_name("run_lock.py"),
        "gate_code": Path(__file__).resolve().with_name("qt4_packet_gate.py"),
        **{f"packet_{arm.name}": arm.packet_path for arm in arms},
    }


def validate_and_bind_sources(
    *,
    spec_path: Path,
    gate_report_path: Path,
    input_path: Path,
    arms: list[Arm],
    schema_path: Path = Path("schemas/codex_answer.schema.json"),
    harness_path: Path = Path("codex_harness.py"),
    post_validation_hook: Callable[[], None] | None = None,
) -> tuple[list[str], dict[str, Any], dict[str, str]]:
    """Validate frozen sources and bind validation to their exact bytes.

    The optional hook exists only to make the validation-to-snapshot race
    testable. Production callers leave it unset.
    """
    sources = _critical_source_paths(
        spec_path=spec_path,
        gate_report_path=gate_report_path,
        input_path=input_path,
        arms=arms,
        schema_path=schema_path,
        harness_path=harness_path,
    )
    before = {name: _sha256_file(path) for name, path in sources.items()}
    question_ids = validate_registered_question_spec(spec_path, input_path)
    preflight = validate_preflight(
        question_ids=question_ids,
        arms=arms,
        gate_report_path=gate_report_path,
        input_path=input_path,
    )
    if post_validation_hook is not None:
        post_validation_hook()
    after = {name: _sha256_file(path) for name, path in sources.items()}
    if after != before:
        changed = sorted(name for name in sources if before[name] != after[name])
        raise ValueError(
            "validated source changed before controller sealing: " + ",".join(changed)
        )
    bound_preflight = {
        **preflight,
        "validated_source_sha256": before,
    }
    return question_ids, bound_preflight, before


def interleaved_schedule(
    question_ids: Iterable[str], arms: list[Arm]
) -> list[tuple[str, Arm]]:
    if not arms:
        raise ValueError("at least one arm is required")
    schedule: list[tuple[str, Arm]] = []
    for index, qid in enumerate(question_ids):
        offset = index % len(arms)
        rotated = arms[offset:] + arms[:offset]
        schedule.extend((str(qid), arm) for arm in rotated)
    return schedule


def build_harness_command(
    *,
    arm: Arm,
    question_id: str,
    input_path: Path,
    schema_path: Path,
    timeout: int,
    model: str,
    reasoning_effort: str,
    codex_bin: str,
    python_bin: str = sys.executable,
    harness_path: Path = Path("codex_harness.py"),
) -> list[str]:
    return [
        python_bin,
        str(harness_path),
        "--mode",
        "packet",
        "--packet-json",
        str(arm.packet_path),
        "--input",
        str(input_path),
        "--schema",
        str(schema_path),
        "--out-dir",
        str(arm.out_dir),
        "--timeout",
        str(timeout),
        "--model",
        model,
        "--reasoning-effort",
        reasoning_effort,
        "--codex-bin",
        codex_bin,
        "--question-id",
        question_id,
        "--live",
        "--skip-existing",
    ]


def _snapshot_plan(
    *,
    controller_manifest: Path,
    spec_path: Path,
    gate_report_path: Path,
    input_path: Path,
    arms: list[Arm],
    schema_path: Path,
    harness_path: Path,
    validated_source_hashes: dict[str, str],
) -> dict[str, dict[str, str]]:
    artifact_dir = controller_manifest.parent / "artifacts"
    sources = {
        "spec": spec_path,
        "gate_report": gate_report_path,
        "input": input_path,
        "schema": schema_path,
        "harness": harness_path,
        "runner": Path(__file__).resolve(),
        "run_lock": Path(__file__).resolve().with_name("run_lock.py"),
        "gate_code": Path(__file__).resolve().with_name("qt4_packet_gate.py"),
        **{f"packet_{arm.name}": arm.packet_path for arm in arms},
    }
    suffixes = {
        "spec": ".json",
        "gate_report": ".json",
        "input": ".csv",
        "schema": ".json",
        "harness": ".py",
        "runner": ".py",
        "run_lock": ".py",
        "gate_code": ".py",
        **{f"packet_{arm.name}": ".jsonl" for arm in arms},
    }
    plan: dict[str, dict[str, str]] = {}
    for name, path in sources.items():
        observed_sha = _sha256_file(path)
        if name not in validated_source_hashes:
            raise ValueError(f"snapshot source was not bound during validation: {name}")
        expected_sha = validated_source_hashes[name]
        if observed_sha != expected_sha:
            raise ValueError(f"validated source changed before snapshot plan: {name}")
        plan[name] = {
            "source_path": str(path.resolve()),
            "snapshot_path": str((artifact_dir / f"{name}{suffixes[name]}").resolve()),
            "sha256": expected_sha,
        }
    return plan


def build_controller_identity(
    *,
    controller_manifest: Path,
    spec_path: Path,
    gate_report_path: Path,
    input_path: Path,
    question_ids: list[str],
    arms: list[Arm],
    schema_path: Path,
    harness_path: Path,
    model: str,
    reasoning_effort: str,
    timeout: int,
    codex_bin: str,
    preflight: dict[str, Any],
    validated_source_hashes: dict[str, str],
) -> dict[str, Any]:
    expected_bound_sources = {
        "spec",
        "gate_report",
        "input",
        "schema",
        "harness",
        "runner",
        "run_lock",
        "gate_code",
        *(f"packet_{arm.name}" for arm in arms),
    }
    if set(validated_source_hashes) != expected_bound_sources:
        raise ValueError("controller source binding is incomplete or has unknown inputs")
    resolved_codex = shutil.which(codex_bin) or str(Path(codex_bin).resolve())
    snapshots = _snapshot_plan(
        controller_manifest=controller_manifest,
        spec_path=spec_path,
        gate_report_path=gate_report_path,
        input_path=input_path,
        arms=arms,
        schema_path=schema_path,
        harness_path=harness_path,
        validated_source_hashes=validated_source_hashes,
    )
    return {
        "kind": "qt4_micro_interleaved_controller_manifest",
        "schema_version": "qt4-controller-v2",
        "question_ids": question_ids,
        "schedule": [
            {"question_id": qid, "arm": arm.name}
            for qid, arm in interleaved_schedule(question_ids, arms)
        ],
        "execution": {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "timeout_seconds": timeout,
            "codex_bin": str(Path(resolved_codex).resolve()),
            "codex_version": run_version(str(resolved_codex)),
            "python_bin": str(Path(sys.executable).resolve()),
            "python_version": sys.version.split()[0],
            "ignore_user_config": True,
            "ignore_rules": True,
        },
        "outputs": {arm.name: str(arm.out_dir.resolve()) for arm in arms},
        "snapshots": snapshots,
        "preflight": preflight,
    }


def _terminal_files_exist(arms: list[Arm]) -> bool:
    for arm in arms:
        questions = arm.out_dir / "questions"
        if questions.exists() and any(path.is_file() for path in questions.rglob("*")):
            return True
    return False


def _copy_or_verify_snapshot(entry: dict[str, str]) -> None:
    source = Path(entry["source_path"])
    destination = Path(entry["snapshot_path"])
    expected_sha = entry["sha256"]
    if _sha256_file(source) != expected_sha:
        raise ValueError(f"source changed during controller sealing: {source}")
    if destination.exists():
        if _sha256_file(destination) != expected_sha:
            raise ValueError(f"immutable controller snapshot changed: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    if _sha256_file(temporary) != expected_sha:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"controller snapshot copy hash mismatch: {source}")
    os.replace(temporary, destination)


def seal_controller_bundle(
    *,
    manifest_path: Path,
    identity: dict[str, Any],
    arms: list[Arm],
) -> ControllerBundle:
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("controller manifest is not an object")
        existing_identity = {
            key: value for key, value in manifest.items() if key != "created_at"
        }
        if existing_identity != identity:
            raise ValueError("immutable controller manifest does not match this resume")
    else:
        if _terminal_files_exist(arms):
            raise ValueError(
                "terminal outputs exist without a controller manifest; refusing stale adoption"
            )
        for entry in identity["snapshots"].values():
            _copy_or_verify_snapshot(entry)
        manifest = {"created_at": dt.datetime.now(dt.UTC).isoformat(), **identity}
        _write_json_atomic(manifest_path, manifest)

    for entry in manifest["snapshots"].values():
        snapshot = Path(entry["snapshot_path"])
        if not snapshot.exists() or _sha256_file(snapshot) != entry["sha256"]:
            raise ValueError(f"controller snapshot missing or changed: {snapshot}")
    snapshot_arms = [
        Arm(
            arm.name,
            Path(manifest["snapshots"][f"packet_{arm.name}"]["snapshot_path"]),
            arm.out_dir,
        )
        for arm in arms
    ]
    return ControllerBundle(
        manifest=manifest,
        manifest_sha256=_sha256_file(manifest_path),
        arms=snapshot_arms,
        input_path=Path(manifest["snapshots"]["input"]["snapshot_path"]),
        schema_path=Path(manifest["snapshots"]["schema"]["snapshot_path"]),
        harness_path=Path(manifest["snapshots"]["harness"]["snapshot_path"]),
    )


def _question_dir(arm: Arm, question_id: str) -> Path:
    return arm.out_dir / "questions" / slugify(question_id)


def _completion_path(arm: Arm, question_id: str) -> Path:
    return _question_dir(arm, question_id) / "completion.json"


def _attempts_dir(arm: Arm, question_id: str) -> Path:
    return _question_dir(arm, question_id) / "attempts"


def _attempt_ledger_path(arm: Arm, question_id: str) -> Path:
    return _question_dir(arm, question_id) / "attempts.jsonl"


def _valid_answer_shape(path: Path) -> bool:
    try:
        value = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and isinstance(value.get("answer"), str)
        and isinstance(value.get("source_resource_ids"), list)
        and all(isinstance(item, str) for item in value["source_resource_ids"])
        and isinstance(value.get("evidence_summary"), str)
        and (
            value.get("insufficiency_reason") is None
            or isinstance(value.get("insufficiency_reason"), str)
        )
    )


def is_terminal_attempt(
    arm: Arm, question_id: str, *, controller_manifest_sha256: str
) -> bool:
    question_dir = _question_dir(arm, question_id)
    completion_path = _completion_path(arm, question_id)
    if not completion_path.exists():
        return False
    try:
        receipt = _read_json(completion_path)
    except (OSError, json.JSONDecodeError):
        return False
    if (
        not isinstance(receipt, dict)
        or receipt.get("kind") != "qt4_attempt_completion"
        or receipt.get("schema_version") != ATTEMPT_SCHEMA_VERSION
        or receipt.get("status") != "answered"
        or receipt.get("model") != REGISTERED_MODEL
        or receipt.get("reasoning_effort") != REGISTERED_REASONING_EFFORT
        or not isinstance(receipt.get("attempt_number"), int)
    ):
        return False
    if (
        receipt.get("controller_manifest_sha256") != controller_manifest_sha256
        or receipt.get("arm") != arm.name
        or receipt.get("question_id") != question_id
        or receipt.get("packet_sha256") != _sha256_file(arm.packet_path)
    ):
        return False
    answer_path = question_dir / "answer.json"
    event_path = question_dir / "events.jsonl"
    prompt_path = question_dir / "prompt.txt"
    if terminal_question_status(question_dir) != "answered":
        return False
    if not _valid_answer_shape(answer_path) or audit_event_log(event_path)["contaminated"]:
        return False
    expected_files = {
        "answer_sha256": answer_path,
        "event_log_sha256": event_path,
        "prompt_sha256": prompt_path,
    }
    return all(
        path.exists() and receipt.get(key) == _sha256_file(path)
        for key, path in expected_files.items()
    )


def _attempt_failure_exists(arm: Arm, question_id: str) -> bool:
    question_dir = _question_dir(arm, question_id)
    if any(
        (question_dir / marker).exists()
        for marker in ("contamination.json", "stale_artifact.json")
    ):
        return True
    if any(_attempts_dir(arm, question_id).glob("attempt-*/attempt.json")):
        return True
    completion = _completion_path(arm, question_id)
    if not completion.exists():
        return False
    try:
        return _read_json(completion).get("status") != "answered"
    except (OSError, json.JSONDecodeError, AttributeError):
        return True


def _retry_cap_reached(arm: Arm, question_id: str) -> bool:
    return len(_attempt_receipts(arm, question_id)) >= MAX_ATTEMPTS_PER_ITEM


def _extract_event_usage(event_path: Path) -> dict[str, int | float]:
    usage: dict[str, int | float] = {}
    if not event_path.exists():
        return usage
    for line in event_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        candidate = event.get("usage")
        if isinstance(candidate, dict):
            usage = {
                str(key): value
                for key, value in candidate.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
    return usage


def _harness_question_result(arm: Arm, question_id: str) -> dict[str, Any] | None:
    summary_path = arm.out_dir / "summary.json"
    if not summary_path.exists():
        return None
    try:
        summary = _read_json(summary_path)
    except (OSError, json.JSONDecodeError):
        return None
    questions = summary.get("questions") if isinstance(summary, dict) else None
    if not isinstance(questions, list):
        return None
    for item in questions:
        if isinstance(item, dict) and str(item.get("question_id")) == question_id:
            return item
    return None


def _attempt_receipts(arm: Arm, question_id: str) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    attempts_dir = _attempts_dir(arm, question_id)
    if not attempts_dir.exists():
        return receipts
    for path in sorted(attempts_dir.glob("attempt-*/attempt.json")):
        try:
            value = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            receipts.append(value)
    return receipts


def _ledger_receipts(arm: Arm, question_id: str) -> list[dict[str, Any]]:
    path = _attempt_ledger_path(arm, question_id)
    if not path.exists():
        return []
    receipts: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, dict):
            return []
        receipts.append(value)
    return receipts


def _append_attempt_ledger(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _archive_failed_attempt(
    *,
    arm: Arm,
    question_id: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Move one failed attempt into an immutable numbered provenance bundle."""
    question_dir = _question_dir(arm, question_id)
    attempt_number = int(receipt["attempt_number"])
    attempt_dir = _attempts_dir(arm, question_id) / f"attempt-{attempt_number:04d}"
    if attempt_dir.exists():
        raise ValueError(f"attempt archive already exists: {attempt_dir}")
    attempt_dir.mkdir(parents=True)
    archived_files: dict[str, dict[str, str]] = {}
    movable_names = (
        "prompt.txt",
        "events.jsonl",
        "command.json",
        "answer.json",
        "answer.failed.json",
        "answer.contaminated.json",
        "answer.stale.json",
    )
    for name in movable_names:
        source = question_dir / name
        if not source.exists():
            continue
        destination = attempt_dir / name
        source.replace(destination)
        archived_files[name] = {
            "path": str(destination),
            "sha256": _sha256_file(destination),
        }
    for name in ("contamination.json", "stale_artifact.json"):
        source = question_dir / name
        if not source.exists():
            continue
        destination = attempt_dir / name
        shutil.copyfile(source, destination)
        archived_files[name] = {
            "path": str(destination),
            "sha256": _sha256_file(destination),
        }
    archived = {
        **receipt,
        "archived_files": archived_files,
        "attempt_receipt_path": str(attempt_dir / "attempt.json"),
    }
    _write_json_atomic(attempt_dir / "attempt.json", archived)
    _append_attempt_ledger(_attempt_ledger_path(arm, question_id), archived)
    return archived


def _blocking_artifact_reason(
    arm: Arm,
    question_id: str,
    *,
    controller_manifest_sha256: str,
) -> str | None:
    """Return why an item cannot be safely resumed, without adopting artifacts."""
    question_dir = _question_dir(arm, question_id)
    if (question_dir / "contamination.json").exists():
        return "contamination_marker"
    if (question_dir / "stale_artifact.json").exists():
        return "stale_artifact_marker"

    completion = _completion_path(arm, question_id)
    completion_valid = False
    if completion.exists():
        completion_valid = is_terminal_attempt(
            arm,
            question_id,
            controller_manifest_sha256=controller_manifest_sha256,
        )
        if not completion_valid:
            return "invalid_or_cross_controller_completion"

    canonical_artifacts = (
        "answer.json",
        "answer.failed.json",
        "answer.contaminated.json",
        "answer.stale.json",
        "prompt.txt",
        "events.jsonl",
        "command.json",
    )
    if not completion_valid and any(
        (question_dir / name).exists() for name in canonical_artifacts
    ):
        return "orphan_canonical_artifacts"

    archived = _attempt_receipts(arm, question_id)
    archive_dirs = sorted(_attempts_dir(arm, question_id).glob("attempt-*"))
    ledger = _ledger_receipts(arm, question_id)
    if len(archive_dirs) != len(archived) or archived != ledger:
        return "malformed_or_nonappend_attempt_ledger"
    if [receipt.get("attempt_number") for receipt in archived] != list(
        range(1, len(archived) + 1)
    ):
        return "noncontiguous_attempt_ledger"
    for receipt in archived:
        if (
            receipt.get("kind") != "qt4_attempt_completion"
            or receipt.get("schema_version") != ATTEMPT_SCHEMA_VERSION
            or receipt.get("status")
            not in {"transient_failure", "contaminated", "stale_artifact"}
            or receipt.get("model") != REGISTERED_MODEL
            or receipt.get("reasoning_effort") != REGISTERED_REASONING_EFFORT
        ):
            return "malformed_attempt_receipt"
        if receipt.get("controller_manifest_sha256") != controller_manifest_sha256:
            return "cross_controller_attempt_archive"
        if receipt.get("arm") != arm.name or receipt.get("question_id") != question_id:
            return "misbound_attempt_archive"
        if receipt.get("packet_sha256") != _sha256_file(arm.packet_path):
            return "stale_packet_attempt_archive"
        if receipt.get("status") in {"contaminated", "stale_artifact"}:
            return f"archived_{receipt['status']}"
        archived_files = receipt.get("archived_files")
        if not isinstance(archived_files, dict):
            return "malformed_attempt_archive_files"
        for metadata in archived_files.values():
            if not isinstance(metadata, dict):
                return "malformed_attempt_archive_files"
            path = Path(str(metadata.get("path") or ""))
            if not path.is_file() or metadata.get("sha256") != _sha256_file(path):
                return "changed_attempt_archive"
    if completion_valid:
        completion_receipt = _read_json(completion)
        if completion_receipt.get("attempt_number") != len(archived) + 1:
            return "noncontiguous_accepted_attempt"
    return None


def _write_attempt_receipt(
    *,
    arm: Arm,
    question_id: str,
    controller_manifest_sha256: str,
    returncode: int,
    model: str = REGISTERED_MODEL,
    reasoning_effort: str = REGISTERED_REASONING_EFFORT,
    attempt_number: int | None = None,
    schema_path: Path = Path("schemas/codex_answer.schema.json"),
) -> dict[str, Any]:
    question_dir = _question_dir(arm, question_id)
    answer_path = question_dir / "answer.json"
    event_path = question_dir / "events.jsonl"
    prompt_path = question_dir / "prompt.txt"
    audit = audit_event_log(event_path)
    answered = (
        returncode == 0
        and terminal_question_status(question_dir) == "answered"
        and answer_matches_schema(answer_path, schema_path)
        and not audit["contaminated"]
    )
    if attempt_number is None:
        attempt_number = len(_attempt_receipts(arm, question_id)) + 1
    receipt: dict[str, Any] = {
        "kind": "qt4_attempt_completion",
        "schema_version": ATTEMPT_SCHEMA_VERSION,
        "controller_manifest_sha256": controller_manifest_sha256,
        "arm": arm.name,
        "question_id": question_id,
        "packet_sha256": _sha256_file(arm.packet_path),
        "schema_sha256": _sha256_file(schema_path),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "attempt_number": attempt_number,
        "harness_exit_code": returncode,
        "status": "answered" if answered else "invalid",
        "event_integrity": audit,
        "usage": _extract_event_usage(event_path),
    }
    harness_result = _harness_question_result(arm, question_id)
    receipt["harness_result"] = harness_result
    receipt["returncode"] = (
        harness_result.get("returncode")
        if isinstance(harness_result, dict)
        else returncode
    )
    for key, path in {
        "answer_sha256": answer_path,
        "event_log_sha256": event_path,
        "prompt_sha256": prompt_path,
    }.items():
        receipt[key] = _sha256_file(path) if path.exists() else None
    if answered:
        _write_json_atomic(_completion_path(arm, question_id), receipt)
    return receipt


def _failed_attempt_status(receipt: dict[str, Any], question_dir: Path) -> str:
    if (question_dir / "stale_artifact.json").exists():
        return "stale_artifact"
    if (question_dir / "contamination.json").exists() or receipt.get(
        "event_integrity", {}
    ).get("contaminated"):
        return "contaminated"
    return "transient_failure"


def _sum_usage(receipts: Iterable[dict[str, Any]]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}
    for receipt in receipts:
        usage = receipt.get("usage")
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[str(key)] = totals.get(str(key), 0) + value
    return totals


def _progress(
    question_ids: list[str],
    arms: list[Arm],
    *,
    controller_manifest_sha256: str | None,
) -> dict[str, Any]:
    if controller_manifest_sha256 is None:
        completed = {arm.name: 0 for arm in arms}
        fully_paired = 0
    else:
        completed = {
            arm.name: sum(
                is_terminal_attempt(
                    arm, qid, controller_manifest_sha256=controller_manifest_sha256
                )
                and _blocking_artifact_reason(
                    arm,
                    qid,
                    controller_manifest_sha256=controller_manifest_sha256,
                )
                is None
                for qid in question_ids
            )
            for arm in arms
        }
        fully_paired = sum(
            all(
                is_terminal_attempt(
                    arm, qid, controller_manifest_sha256=controller_manifest_sha256
                )
                and _blocking_artifact_reason(
                    arm,
                    qid,
                    controller_manifest_sha256=controller_manifest_sha256,
                )
                is None
                for arm in arms
            )
            for qid in question_ids
        )
    archived_by_arm = {
        arm.name: [
            receipt
            for qid in question_ids
            for receipt in _attempt_receipts(arm, qid)
        ]
        for arm in arms
    }
    accepted_by_arm: dict[str, list[dict[str, Any]]] = {}
    for arm in arms:
        receipts: list[dict[str, Any]] = []
        for qid in question_ids:
            completion = _completion_path(arm, qid)
            if not completion.exists():
                continue
            try:
                value = _read_json(completion)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("status") == "answered":
                receipts.append(value)
        accepted_by_arm[arm.name] = receipts
    return {
        "scheduled": len(question_ids),
        "fully_paired": fully_paired,
        "completed_by_arm": completed,
        "failed_attempts": {
            arm.name: len(archived_by_arm[arm.name]) for arm in arms
        },
        "attempts_by_arm": {
            arm.name: len(archived_by_arm[arm.name]) + len(accepted_by_arm[arm.name])
            for arm in arms
        },
        "archived_token_usage_by_arm": {
            arm.name: _sum_usage(archived_by_arm[arm.name]) for arm in arms
        },
        "accepted_token_usage_by_arm": {
            arm.name: _sum_usage(accepted_by_arm[arm.name]) for arm in arms
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument(
        "--input", type=Path, default=Path("final_dataset/full_test409.csv")
    )
    parser.add_argument("--a6a-packets", type=Path, required=True)
    parser.add_argument("--qt4v-packets", type=Path, required=True)
    parser.add_argument("--qt4t-packets", type=Path, required=True)
    parser.add_argument(
        "--a6a-out", type=Path, default=Path("runs/codex-qt4-a6a-micro42")
    )
    parser.add_argument(
        "--qt4v-out", type=Path, default=Path("runs/codex-qt4v-micro42")
    )
    parser.add_argument(
        "--qt4t-out", type=Path, default=Path("runs/codex-qt4t-micro42")
    )
    parser.add_argument(
        "--controller-manifest",
        type=Path,
        default=Path("runs/qt4-micro42-controller/manifest.json"),
    )
    parser.add_argument("--lock", type=Path, default=Path("runs/.run_qt4_micro42.lock"))
    parser.add_argument("--schema", type=Path, default=Path("schemas/codex_answer.schema.json"))
    parser.add_argument(
        "--harness",
        type=Path,
        default=Path(__file__).resolve().with_name("codex_harness.py"),
    )
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--model", default=REGISTERED_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high", "xhigh"],
        default=REGISTERED_REASONING_EFFORT,
    )
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="spend-control cap; an incomplete capped invocation exits 3",
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    validate_registered_execution(model=args.model, reasoning_effort=args.reasoning_effort)
    source_arms = [
        Arm("a6a", args.a6a_packets, args.a6a_out),
        Arm("qt4v", args.qt4v_packets, args.qt4v_out),
        Arm("qt4t", args.qt4t_packets, args.qt4t_out),
    ]
    validate_output_directories(source_arms)

    def prepare() -> tuple[list[str], dict[str, Any]]:
        question_ids, preflight, validated_source_hashes = validate_and_bind_sources(
            spec_path=args.spec,
            gate_report_path=args.gate_report,
            input_path=args.input,
            arms=source_arms,
            schema_path=args.schema,
            harness_path=args.harness,
        )
        identity = build_controller_identity(
            controller_manifest=args.controller_manifest,
            spec_path=args.spec,
            gate_report_path=args.gate_report,
            input_path=args.input,
            question_ids=question_ids,
            arms=source_arms,
            schema_path=args.schema,
            harness_path=args.harness,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
            codex_bin=args.codex_bin,
            preflight=preflight,
            validated_source_hashes=validated_source_hashes,
        )
        return question_ids, identity

    if args.status:
        question_ids, identity = prepare()
        if not args.controller_manifest.exists():
            print(
                json.dumps(
                    _progress(
                        question_ids,
                        source_arms,
                        controller_manifest_sha256=None,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        bundle = seal_controller_bundle(
            manifest_path=args.controller_manifest,
            identity=identity,
            arms=source_arms,
        )
        print(
            json.dumps(
                _progress(
                    question_ids,
                    bundle.arms,
                    controller_manifest_sha256=bundle.manifest_sha256,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if not args.live:
        raise SystemExit("model execution requires --live; use --status for progress")

    try:
        instance_lock = _acquire_live_instance_lock(args.lock)
    except AlreadyRunning as exc:
        print(f"ALREADY_RUNNING: {exc}")
        return LOCK_BUSY_EXIT

    with instance_lock:
        # No frozen source is read before the live singleton lock is held.
        # Validation hashes are then carried through snapshot copy and the
        # immutable manifest, closing the gate-to-launch TOCTOU window.
        question_ids, identity = prepare()
        # Seal only while holding the lock. A lock-losing invocation can never
        # write or replace the controller manifest or snapshots.
        bundle = seal_controller_bundle(
            manifest_path=args.controller_manifest,
            identity=identity,
            arms=source_arms,
        )
        print(
            json.dumps(
                _progress(
                    question_ids,
                    bundle.arms,
                    controller_manifest_sha256=bundle.manifest_sha256,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        attempts = 0
        for question_id, arm in interleaved_schedule(question_ids, bundle.arms):
            blocking_reason = _blocking_artifact_reason(
                arm,
                question_id,
                controller_manifest_sha256=bundle.manifest_sha256,
            )
            if blocking_reason is not None:
                print(
                    f"BLOCKED_ARTIFACT question={question_id} arm={arm.name} "
                    f"reason={blocking_reason}",
                    flush=True,
                )
                return 1
            if is_terminal_attempt(
                arm,
                question_id,
                controller_manifest_sha256=bundle.manifest_sha256,
            ):
                continue
            prior_attempts = len(_attempt_receipts(arm, question_id))
            if _retry_cap_reached(arm, question_id):
                print(
                    f"BLOCKED_RETRY_CAP question={question_id} arm={arm.name} "
                    f"attempts={prior_attempts}",
                    flush=True,
                )
                return 1

            while True:
                if args.max_attempts is not None and attempts >= args.max_attempts:
                    print("MAX_ATTEMPTS_REACHED_INCOMPLETE", flush=True)
                    return PARTIAL_RUN_EXIT
                attempt_number = len(_attempt_receipts(arm, question_id)) + 1
                if attempt_number > MAX_ATTEMPTS_PER_ITEM:
                    print(
                        f"BLOCKED_RETRY_CAP question={question_id} arm={arm.name} "
                        f"attempts={attempt_number - 1}",
                        flush=True,
                    )
                    return 1
                command = build_harness_command(
                    arm=arm,
                    question_id=question_id,
                    input_path=bundle.input_path,
                    schema_path=bundle.schema_path,
                    timeout=args.timeout,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    codex_bin=bundle.manifest["execution"]["codex_bin"],
                    harness_path=bundle.harness_path,
                )
                print(
                    f"RUN question={question_id} arm={arm.name} "
                    f"attempt={attempt_number}",
                    flush=True,
                )
                result = subprocess.run(command, check=False)
                attempts += 1
                receipt = _write_attempt_receipt(
                    arm=arm,
                    question_id=question_id,
                    controller_manifest_sha256=bundle.manifest_sha256,
                    returncode=result.returncode,
                    model=bundle.manifest["execution"]["model"],
                    reasoning_effort=bundle.manifest["execution"][
                        "reasoning_effort"
                    ],
                    attempt_number=attempt_number,
                    schema_path=bundle.schema_path,
                )
                if receipt["status"] == "answered":
                    break
                failure_status = _failed_attempt_status(
                    receipt, _question_dir(arm, question_id)
                )
                archived = _archive_failed_attempt(
                    arm=arm,
                    question_id=question_id,
                    receipt={**receipt, "status": failure_status},
                )
                if failure_status != "transient_failure":
                    print(
                        f"BLOCKED_{failure_status.upper()} question={question_id} "
                        f"arm={arm.name} attempt={attempt_number}",
                        flush=True,
                    )
                    return result.returncode or 1
                if attempt_number >= MAX_ATTEMPTS_PER_ITEM:
                    print(
                        f"RETRY_CAP_REACHED question={question_id} arm={arm.name} "
                        f"attempt_receipt={archived['attempt_receipt_path']}",
                        flush=True,
                    )
                    return result.returncode or 1
                print(
                    f"RETRY_TRANSIENT question={question_id} arm={arm.name} "
                    f"attempt_receipt={archived['attempt_receipt_path']}",
                    flush=True,
                )

        progress = _progress(
            question_ids,
            bundle.arms,
            controller_manifest_sha256=bundle.manifest_sha256,
        )
        print(json.dumps(progress, indent=2, sort_keys=True))
        return 0 if progress["fully_paired"] == len(question_ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
