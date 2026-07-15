#!/usr/bin/env python3
"""Seal and run the registered A11 V/T/E efficacy experiment.

``--seal`` is a zero-model operation.  It validates the independently pinned
dataset and answer-input materialization, freezes all 360 exact prompts, binds
the answer/panel runtime and analysis code, and writes an immutable controller
manifest.  ``--live`` refuses to create or amend that manifest; it only runs
the already sealed rotating schedule and records transport receipts.  ``--status``
reports receipt counts and token totals without opening answer content.
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
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


_BOOTSTRAPPED_ENV = "A11_IMMUTABLE_BOOTSTRAP"
_PRELOCK_FD_ENV = "QT4_PREIMPORT_LOCK_FD"
_PRELOCK_PATH_ENV = "QT4_PREIMPORT_LOCK_PATH"
_BOOTSTRAP_SNAPSHOTS = {
    "run_a11_experiment": "run_a11_experiment.py",
    "run_qt4_experiment": "run_qt4_experiment.py",
    "codex_harness": "codex_harness.py",
    "a11_answer_harness": "a11_answer_harness.py",
    "qt4_packet_gate": "qt4_packet_gate.py",
    "run_lock": "run_lock.py",
    "a11_grading": "a11_grading.py",
    "panel_grade": "panel_grade.py",
    "paired_stats": "paired_stats.py",
}


def _early_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _early_cli_path(flag: str, default: str) -> Path:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return Path(default)
    if index + 1 >= len(sys.argv):
        raise SystemExit(f"{flag} requires a path")
    return Path(sys.argv[index + 1])


def _early_controller() -> tuple[Path, dict[str, Any], str]:
    path = _early_cli_path(
        "--controller-manifest", "runs/a11-vte-controller/manifest.json"
    ).resolve()
    try:
        manifest_sha256 = _early_sha256(path)
        sidecar = path.with_suffix(".sha256").read_text(encoding="ascii")
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("A11 controller is not a valid immutable manifest") from exc
    if sidecar != manifest_sha256 + "\n":
        raise SystemExit("A11 controller manifest sidecar changed")
    if (
        not isinstance(manifest, dict)
        or manifest.get("kind") != "a11_interleaved_controller_manifest"
        or manifest.get("schema_version")
        not in {"a11-controller-v1", "a11-controller-v2"}
    ):
        raise SystemExit("A11 controller manifest contract changed")
    return path, manifest, manifest_sha256


def _verify_bootstrap(stage_dir: Path, *, controller_sha256: str) -> Path:
    manifest_path = stage_dir / "bootstrap-manifest.json"
    try:
        receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("invalid A11 immutable bootstrap") from exc
    expected_files = receipt.get("files") if isinstance(receipt, dict) else None
    if (
        receipt.get("controller_manifest_sha256") != controller_sha256
        or not isinstance(expected_files, dict)
        or set(expected_files) != set(_BOOTSTRAP_SNAPSHOTS.values())
        or stat.S_IMODE(stage_dir.stat().st_mode) & 0o222
    ):
        raise SystemExit("A11 immutable bootstrap identity changed")
    for filename, expected_sha in expected_files.items():
        path = stage_dir / filename
        if (
            path.is_symlink()
            or not path.is_file()
            or _early_sha256(path) != expected_sha
            or stat.S_IMODE(path.stat().st_mode) & 0o222
        ):
            raise SystemExit(f"A11 immutable bootstrap file changed: {filename}")
    if stat.S_IMODE(manifest_path.stat().st_mode) & 0o222:
        raise SystemExit("A11 immutable bootstrap manifest is writable")
    return stage_dir / "run_a11_experiment.py"


def _stage_bootstrap(
    controller_path: Path, manifest: dict[str, Any], controller_sha256: str
) -> Path:
    stage_dir = controller_path.parent / "bootstrap"
    if stage_dir.exists():
        return _verify_bootstrap(stage_dir, controller_sha256=controller_sha256)
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, dict):
        raise SystemExit("A11 controller has no immutable snapshots")
    temporary = stage_dir.with_name(f".bootstrap.{os.getpid()}.tmp")
    temporary.mkdir(parents=True, exist_ok=False)
    renamed = False
    files: dict[str, str] = {}
    try:
        for snapshot_name, filename in _BOOTSTRAP_SNAPSHOTS.items():
            entry = snapshots.get(snapshot_name)
            if not isinstance(entry, dict):
                raise SystemExit(f"A11 bootstrap snapshot is missing: {snapshot_name}")
            source = Path(str(entry.get("snapshot_path") or ""))
            if (
                source.is_symlink()
                or not source.is_file()
                or stat.S_IMODE(source.stat().st_mode) & 0o222
            ):
                raise SystemExit(f"A11 bootstrap source changed: {snapshot_name}")
            payload = source.read_bytes()
            payload_sha256 = hashlib.sha256(payload).hexdigest()
            if (
                payload_sha256 != entry.get("sha256")
                or len(payload) != entry.get("bytes")
            ):
                raise SystemExit(f"A11 bootstrap source changed: {snapshot_name}")
            destination = temporary / filename
            destination.write_bytes(payload)
            files[filename] = payload_sha256
        receipt = {
            "kind": "a11_immutable_preimport_bootstrap",
            "schema_version": "a11-bootstrap-v1",
            "controller_manifest_sha256": controller_sha256,
            "files": files,
        }
        (temporary / "bootstrap-manifest.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for path in temporary.iterdir():
            path.chmod(0o444)
        temporary.rename(stage_dir)
        renamed = True
        stage_dir.chmod(0o555)
    except BaseException:
        cleanup = stage_dir if renamed else temporary
        if cleanup.exists():
            cleanup.chmod(0o755)
            for path in cleanup.iterdir():
                path.chmod(0o644)
            shutil.rmtree(cleanup, ignore_errors=True)
        raise
    return _verify_bootstrap(stage_dir, controller_sha256=controller_sha256)


def _exec_immutable_bootstrap(*, lock_required: bool) -> None:
    controller_path, manifest, controller_sha256 = _early_controller()
    lock_fd: int | None = None
    environment = os.environ.copy()
    if lock_required:
        sealed_lock = Path(str(manifest.get("integrity", {}).get("singleton_lock") or ""))
        requested_lock = _early_cli_path("--lock", "runs/.a11-vte.lock").resolve()
        if requested_lock != sealed_lock:
            raise SystemExit("A11 lock path differs from the controller seal")
        sealed_lock.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(sealed_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = os.read(lock_fd, 256).decode("utf-8", errors="replace").strip()
            os.close(lock_fd)
            print(f"ALREADY_RUNNING: {sealed_lock} is held by {owner or 'unknown PID'}")
            raise SystemExit(75)
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"pid={os.getpid()}\n".encode("utf-8"))
        os.fsync(lock_fd)
        os.set_inheritable(lock_fd, True)
        environment[_PRELOCK_FD_ENV] = str(lock_fd)
        environment[_PRELOCK_PATH_ENV] = str(sealed_lock)
    runner = _stage_bootstrap(controller_path, manifest, controller_sha256)
    environment[_BOOTSTRAPPED_ENV] = "1"
    try:
        os.execve(sys.executable, [sys.executable, str(runner), *sys.argv[1:]], environment)
    except BaseException:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        raise


def _bootstrap_before_project_imports() -> None:
    if __name__ != "__main__" or "--seal" in sys.argv:
        return
    if os.environ.get(_BOOTSTRAPPED_ENV) == "1":
        controller_path, _, controller_sha256 = _early_controller()
        _verify_bootstrap(
            controller_path.parent / "bootstrap",
            controller_sha256=controller_sha256,
        )
        return
    recognized = {"--live", "--status", "--prepare-grading", "--finalize"} & set(sys.argv)
    if recognized:
        _exec_immutable_bootstrap(
            lock_required=bool({"--live", "--prepare-grading", "--finalize"} & recognized)
        )


_bootstrap_before_project_imports()

import a11_answer_harness  # noqa: E402 - immutable bootstrap runs first
import codex_harness  # noqa: E402 - immutable bootstrap runs first
import run_qt4_experiment as transport  # noqa: E402


CONTROLLER_VERSION = "a11-controller-v2"
LEGACY_CONTROLLER_VERSION = "a11-controller-v1"
SUPPORTED_CONTROLLER_VERSIONS = frozenset(
    {LEGACY_CONTROLLER_VERSION, CONTROLLER_VERSION}
)
EXPERIMENT_PROFILE = "a11-vte-efficacy-120-v1"
REGISTERED_MODEL = "gpt-5.6-sol"
REGISTERED_REASONING_EFFORT = "high"
REGISTERED_TIMEOUT_SECONDS = 600
REGISTERED_MAX_ATTEMPTS = 3
REGISTERED_PANEL_VOTES = 3
REGISTERED_PANEL_BATCH_SIZE = 20
REGISTERED_PANEL_TIMEOUT_SECONDS = 600
REGISTERED_ANALYSIS_ORDER = (
    "hard_failures",
    "primary_e_minus_t_all_efficacy",
    "secondary_t_minus_v_answerable",
    "mechanism_outcomes",
    "answer_behavior_outcomes",
    "economics",
    "family_depth_breakdowns",
)
ARMS = ("v", "t", "e")
PARTIAL_RUN_EXIT = transport.PARTIAL_RUN_EXIT


@dataclass(frozen=True)
class A11ControllerBundle:
    manifest_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    question_ids: tuple[str, ...]
    arms: tuple[transport.Arm, ...]
    input_path: Path
    schema_path: Path
    harness_path: Path
    prompt_by_host: dict[tuple[str, str], dict[str, Any]]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _loads(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {label}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    value = _loads(path.read_bytes(), label=str(path))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact is not an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        value = _loads(line, label=f"{path}:{line_number}")
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object: {path}:{line_number}")
        rows.append(value)
    return rows


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_pretty_bytes(value))
    os.replace(temporary, path)


def _ensure_nonnegative_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def strict_event_usage(event_path: Path) -> dict[str, Any]:
    """Require exactly one completed turn and a reconciled integer token receipt."""

    completed: list[dict[str, Any]] = []
    for line_number, line in enumerate(event_path.read_bytes().splitlines(), start=1):
        event = _loads(line, label=f"{event_path}:{line_number}")
        if not isinstance(event, dict):
            raise ValueError("Codex event is not an object")
        if event.get("type") == "turn.completed":
            completed.append(event)
    if len(completed) != 1:
        raise ValueError("accepted attempt must contain exactly one turn.completed")
    raw = completed[0].get("usage")
    if not isinstance(raw, dict):
        raise ValueError("turn.completed has no usage object")
    usage = {str(key): _ensure_nonnegative_int(value, field=f"usage.{key}") for key, value in raw.items()}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    reported_total_tokens = usage.get("total_tokens")
    if input_tokens is None or output_tokens is None:
        raise ValueError("accepted token receipt lacks input/output tokens")
    total_tokens = input_tokens + output_tokens
    if reported_total_tokens is not None and reported_total_tokens != total_tokens:
        raise ValueError("accepted total tokens do not reconcile")
    return {
        "raw": usage,
        "input_tokens": input_tokens,
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": output_tokens,
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "total_tokens": total_tokens,
        "total_tokens_source": (
            "reported" if reported_total_tokens is not None else "derived_input_plus_output"
        ),
        "cached_input_tokens_complete": "cached_input_tokens" in usage,
        "reasoning_output_tokens_complete": "reasoning_output_tokens" in usage,
    }


def _codex_identity(codex_bin: str) -> dict[str, Any]:
    return codex_harness.codex_runtime_identity(codex_bin)


def _codex_runtime(identity: Mapping[str, Any]) -> dict[str, Any]:
    return codex_harness.normalized_codex_runtime(dict(identity))


def verify_codex_identity(expected: Mapping[str, Any]) -> None:
    codex_harness.verify_codex_runtime_identity(dict(expected))


def _safe_snapshot_name(name: str, source: Path) -> str:
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in name):
        raise ValueError(f"unsafe snapshot name: {name}")
    suffix = "".join(source.suffixes[-2:]) or ".bin"
    return f"{name}{suffix}"


def _snapshot_file(
    *,
    artifact_dir: Path,
    name: str,
    source: Path,
    logical_source: str,
) -> dict[str, Any]:
    source = source.resolve()
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"snapshot source is unsafe: {source}")
    payload = source.read_bytes()
    destination = artifact_dir / _safe_snapshot_name(name, source)
    _write_exclusive(destination, payload)
    return {
        "logical_source": logical_source,
        "snapshot_path": str(destination.resolve()),
        "sha256": _sha256_bytes(payload),
        "bytes": len(payload),
    }


def _generated_snapshot(
    *, artifact_dir: Path, name: str, suffix: str, payload: bytes
) -> dict[str, Any]:
    destination = artifact_dir / f"{name}{suffix}"
    _write_exclusive(destination, payload)
    return {
        "logical_source": f"generated:{name}",
        "snapshot_path": str(destination.resolve()),
        "sha256": _sha256_bytes(payload),
        "bytes": len(payload),
    }


def _assert_distinct_outputs(paths: Mapping[str, Path]) -> dict[str, str]:
    resolved = {name: path.resolve() for name, path in paths.items()}
    values = list(resolved.items())
    for index, (left_name, left) in enumerate(values):
        for right_name, right in values[index + 1 :]:
            if left == right:
                raise ValueError(f"output directories overlap: {left_name}/{right_name}")
            try:
                left.relative_to(right)
            except ValueError:
                pass
            else:
                raise ValueError(f"nested output directories: {left_name}/{right_name}")
            try:
                right.relative_to(left)
            except ValueError:
                pass
            else:
                raise ValueError(f"nested output directories: {left_name}/{right_name}")
    return {name: str(path) for name, path in resolved.items()}


def _output_artifacts_exist(paths: Iterable[Path]) -> bool:
    return any(path.exists() and any(child.is_file() for child in path.rglob("*")) for path in paths)


def _tree_receipts(root: Path, *, label: str) -> dict[str, dict[str, Any]]:
    """Return an exact regular-file inventory and reject links/special files."""

    root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} source tree is unsafe: {root}")
    receipts: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"{label} source contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"{label} source contains a special file: {relative}")
        receipts[relative] = {
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
    if not receipts:
        raise ValueError(f"{label} source tree is empty")
    return receipts


def _file_receipt(path: Path, *, label: str) -> dict[str, Any]:
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} source is unsafe: {path}")
    return {"sha256": _sha256_file(path), "bytes": path.stat().st_size}


def _source_binding(
    *,
    dataset_dir: Path,
    answer_inputs_dir: Path,
    compilation_timing: Path,
    schema_path: Path,
    code_sources: Mapping[str, Path],
    preregistration: Path,
) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    files.update(
        {
            f"dataset/{name}": receipt
            for name, receipt in _tree_receipts(dataset_dir, label="dataset").items()
        }
    )
    files.update(
        {
            f"answer_inputs/{name}": receipt
            for name, receipt in _tree_receipts(
                answer_inputs_dir, label="answer inputs"
            ).items()
        }
    )
    files["compilation_timing"] = _file_receipt(
        compilation_timing, label="compilation timing"
    )
    files["schema"] = _file_receipt(schema_path, label="answer schema")
    for name, path in sorted(code_sources.items()):
        files[f"code/{name}"] = _file_receipt(path, label=f"code {name}")
    files["preregistration"] = _file_receipt(
        preregistration, label="preregistration"
    )
    return {
        "schema_version": "a11-source-binding-v1",
        "files": files,
        "files_sha256": _sha256_bytes(_canonical_bytes(files)),
    }


def _assert_snapshot_matches(
    snapshot: Mapping[str, Any], source_binding: Mapping[str, Any], logical_name: str
) -> None:
    expected = source_binding["files"].get(logical_name)
    if not isinstance(expected, dict) or {
        "sha256": snapshot.get("sha256"),
        "bytes": snapshot.get("bytes"),
    } != expected:
        raise ValueError(f"snapshot differs from bound source: {logical_name}")


def _assert_answer_inputs_replay(
    *, dataset_dir: Path, answer_inputs_dir: Path
) -> None:
    """Independently rebuild inputs from the pinned dataset and compare bytes."""

    import a11_answer_inputs

    with tempfile.TemporaryDirectory(prefix="a11-answer-input-replay-") as directory:
        replay_dir = Path(directory) / "answer-inputs"
        a11_answer_inputs.materialize_answer_inputs(
            dataset_dir,
            replay_dir,
            expected_dataset_manifest_sha256=(
                a11_answer_inputs.REGISTERED_DATASET_MANIFEST_SHA256
            ),
        )
        if _tree_receipts(replay_dir, label="replayed answer inputs") != _tree_receipts(
            answer_inputs_dir, label="supplied answer inputs"
        ):
            raise ValueError(
                "supplied answer inputs differ from independent pinned-dataset replay"
            )


def build_prompt_records(
    *, answer_inputs_dir: Path
) -> tuple[dict[str, bytes], list[dict[str, Any]], tuple[str, ...]]:
    """Materialize exact prompt records and their host-only index."""

    input_path = answer_inputs_dir / "answer_input.csv"
    rows = a11_answer_harness.load_input_rows(input_path)
    by_id = {row["question_id"]: row for row in rows}
    question_ids = tuple(row["question_id"] for row in rows)
    record_bytes: dict[str, bytes] = {}
    prompt_index: list[dict[str, Any]] = []
    common_envelope_by_question: dict[str, bytes] = {}
    for arm in ARMS:
        payload_rows = _read_jsonl(answer_inputs_dir / f"{arm}_packets.jsonl")
        if [row.get("question_id") for row in payload_rows] != list(question_ids):
            raise ValueError(f"{arm} payload order differs from blind answer input")
        records: list[dict[str, Any]] = []
        for payload_row in payload_rows:
            question_id = payload_row["question_id"]
            row = by_id[question_id]
            payload_text = payload_row.get("model_payload_json")
            if not isinstance(payload_text, str):
                raise ValueError(f"{arm} payload is not a string")
            payload_bytes = payload_text.encode("utf-8")
            if (
                _sha256_bytes(payload_bytes) != payload_row.get("model_payload_sha256")
                or len(payload_bytes) != payload_row.get("model_payload_utf8_bytes")
            ):
                raise ValueError(f"{arm} payload binding changed for {question_id}")
            prompt = a11_answer_harness.render_prompt_bytes(row, payload_text)
            record = {
                "schema_version": a11_answer_harness.PROMPT_RECORD_VERSION,
                "question_id": question_id,
                "model_payload_json": payload_text,
                "model_payload_sha256": payload_row["model_payload_sha256"],
                "model_payload_utf8_bytes": payload_row["model_payload_utf8_bytes"],
                "prompt_text": prompt.decode("utf-8"),
                "prompt_sha256": _sha256_bytes(prompt),
            }
            if a11_answer_harness.build_verified_prompt(row, record) != prompt:
                raise RuntimeError("verified A11 prompt bytes changed")
            sentinel_envelope = a11_answer_harness.render_prompt_bytes(row, "__A11_PAYLOAD__")
            previous = common_envelope_by_question.setdefault(question_id, sentinel_envelope)
            if previous != sentinel_envelope:
                raise ValueError(f"prompt envelope differs across arms for {question_id}")
            records.append(record)
            prompt_index.append(
                {
                    "question_id": question_id,
                    "arm": arm,
                    "model_payload_sha256": payload_row["model_payload_sha256"],
                    "model_payload_utf8_bytes": payload_row["model_payload_utf8_bytes"],
                    "prompt_sha256": record["prompt_sha256"],
                    "prompt_utf8_bytes": len(prompt),
                }
            )
        record_bytes[arm] = b"".join(_canonical_bytes(record) + b"\n" for record in records)
    return record_bytes, prompt_index, question_ids


def _validate_timing(timing_path: Path, *, answer_inputs_manifest_sha256: str) -> dict[str, Any]:
    timing = _read_json(timing_path)
    if (
        timing.get("schema_version") != "a11-compilation-timing-v1"
        or timing.get("model_calls") != 0
        or timing.get("answer_inputs_manifest_sha256") != answer_inputs_manifest_sha256
        or timing.get("efficacy_question_count") != 120
        or not isinstance(timing.get("rows"), list)
        or len(timing["rows"]) != 120
    ):
        raise ValueError("A11 compilation timing receipt changed")
    for row in timing["rows"]:
        if not isinstance(row, dict):
            raise ValueError("A11 compilation timing row is malformed")
        for key, value in row.items():
            if key.endswith("_ns"):
                _ensure_nonnegative_int(value, field=f"timing.{key}")
    return timing


def _required_code_sources(repo: Path) -> dict[str, Path]:
    names = (
        "a11_answer_harness.py",
        "a11_answer_inputs.py",
        "a11_grading.py",
        "run_a11_experiment.py",
        "run_a11_panel.py",
        "run_qt4_experiment.py",
        "qt4_packet_gate.py",
        "run_lock.py",
        "panel_grade.py",
        "paired_stats.py",
        "codex_harness.py",
        "compile_evidence.py",
        "a11_dataset_builder.py",
        "a11_evidence_core.py",
        "a11_event_group_benchmark.py",
        "a11_governed_retrieval.py",
        "a11_packet_adapter.py",
        "a6_packet_builder.py",
    )
    return {Path(name).stem: repo / name for name in names}


def _registered_codex_analysis(
    *,
    codex_bin: str,
    schema_path: Path,
    code_sources: Mapping[str, Path],
) -> tuple[dict[str, Any], dict[str, Any]]:
    codex = _codex_identity(codex_bin)
    codex_runtime = _codex_runtime(codex)

    from a11_grading import registered_analysis_config

    analysis = registered_analysis_config(
        codex_bin=codex_runtime["path"],
        codex_version=codex_runtime["version"],
        codex_binary_sha256=codex_runtime["sha256"],
        answer_schema_sha256=_sha256_file(schema_path),
        panel_source_sha256=_sha256_file(code_sources["run_a11_panel"]),
        grading_source_sha256=_sha256_file(code_sources["a11_grading"]),
    )
    return codex, analysis


def seal_controller(
    *,
    dataset_dir: Path,
    answer_inputs_dir: Path,
    compilation_timing: Path,
    controller_manifest: Path,
    schema_path: Path,
    codex_bin: str,
    arm_outputs: Mapping[str, Path],
    grading_output: Path,
    panel_output: Path,
    result_output: Path,
    lock_path: Path,
) -> A11ControllerBundle:
    """Create the immutable zero-model A11 controller manifest."""

    import a11_answer_inputs

    controller_manifest = controller_manifest.resolve()
    if controller_manifest.exists() or controller_manifest.with_suffix(".sha256").exists():
        raise FileExistsError("A11 controller manifest already exists and is immutable")
    artifact_dir = controller_manifest.parent / "artifacts"
    if artifact_dir.exists():
        raise FileExistsError("A11 controller artifacts already exist without a manifest")
    all_locations = _assert_distinct_outputs(
        {
            **{f"arm_{arm}": arm_outputs[arm] for arm in ARMS},
            "grading": grading_output,
            "panel": panel_output,
            "result": result_output,
            "controller": controller_manifest.parent,
            "dataset_source": dataset_dir,
            "answer_inputs_source": answer_inputs_dir,
        }
    )
    outputs = {
        name: all_locations[name]
        for name in (*[f"arm_{arm}" for arm in ARMS], "grading", "panel", "result")
    }
    if _output_artifacts_exist([Path(path) for path in outputs.values()]):
        raise ValueError("answer/grading/panel outputs must be empty at first seal")

    dataset_dir = dataset_dir.resolve()
    answer_inputs_dir = answer_inputs_dir.resolve()
    compilation_timing = compilation_timing.resolve()
    schema_path = schema_path.resolve()
    repo = Path(__file__).resolve().parent
    code_sources = _required_code_sources(repo)
    missing_code = [name for name, path in code_sources.items() if not path.is_file()]
    if missing_code:
        raise ValueError("A11 finalizer/controller code is incomplete: " + ",".join(missing_code))
    prereg = repo / "docs/prereg/A11_EVENT_GROUP.md"
    source_binding = _source_binding(
        dataset_dir=dataset_dir,
        answer_inputs_dir=answer_inputs_dir,
        compilation_timing=compilation_timing,
        schema_path=schema_path,
        code_sources=code_sources,
        preregistration=prereg,
    )

    dataset_manifest = a11_answer_inputs.verify_dataset(
        dataset_dir,
        expected_manifest_sha256=a11_answer_inputs.REGISTERED_DATASET_MANIFEST_SHA256,
    )
    answer_manifest = a11_answer_inputs.verify_answer_inputs(
        answer_inputs_dir,
        expected_dataset_manifest_sha256=a11_answer_inputs.REGISTERED_DATASET_MANIFEST_SHA256,
    )
    _assert_answer_inputs_replay(
        dataset_dir=dataset_dir,
        answer_inputs_dir=answer_inputs_dir,
    )
    answer_manifest_sha256 = _sha256_file(answer_inputs_dir / "manifest.json")
    timing = _validate_timing(
        compilation_timing,
        answer_inputs_manifest_sha256=answer_manifest_sha256,
    )
    schema = _read_json(schema_path)
    if schema.get("additionalProperties") is not False or set(schema.get("required", [])) != {
        "answer",
        "source_resource_ids",
        "evidence_summary",
        "insufficiency_reason",
    }:
        raise ValueError("A11 answer schema is not strict")
    codex, analysis = _registered_codex_analysis(
        codex_bin=codex_bin,
        schema_path=schema_path,
        code_sources=code_sources,
    )
    if tuple(analysis.get("analysis_order", [])) != REGISTERED_ANALYSIS_ORDER:
        raise ValueError("registered A11 analysis order changed")

    record_bytes, prompt_index, question_ids = build_prompt_records(
        answer_inputs_dir=answer_inputs_dir.resolve()
    )
    if question_ids != tuple(answer_manifest["question_ids"]):
        raise ValueError("prompt question order differs from answer-input manifest")
    schedule = [
        {"question_id": question_id, "arm": arm.name}
        for question_id, arm in transport.interleaved_schedule(
            question_ids,
            [transport.Arm(arm, Path("unused"), arm_outputs[arm]) for arm in ARMS],
        )
    ]
    if len(schedule) != 360:
        raise ValueError("A11 rotating schedule is not exactly 360 items")

    controller_manifest.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(exist_ok=False)
    snapshots: dict[str, dict[str, Any]] = {}
    try:
        snapshots["input"] = _snapshot_file(
            artifact_dir=artifact_dir,
            name="answer_input",
            source=answer_inputs_dir / "answer_input.csv",
            logical_source="answer_inputs/answer_input.csv",
        )
        snapshots["schema"] = _snapshot_file(
            artifact_dir=artifact_dir,
            name="codex_answer_schema",
            source=schema_path,
            logical_source="schemas/codex_answer.schema.json",
        )
        snapshots["compilation_timing"] = _snapshot_file(
            artifact_dir=artifact_dir,
            name="compilation_timing",
            source=compilation_timing,
            logical_source="compilation_timing.json",
        )
        for arm in ARMS:
            snapshots[f"packet_{arm}"] = _generated_snapshot(
                artifact_dir=artifact_dir,
                name=f"packet_{arm}",
                suffix=".jsonl",
                payload=record_bytes[arm],
            )
        prompt_index_bytes = b"".join(
            _canonical_bytes(row) + b"\n" for row in prompt_index
        )
        snapshots["prompt_index"] = _generated_snapshot(
            artifact_dir=artifact_dir,
            name="prompt_index",
            suffix=".jsonl",
            payload=prompt_index_bytes,
        )
        dataset_files = {
            "dataset_manifest": dataset_dir / "manifest.json",
            "dataset_manifest_sidecar": dataset_dir / "manifest.sha256",
            **{
                f"dataset_{name.replace('/', '_').replace('.', '_')}": dataset_dir / name
                for name in dataset_manifest["artifacts"]
            },
        }
        answer_files = {
            "answer_inputs_manifest": answer_inputs_dir / "manifest.json",
            "answer_inputs_manifest_sidecar": answer_inputs_dir / "manifest.sha256",
            **{
                f"answer_inputs_{name.replace('/', '_').replace('.', '_')}": answer_inputs_dir / name
                for name in answer_manifest["artifacts"]
            },
        }
        for name, source in {**dataset_files, **answer_files}.items():
            snapshots[name] = _snapshot_file(
                artifact_dir=artifact_dir,
                name=name,
                source=source,
                logical_source=str(source.relative_to(source.parents[1])),
            )
        for name, source in code_sources.items():
            snapshots[name] = _snapshot_file(
                artifact_dir=artifact_dir,
                name=name,
                source=source,
                logical_source=source.name,
            )
        snapshots["preregistration"] = _snapshot_file(
            artifact_dir=artifact_dir,
            name="a11_preregistration",
            source=prereg,
            logical_source="docs/prereg/A11_EVENT_GROUP.md",
        )
        direct_snapshot_bindings = {
            "input": "answer_inputs/answer_input.csv",
            "schema": "schema",
            "compilation_timing": "compilation_timing",
            "dataset_manifest": "dataset/manifest.json",
            "dataset_manifest_sidecar": "dataset/manifest.sha256",
            "answer_inputs_manifest": "answer_inputs/manifest.json",
            "answer_inputs_manifest_sidecar": "answer_inputs/manifest.sha256",
            "preregistration": "preregistration",
            **{
                f"dataset_{name.replace('/', '_').replace('.', '_')}": f"dataset/{name}"
                for name in dataset_manifest["artifacts"]
            },
            **{
                f"answer_inputs_{name.replace('/', '_').replace('.', '_')}": (
                    f"answer_inputs/{name}"
                )
                for name in answer_manifest["artifacts"]
            },
            **{name: f"code/{name}" for name in code_sources},
        }
        for snapshot_name, logical_name in direct_snapshot_bindings.items():
            _assert_snapshot_matches(
                snapshots[snapshot_name], source_binding, logical_name
            )
        if _source_binding(
            dataset_dir=dataset_dir,
            answer_inputs_dir=answer_inputs_dir,
            compilation_timing=compilation_timing,
            schema_path=schema_path,
            code_sources=code_sources,
            preregistration=prereg,
        ) != source_binding:
            raise ValueError("A11 source bytes changed during controller seal")
        artifact_dir.chmod(0o555)
    except BaseException:
        if artifact_dir.exists():
            artifact_dir.chmod(0o755)
        raise

    prompt_by_host = {
        (row["question_id"], row["arm"]): row for row in prompt_index
    }
    receipt_by_qid = {
        row["question_id"]: row
        for row in _read_jsonl(
            Path(
                snapshots["answer_inputs_governed_receipts_jsonl"][
                    "snapshot_path"
                ]
            )
        )
    }
    payload_by_arm = {
        arm: {
            row["question_id"]: {
                "sha256": row["model_payload_sha256"],
                "bytes": row["model_payload_utf8_bytes"],
            }
            for row in _read_jsonl(
                Path(
                    snapshots[f"answer_inputs_{arm}_packets_jsonl"][
                        "snapshot_path"
                    ]
                )
            )
        }
        for arm in ARMS
    }
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "a11_interleaved_controller_manifest",
        "schema_version": CONTROLLER_VERSION,
        "experiment_profile": EXPERIMENT_PROFILE,
        "transport_protocol": transport.REGISTERED_TRANSPORT_PROTOCOL,
        "protocol": {
            "answer_prompt": a11_answer_harness.PROMPT_PROTOCOL_VERSION,
            "answer_prompt_record": a11_answer_harness.PROMPT_RECORD_VERSION,
            "answer_inputs": a11_answer_inputs.ANSWER_INPUTS_VERSION,
            "dataset": dataset_manifest["schema_version"],
            "evidence_recipe": "a11-four-family-depth-aware-v1",
            "schedule": "rotating-interleaved-v1",
            "attempt_transport": transport.ATTEMPT_SCHEMA_VERSION,
        },
        "dataset": {
            "manifest_sha256": a11_answer_inputs.REGISTERED_DATASET_MANIFEST_SHA256,
            "profile_sha256": dataset_manifest["profile_sha256"],
            "source_epoch": dataset_manifest["source_epoch"],
            "artifacts": dataset_manifest["artifacts"],
            "compiler_dependencies": dataset_manifest["compiler_dependencies"],
        },
        "answer_inputs": {
            "manifest_sha256": answer_manifest_sha256,
            "question_ids_sha256": answer_manifest["question_ids_sha256"],
            "v_producer_manifest_sha256": answer_manifest[
                "v_producer_manifest_sha256"
            ],
            "compilation_timing_sha256": source_binding["files"][
                "compilation_timing"
            ]["sha256"],
        },
        "population": {
            "split": "efficacy",
            "selection": "question_order.json order filtered by questions.jsonl split=efficacy",
            "question_ids": list(question_ids),
            "questions": len(question_ids),
            "patient_clusters": answer_manifest["patient_clusters"],
            "answerable": answer_manifest["population"]["answerable"],
            "unanswerable": answer_manifest["population"]["unanswerable"],
            "development_questions_scheduled": 0,
        },
        "arms": {
            arm: {
                "treatment": {
                    "v": "exact promoted query-aware V payload",
                    "t": "flat governed traversal payload",
                    "e": "typed event-group payload from the same governed traversal",
                }[arm],
                "payloads": payload_by_arm[arm],
                **(
                    {}
                    if arm == "v"
                    else {
                        "shared_retrieval_by_question": {
                            qid: receipt_by_qid[qid][
                                "shared_retrieval_source_sha256"
                            ]
                            for qid in question_ids
                        }
                    }
                ),
            }
            for arm in ARMS
        },
        "schedule": {
            "method": "rotating-interleaved-v1",
            "items": schedule,
            "sha256": _sha256_bytes(_canonical_bytes(schedule)),
            "accepted_answers_required": 360,
        },
        "prompt": {
            "metadata_allowlist": ["question_id", "question", "assumption"],
            "exact_payload_insertion": True,
            "governance_identifiers_visible": False,
            "synthetic_fhir_resource_identifiers_visible": True,
            "synthetic_patient_fhir_reference_visible": True,
            "arm_label_visible": False,
            "items": prompt_index,
            "index_sha256": _sha256_bytes(prompt_index_bytes),
        },
        "execution": {
            "model": REGISTERED_MODEL,
            "reasoning_effort": REGISTERED_REASONING_EFFORT,
            "timeout_seconds": REGISTERED_TIMEOUT_SECONDS,
            "codex": codex,
            "python_path": str(Path(sys.executable).resolve()),
            "python_version": sys.version.split()[0],
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "sandbox": "read-only",
            "approval": "never",
            "empty_nonrepository_cwd": True,
            "stdout_jsonl_stderr_separated": True,
            "no_tool_events": True,
        },
        "retry_policy": {
            "maximum_attempts_per_item": REGISTERED_MAX_ATTEMPTS,
            "maximum_attempts_persist_across_restart": True,
            "only_exact_answerless_provider_failure_is_retryable_after_contamination": True,
            "every_attempt_charged": True,
        },
        "grading": analysis,
        "analysis": {
            "order": list(REGISTERED_ANALYSIS_ORDER),
            "primary": "E minus T on all 120 efficacy questions",
            "secondary": "T minus V on 96 answerable questions",
            "promotion_rule": "positive E-T and patient-cluster bootstrap ci_low > 0 and zero critical safety failures",
            "mcnemar": "two-sided exact; report-only",
        },
        "outputs": outputs,
        "snapshots": snapshots,
        "integrity": {
            "model_calls_during_seal": 0,
            "immutable_snapshots": True,
            "source_binding": source_binding,
            "answer_content_forbidden_mid_run": True,
            "source_manifest_must_precede_outputs": True,
            "singleton_lock": str(lock_path.resolve()),
        },
    }
    _write_exclusive(controller_manifest, _pretty_bytes(manifest))
    manifest_sha256 = _sha256_file(controller_manifest)
    sidecar = controller_manifest.with_suffix(".sha256")
    _write_exclusive(sidecar, (manifest_sha256 + "\n").encode("ascii"))
    return load_controller(controller_manifest)


def load_controller(manifest_path: Path) -> A11ControllerBundle:
    manifest_path = manifest_path.resolve()
    manifest_sha256 = _sha256_file(manifest_path)
    sidecar = manifest_path.with_suffix(".sha256")
    if sidecar.read_text(encoding="ascii") != manifest_sha256 + "\n":
        raise ValueError("A11 controller manifest sidecar changed")
    manifest = _read_json(manifest_path)
    if (
        manifest.get("kind") != "a11_interleaved_controller_manifest"
        or manifest.get("schema_version") not in SUPPORTED_CONTROLLER_VERSIONS
        or manifest.get("experiment_profile") != EXPERIMENT_PROFILE
    ):
        raise ValueError("A11 controller manifest contract changed")
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, dict):
        raise ValueError("A11 controller snapshot inventory is missing")
    for name, entry in snapshots.items():
        if not isinstance(entry, dict):
            raise ValueError(f"A11 snapshot metadata is malformed: {name}")
        path = Path(str(entry.get("snapshot_path") or ""))
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != entry.get("sha256")
            or path.stat().st_size != entry.get("bytes")
            or stat.S_IMODE(path.stat().st_mode) & 0o222
        ):
            raise ValueError(f"A11 immutable snapshot changed: {name}")
    artifact_parents = {Path(entry["snapshot_path"]).parent for entry in snapshots.values()}
    if len(artifact_parents) != 1:
        raise ValueError("A11 snapshots are not in one immutable directory")
    artifact_dir = next(iter(artifact_parents))
    if stat.S_IMODE(artifact_dir.stat().st_mode) & 0o222:
        raise ValueError("A11 snapshot directory is writable")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {
        "arm_v",
        "arm_t",
        "arm_e",
        "grading",
        "panel",
        "result",
    }:
        raise ValueError("A11 output inventory changed")
    _assert_distinct_outputs(
        {
            **{name: Path(path) for name, path in outputs.items()},
            "controller": manifest_path.parent,
        }
    )
    question_ids = tuple(manifest.get("population", {}).get("question_ids", []))
    if len(question_ids) != 120 or len(set(question_ids)) != 120:
        raise ValueError("A11 controller question coverage changed")
    prompt_index = _read_jsonl(Path(snapshots["prompt_index"]["snapshot_path"]))
    if (
        len(prompt_index) != 360
        or _sha256_file(Path(snapshots["prompt_index"]["snapshot_path"]))
        != snapshots["prompt_index"]["sha256"]
    ):
        raise ValueError("A11 prompt index changed")
    prompt_by_host = {
        (row["question_id"], row["arm"]): row for row in prompt_index
    }
    if len(prompt_by_host) != 360:
        raise ValueError("A11 prompt host coverage is not unique")
    arms = tuple(
        transport.Arm(
            arm,
            Path(snapshots[f"packet_{arm}"]["snapshot_path"]),
            Path(manifest["outputs"][f"arm_{arm}"]),
        )
        for arm in ARMS
    )
    return A11ControllerBundle(
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        question_ids=question_ids,
        arms=arms,
        input_path=Path(snapshots["input"]["snapshot_path"]),
        schema_path=Path(snapshots["schema"]["snapshot_path"]),
        harness_path=Path(snapshots["a11_answer_harness"]["snapshot_path"]),
        prompt_by_host=prompt_by_host,
    )


def _verify_loaded_code(bundle: A11ControllerBundle) -> None:
    current = {
        "run_a11_experiment": Path(__file__).resolve(),
        "run_qt4_experiment": Path(transport.__file__).resolve(),
        "a11_answer_harness": Path(a11_answer_harness.__file__).resolve(),
        "codex_harness": Path(codex_harness.__file__).resolve(),
    }
    for name, path in current.items():
        expected = bundle.manifest["snapshots"][name]["sha256"]
        if _sha256_file(path) != expected:
            raise ValueError(f"loaded controller code differs from seal: {name}")


def _completion_path(arm: transport.Arm, question_id: str) -> Path:
    return arm.out_dir / "questions" / codex_harness.slugify(question_id) / "completion.json"


def _a11_receipt_fields(
    *, bundle: A11ControllerBundle, arm: transport.Arm, question_id: str
) -> dict[str, Any]:
    prompt = bundle.prompt_by_host[(question_id, arm.name)]
    codex_runtime = _codex_runtime(bundle.manifest["execution"]["codex"])
    return {
        "a11_controller_version": bundle.manifest["schema_version"],
        "codex_binary_sha256": codex_runtime["sha256"],
        "codex_version": bundle.manifest["execution"]["codex"]["version"],
        "payload_sha256": prompt["model_payload_sha256"],
        "payload_utf8_bytes": prompt["model_payload_utf8_bytes"],
        "expected_prompt_sha256": prompt["prompt_sha256"],
        "expected_prompt_utf8_bytes": prompt["prompt_utf8_bytes"],
    }


def _build_a11_harness_command(
    *, bundle: A11ControllerBundle, arm: transport.Arm, question_id: str
) -> list[str]:
    codex_runtime = _codex_runtime(bundle.manifest["execution"]["codex"])
    return transport.build_harness_command(
        arm=arm,
        question_id=question_id,
        input_path=bundle.input_path,
        schema_path=bundle.schema_path,
        timeout=REGISTERED_TIMEOUT_SECONDS,
        model=REGISTERED_MODEL,
        reasoning_effort=REGISTERED_REASONING_EFFORT,
        codex_bin=codex_runtime["path"],
        python_bin=bundle.manifest["execution"]["python_path"],
        harness_path=bundle.harness_path,
    )


def _execute_a11_harness_command(
    *,
    bundle: A11ControllerBundle,
    command: list[str],
    run_process: Any | None = None,
) -> tuple[Any, bool]:
    expected_codex = bundle.manifest["execution"]["codex"]
    verify_codex_identity(expected_codex)
    if run_process is None:
        run_process = subprocess.run
    result = run_process(command, check=False)
    try:
        verify_codex_identity(expected_codex)
    except (OSError, ValueError):
        return result, False
    return result, True


def _augment_attempt_receipt(
    receipt: dict[str, Any],
    *,
    bundle: A11ControllerBundle,
    arm: transport.Arm,
    question_id: str,
) -> dict[str, Any]:
    augmented = {
        **receipt,
        **_a11_receipt_fields(bundle=bundle, arm=arm, question_id=question_id),
    }
    event_path = arm.out_dir / "questions" / codex_harness.slugify(question_id) / "events.jsonl"
    if receipt.get("status") == "answered":
        augmented["a11_usage"] = strict_event_usage(event_path)
        if augmented.get("prompt_sha256") != augmented["expected_prompt_sha256"]:
            raise ValueError("accepted prompt differs from sealed A11 prompt")
        _write_json_atomic(_completion_path(arm, question_id), augmented)
    else:
        try:
            augmented["a11_usage"] = strict_event_usage(event_path)
        except (OSError, ValueError):
            augmented["a11_usage"] = None
    return augmented


def is_a11_retryable_provider_failure(receipt: Mapping[str, Any]) -> bool:
    """Allow retries only for the registered exact answerless provider shape."""

    return (
        receipt.get("status") == "invalid"
        and receipt.get("harness_exit_code") not in (None, 0)
        and receipt.get("answer_sha256") is None
        and codex_harness.is_retryable_incomplete_packet_audit(
            receipt.get("event_integrity")
        )
    )


def a11_blocking_artifact_reason(
    bundle: A11ControllerBundle, arm: transport.Arm, question_id: str
) -> str | None:
    reason = transport._blocking_artifact_reason(
        arm,
        question_id,
        controller_manifest_sha256=bundle.manifest_sha256,
    )
    if reason == "archived_contaminated":
        receipts = transport._attempt_receipts(arm, question_id)
        if (
            receipts
            and receipts[-1].get("status") == "contaminated"
            and receipts[-1].get("a11_terminal_failure") == "model_failure"
        ):
            return "archived_model_failure"
    return reason


def is_a11_terminal(
    bundle: A11ControllerBundle, arm: transport.Arm, question_id: str
) -> bool:
    if not transport.is_terminal_attempt(
        arm,
        question_id,
        controller_manifest_sha256=bundle.manifest_sha256,
    ):
        return False
    try:
        receipt = _read_json(_completion_path(arm, question_id))
    except (OSError, ValueError):
        return False
    expected = _a11_receipt_fields(bundle=bundle, arm=arm, question_id=question_id)
    if any(receipt.get(key) != value for key, value in expected.items()):
        return False
    try:
        return receipt.get("a11_usage") == strict_event_usage(
            arm.out_dir
            / "questions"
            / codex_harness.slugify(question_id)
            / "events.jsonl"
        )
    except (OSError, ValueError):
        return False


def a11_progress(bundle: A11ControllerBundle) -> dict[str, Any]:
    completed = {
        arm.name: sum(is_a11_terminal(bundle, arm, qid) for qid in bundle.question_ids)
        for arm in bundle.arms
    }
    fully_paired = sum(
        all(is_a11_terminal(bundle, arm, qid) for arm in bundle.arms)
        for qid in bundle.question_ids
    )
    failed = {
        arm.name: sum(len(transport._attempt_receipts(arm, qid)) for qid in bundle.question_ids)
        for arm in bundle.arms
    }
    accepted_usage: dict[str, dict[str, int]] = {}
    all_usage: dict[str, dict[str, int]] = {}
    usage_completeness: dict[str, dict[str, Any]] = {}
    retry_yield: dict[str, dict[str, Any]] = {}
    for arm in bundle.arms:
        accepted_totals: dict[str, int] = {}
        all_totals: dict[str, int] = {}
        accepted_attempts = 0
        all_attempts = 0
        accepted_attempts_with_usage = 0
        all_attempts_with_usage = 0
        retry_attempts = 0
        questions_recovered_after_retry = 0
        for qid in bundle.question_ids:
            receipts = transport._attempt_receipts(arm, qid)
            completion = _completion_path(arm, qid)
            if completion.exists():
                try:
                    completion_receipt = _read_json(completion)
                    if completion_receipt.get("status") == "answered":
                        prior_attempts = int(completion_receipt.get("attempt_number", 1)) - 1
                        retry_attempts += max(0, prior_attempts)
                        if prior_attempts > 0:
                            questions_recovered_after_retry += 1
                    receipts = [*receipts, completion_receipt]
                except (OSError, ValueError):
                    pass
            for receipt in receipts:
                all_attempts += 1
                if receipt.get("status") == "answered":
                    accepted_attempts += 1
                usage = receipt.get("a11_usage")
                if not isinstance(usage, dict):
                    continue
                all_attempts_with_usage += 1
                if receipt.get("status") == "answered":
                    accepted_attempts_with_usage += 1
                for metric in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "total_tokens",
                ):
                    value = usage.get(metric)
                    if isinstance(value, int) and not isinstance(value, bool):
                        all_totals[metric] = all_totals.get(metric, 0) + value
                        if receipt.get("status") == "answered":
                            accepted_totals[metric] = accepted_totals.get(metric, 0) + value
        accepted_usage[arm.name] = accepted_totals
        all_usage[arm.name] = all_totals
        usage_completeness[arm.name] = {
            "accepted_attempts": accepted_attempts,
            "accepted_attempts_with_usage": accepted_attempts_with_usage,
            "accepted_attempts_missing_usage": (
                accepted_attempts - accepted_attempts_with_usage
            ),
            "all_attempts": all_attempts,
            "all_attempts_with_usage": all_attempts_with_usage,
            "all_attempts_missing_usage": all_attempts - all_attempts_with_usage,
            "accepted_complete": accepted_attempts == accepted_attempts_with_usage,
            "all_attempt_complete": all_attempts == all_attempts_with_usage,
        }
        retry_yield[arm.name] = {
            "retry_attempts": retry_attempts,
            "questions_recovered_after_retry": questions_recovered_after_retry,
            "recovered_questions_per_retry_attempt": (
                questions_recovered_after_retry / retry_attempts
                if retry_attempts
                else None
            ),
        }
    return {
        "scheduled_questions": len(bundle.question_ids),
        "scheduled_answers": len(bundle.question_ids) * len(bundle.arms),
        "fully_paired": fully_paired,
        "completed_by_arm": completed,
        "failed_attempts_by_arm": failed,
        "accepted_token_usage_by_arm": accepted_usage,
        "all_attempt_token_usage_by_arm": all_usage,
        "token_receipt_completeness_by_arm": usage_completeness,
        "retry_yield_by_arm": retry_yield,
        "all_attempt_token_economics_reconciled": all(
            row["all_attempt_complete"] for row in usage_completeness.values()
        ),
    }


def build_completion_coverage(bundle: A11ControllerBundle) -> dict[str, Any]:
    """Build a content-free 360-receipt proof after every arm is complete."""

    blocking = [
        (question_id, arm.name, reason)
        for question_id in bundle.question_ids
        for arm in bundle.arms
        if (
            reason := a11_blocking_artifact_reason(bundle, arm, question_id)
        ) is not None
    ]
    if blocking:
        question_id, arm_name, reason = blocking[0]
        raise ValueError(
            "A11 grading found invalid attempt provenance: "
            f"question={question_id} arm={arm_name} reason={reason}"
        )
    if any(
        not is_a11_terminal(bundle, arm, question_id)
        for question_id in bundle.question_ids
        for arm in bundle.arms
    ):
        raise ValueError("A11 grading requires exactly 360 clean completions")
    import a11_grading

    receipts: list[dict[str, Any]] = []
    for question_id in bundle.question_ids:
        for arm in bundle.arms:
            completion_path = _completion_path(arm, question_id)
            completion = _read_json(completion_path)
            receipts.append(
                {
                    "kind": a11_grading.COMPLETION_KIND,
                    "schema_version": a11_grading.COMPLETION_SCHEMA_VERSION,
                    "controller_manifest_sha256": bundle.manifest_sha256,
                    "arm": arm.name,
                    "question_id": question_id,
                    "status": "answered",
                    "attempt_number": completion["attempt_number"],
                    "answer_sha256": completion["answer_sha256"],
                    "event_log_sha256": completion["event_log_sha256"],
                    "prompt_sha256": completion["prompt_sha256"],
                    "stderr_log_sha256": completion["stderr_log_sha256"],
                    "packet_sha256": completion["packet_sha256"],
                    "schema_sha256": completion["schema_sha256"],
                    "payload_sha256": completion["payload_sha256"],
                    "codex_binary_sha256": completion["codex_binary_sha256"],
                    "transport_completion_sha256": _sha256_file(completion_path),
                    "a11_usage": completion["a11_usage"],
                }
            )
    return {
        "schema_version": a11_grading.COMPLETION_COVERAGE_VERSION,
        "controller_manifest_sha256": bundle.manifest_sha256,
        "question_ids": list(bundle.question_ids),
        "arms": list(ARMS),
        "receipts": receipts,
    }


def validate_coverage_receipt(
    bundle: A11ControllerBundle, receipt: Mapping[str, Any]
) -> bool:
    """Rehash every answer transport artifact behind one public coverage row."""

    arm_name = receipt.get("arm")
    question_id = receipt.get("question_id")
    arm = next((candidate for candidate in bundle.arms if candidate.name == arm_name), None)
    if arm is None or question_id not in bundle.question_ids:
        return False
    if not is_a11_terminal(bundle, arm, str(question_id)):
        return False
    completion_path = _completion_path(arm, str(question_id))
    completion = _read_json(completion_path)
    mappings = {
        "attempt_number": "attempt_number",
        "answer_sha256": "answer_sha256",
        "event_log_sha256": "event_log_sha256",
        "prompt_sha256": "prompt_sha256",
        "stderr_log_sha256": "stderr_log_sha256",
        "packet_sha256": "packet_sha256",
        "schema_sha256": "schema_sha256",
        "payload_sha256": "payload_sha256",
        "codex_binary_sha256": "codex_binary_sha256",
        "a11_usage": "a11_usage",
    }
    if any(receipt.get(public) != completion.get(private) for public, private in mappings.items()):
        return False
    return receipt.get("transport_completion_sha256") == _sha256_file(completion_path)


def _snapshot_path(bundle: A11ControllerBundle, name: str) -> Path:
    return Path(bundle.manifest["snapshots"][name]["snapshot_path"])


def require_reconciled_answer_economics(progress: Mapping[str, Any]) -> None:
    completeness = progress.get("token_receipt_completeness_by_arm")
    if (
        progress.get("all_attempt_token_economics_reconciled") is not True
        or not isinstance(completeness, dict)
        or set(completeness) != set(ARMS)
        or any(
            not isinstance(row, dict)
            or row.get("accepted_complete") is not True
            or row.get("all_attempt_complete") is not True
            for row in completeness.values()
        )
    ):
        raise ValueError(
            "A11 accepted/all-attempt token economics are not fully reconciled"
        )


def prepare_grading(bundle: A11ControllerBundle) -> dict[str, Any]:
    """Read answers and gold only after exact clean completion coverage is proven."""

    import a11_grading

    coverage = build_completion_coverage(bundle)
    progress = a11_progress(bundle)
    require_reconciled_answer_economics(progress)
    gold_path = _snapshot_path(bundle, "dataset_gold_jsonl")
    questions_path = _snapshot_path(bundle, "dataset_questions_jsonl")

    def load_gold() -> list[dict[str, Any]]:
        return [
            row
            for row in _read_jsonl(gold_path)
            if row.get("question_id") in bundle.question_ids
        ]

    gold = a11_grading.load_gold_after_completion(
        coverage,
        gold_loader=load_gold,
        receipt_validator=lambda receipt: validate_coverage_receipt(bundle, receipt),
    )
    questions = {
        row["question_id"]: row
        for row in _read_jsonl(questions_path)
        if row.get("question_id") in bundle.question_ids
    }
    if set(questions) != set(bundle.question_ids):
        raise ValueError("sealed efficacy question coverage changed before grading")

    deterministic: dict[str, dict[str, int]] = {arm: {} for arm in ARMS}
    panel_queue: list[dict[str, Any]] = []
    for question_id in bundle.question_ids:
        for arm in bundle.arms:
            answer_path = (
                arm.out_dir
                / "questions"
                / codex_harness.slugify(question_id)
                / "answer.json"
            )
            answer = _read_json(answer_path)
            verdict, panel_item = a11_grading.deterministic_partition(
                question=questions[question_id],
                gold=gold[question_id],
                answer=answer,
            )
            if verdict is not None:
                deterministic[arm.name][question_id] = verdict
            elif panel_item is not None:
                panel_queue.append(
                    {
                        "arm": arm.name,
                        "question_id": question_id,
                        **panel_item,
                    }
                )
            else:
                raise RuntimeError("A11 grading partition produced no label source")

    grading_dir = Path(bundle.manifest["outputs"]["grading"])
    if grading_dir.exists() and any(grading_dir.iterdir()):
        raise ValueError("A11 grading output already exists and is immutable")
    grading_dir.mkdir(parents=True, exist_ok=True)
    coverage_bytes = _pretty_bytes(coverage)
    deterministic_bytes = _pretty_bytes(deterministic)
    queue_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in panel_queue)
    _write_exclusive(grading_dir / "completion_coverage.json", coverage_bytes)
    _write_exclusive(grading_dir / "deterministic_labels.json", deterministic_bytes)
    _write_exclusive(grading_dir / "panel_queue.jsonl", queue_bytes)
    manifest = {
        "schema_version": "a11-grading-preparation-v1",
        "controller_manifest_sha256": bundle.manifest_sha256,
        "model_calls": 0,
        "completed_answers": 360,
        "deterministic_labels": sum(len(rows) for rows in deterministic.values()),
        "panel_items": len(panel_queue),
        "artifacts": {
            "completion_coverage.json": {
                "sha256": _sha256_bytes(coverage_bytes),
                "bytes": len(coverage_bytes),
            },
            "deterministic_labels.json": {
                "sha256": _sha256_bytes(deterministic_bytes),
                "bytes": len(deterministic_bytes),
            },
            "panel_queue.jsonl": {
                "sha256": _sha256_bytes(queue_bytes),
                "bytes": len(queue_bytes),
            },
        },
        "panel_config": bundle.manifest["grading"]["panel"],
        "answer_economics": {
            "accepted_token_usage_by_arm": progress[
                "accepted_token_usage_by_arm"
            ],
            "all_attempt_token_usage_by_arm": progress[
                "all_attempt_token_usage_by_arm"
            ],
            "token_receipt_completeness_by_arm": progress[
                "token_receipt_completeness_by_arm"
            ],
            "retry_yield_by_arm": progress["retry_yield_by_arm"],
            "all_attempt_token_economics_reconciled": True,
        },
        "gold_sha256": _sha256_file(gold_path),
        "questions_sha256": _sha256_file(questions_path),
        "all_checks_passed": True,
    }
    _write_exclusive(grading_dir / "manifest.json", _pretty_bytes(manifest))
    grading_dir.chmod(0o555)
    return manifest


def _verified_grading_artifacts(
    bundle: A11ControllerBundle,
) -> tuple[dict[str, Any], dict[str, dict[str, int]], list[dict[str, Any]]]:
    grading_dir = Path(bundle.manifest["outputs"]["grading"])
    manifest = _read_json(grading_dir / "manifest.json")
    if (
        manifest.get("schema_version") != "a11-grading-preparation-v1"
        or manifest.get("controller_manifest_sha256") != bundle.manifest_sha256
        or manifest.get("all_checks_passed") is not True
        or manifest.get("completed_answers") != 360
    ):
        raise ValueError("A11 grading preparation is incomplete or cross-controller")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "completion_coverage.json",
        "deterministic_labels.json",
        "panel_queue.jsonl",
    }:
        raise ValueError("A11 grading artifact inventory changed")
    for name, receipt in artifacts.items():
        path = grading_dir / name
        if (
            not isinstance(receipt, dict)
            or not path.is_file()
            or _file_receipt(path, label=f"grading {name}") != receipt
        ):
            raise ValueError(f"A11 grading artifact changed: {name}")
    deterministic = _read_json(grading_dir / "deterministic_labels.json")
    queue = _read_jsonl(grading_dir / "panel_queue.jsonl")
    return manifest, deterministic, queue


def _verified_panel_verdicts(
    bundle: A11ControllerBundle,
    *,
    grading_manifest: Mapping[str, Any],
    panel_queue: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, Any]]:
    import panel_grade

    panel_dir = Path(bundle.manifest["outputs"]["panel"])
    if not panel_queue:
        if panel_dir.exists() and any(panel_dir.iterdir()):
            raise ValueError("A11 panel artifacts exist for an empty registered queue")
        return {}, panel_grade.panel_token_summary({"usage_receipts": []})
    queue_path = Path(bundle.manifest["outputs"]["grading"]) / "panel_queue.jsonl"
    command = [
        bundle.manifest["execution"]["python_path"],
        str(_snapshot_path(bundle, "run_a11_panel")),
        "--audit",
        "--queue",
        str(queue_path),
        "--controller-manifest",
        str(bundle.manifest_path),
        "--expected-controller-sha256",
        bundle.manifest_sha256,
        "--out-dir",
        str(panel_dir),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr:
        raise ValueError("sealed A11 panel replay audit failed")
    try:
        audit = _loads(completed.stdout.encode("utf-8"), label="panel replay audit")
    except ValueError as exc:
        raise ValueError("sealed A11 panel replay audit is malformed") from exc
    verdicts = audit.get("verdicts") if isinstance(audit, dict) else None
    expected_hosts = {f"{row['arm']}|{row['question_id']}" for row in panel_queue}
    if (
        not isinstance(verdicts, dict)
        or set(verdicts) != expected_hosts
        or any(type(value) is not int or value not in (0, 1) for value in verdicts.values())
        or audit.get("schema_version") != "a11-panel-replay-audit-v1"
        or audit.get("controller_manifest_sha256") != bundle.manifest_sha256
        or audit.get("queue_sha256")
        != grading_manifest["artifacts"]["panel_queue.jsonl"]["sha256"]
        or audit.get("verdicts_sha256") != _sha256_bytes(_canonical_bytes(verdicts))
        or audit.get("items") != len(expected_hosts)
        or audit.get("votes_per_item") != REGISTERED_PANEL_VOTES
        or audit.get("all_checks_passed") is not True
    ):
        raise ValueError("A11 panel verdict coverage or binding changed")
    receipt_hashes = audit.get("attempt_receipt_sha256")
    if not isinstance(receipt_hashes, dict) or not receipt_hashes:
        raise ValueError("A11 panel has no immutable attempt receipts")
    token_summary = audit.get("panel_token_usage")
    if not isinstance(token_summary, dict):
        raise ValueError("A11 panel token rollup changed")
    return {str(key): int(value) for key, value in verdicts.items()}, token_summary


def _normalized_ref(value: str) -> str:
    parts = value.split("/")
    if len(parts) >= 2 and parts[0] and parts[1]:
        return f"{parts[0]}/{parts[1]}"
    return value


def _visible_resource_refs(payload: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    resources = payload.get("resources")
    if isinstance(resources, list):
        for resource in resources:
            if isinstance(resource, dict) and isinstance(resource.get("resourceType"), str) and isinstance(resource.get("id"), str):
                refs.add(f"{resource['resourceType']}/{resource['id']}")
    groups = payload.get("event_groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            root = group.get("root")
            if isinstance(root, dict) and isinstance(root.get("reference"), str):
                refs.add(_normalized_ref(root["reference"]))
            members = group.get("members")
            if isinstance(members, list):
                for member in members:
                    resource = member.get("resource") if isinstance(member, dict) else None
                    if isinstance(resource, dict) and isinstance(resource.get("reference"), str):
                        refs.add(_normalized_ref(resource["reference"]))
    return refs


def _sealed_payloads(bundle: A11ControllerBundle) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for arm in ARMS:
        rows = _read_jsonl(
            _snapshot_path(bundle, f"answer_inputs_{arm}_packets_jsonl")
        )
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload_text = row.get("model_payload_json")
            if not isinstance(payload_text, str):
                raise ValueError("sealed A11 model payload is not text")
            payload = _loads(payload_text.encode("utf-8"), label=f"{arm} payload")
            if not isinstance(payload, dict):
                raise ValueError("sealed A11 model payload is not an object")
            by_id[str(row["question_id"])] = payload
        if set(by_id) != set(bundle.question_ids):
            raise ValueError(f"sealed {arm} payload coverage changed")
        result[arm] = by_id
    return result


def _mechanism_outcomes(
    bundle: A11ControllerBundle,
    *,
    gold: Mapping[str, Mapping[str, Any]],
    payloads: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    zero_model_audit = _read_json(
        _snapshot_path(bundle, "dataset_zero_model_audit_json")
    )
    case_audits = {
        row["question_id"]: row
        for row in zero_model_audit.get("case_audits", [])
        if isinstance(row, dict) and row.get("question_id") in bundle.question_ids
    }
    if set(case_audits) != set(bundle.question_ids):
        raise ValueError("sealed source path-replay audit coverage changed")
    replay_fields = (
        "paths_replay",
        "exact_shortest_path",
        "normalized_utc_rank_matches",
        "answerability_matches",
        "failure_mechanism_matches",
    )
    replay_counts = {
        field: sum(case_audits[qid].get(field) is True for qid in bundle.question_ids)
        for field in replay_fields
    }
    if any(count != len(bundle.question_ids) for count in replay_counts.values()):
        raise ValueError("registered A11 source traversal/path replay hard-failed")

    answerable = [qid for qid in bundle.question_ids if gold[qid]["answerable"] is True]
    terminal_recall: dict[str, Any] = {}
    for arm in ARMS:
        present = sum(
            _normalized_ref(str(gold[qid]["terminal_resource_ref"]))
            in _visible_resource_refs(payloads[arm][qid])
            for qid in answerable
        )
        terminal_recall[arm] = {
            "n": len(answerable),
            "terminal_present": present,
            "recall": present / len(answerable),
        }

    selected_root_matches = 0
    selected_root_wrong = 0
    selected_root_missing = 0
    selected_root_eligible = 0
    expected_bound_exhaustion_without_selection = 0
    e_path_valid = 0
    t_path_valid = 0
    answerability_matches = 0
    for qid in bundle.question_ids:
        expected = gold[qid]
        e_payload = payloads["e"][qid]
        groups = e_payload.get("event_groups")
        selected = [
            group
            for group in groups
            if isinstance(groups, list)
            and isinstance(group, dict)
            and isinstance(group.get("temporal_rank"), dict)
            and group["temporal_rank"].get("selected_for_question") is True
        ] if isinstance(groups, list) else []
        selection_expected = expected.get("failure_mode") != "bound_exhaustion"
        if selection_expected:
            selected_root_eligible += 1
        else:
            expected_bound_exhaustion_without_selection += 1
        if len(selected) == 1 and selection_expected:
            root = selected[0].get("root")
            if isinstance(root, dict) and _normalized_ref(str(root.get("reference"))) == _normalized_ref(str(expected["selected_root_ref"])):
                selected_root_matches += 1
            else:
                selected_root_wrong += 1
            edges = selected[0].get("typed_edges")
            relations = [edge.get("relation") for edge in edges] if isinstance(edges, list) else []
            if relations == expected["path_signature"]:
                e_path_valid += 1
        elif selection_expected:
            selected_root_missing += 1
        receipt = e_payload.get("answerability_receipt")
        expected_state = "sufficient" if expected["answerable"] else "insufficient"
        if isinstance(receipt, dict) and receipt.get("state") == expected_state:
            answerability_matches += 1

        citations = payloads["t"][qid].get("path_citations")
        expected_target = (
            _normalized_ref(str(expected["terminal_resource_ref"]))
            if expected["answerable"]
            else None
        )
        for citation in citations if isinstance(citations, list) else []:
            steps = citation.get("steps") if isinstance(citation, dict) else None
            if (
                isinstance(steps, list)
                and len(steps) == int(expected["depth"])
                and _normalized_ref(str(steps[0].get("source")))
                == _normalized_ref(str(expected["selected_root_ref"]))
                and (
                    _normalized_ref(str(steps[-1].get("target")))
                    if steps[-1].get("target") is not None
                    else None
                )
                == expected_target
            ):
                t_path_valid += 1
                break

    packet_bytes = {
        arm: sum(
            int(item["bytes"])
            for item in bundle.manifest["arms"][arm]["payloads"].values()
        )
        for arm in ARMS
    }
    t_shared = bundle.manifest["arms"]["t"]["shared_retrieval_by_question"]
    e_shared = bundle.manifest["arms"]["e"]["shared_retrieval_by_question"]
    if t_shared != e_shared or set(t_shared) != set(bundle.question_ids):
        raise ValueError("registered T/E shared retrieval receipts diverged")
    return {
        "terminal_evidence_recall_by_arm": terminal_recall,
        "selected_root_accuracy_e": {
            "eligible": selected_root_eligible,
            "correct": selected_root_matches,
            "wrong_temporal_root": selected_root_wrong,
            "missing_selection": selected_root_missing,
            "accuracy_when_selection_required": (
                selected_root_matches / selected_root_eligible
            ),
            "expected_bound_exhaustion_without_selection": (
                expected_bound_exhaustion_without_selection
            ),
        },
        "date_order_errors_e": selected_root_wrong,
        "path_validity": {
            "source_replay_n": len(bundle.question_ids),
            "source_replay_passed": replay_counts["paths_replay"],
            "exact_shortest_path_passed": replay_counts["exact_shortest_path"],
            "normalized_utc_rank_passed": replay_counts[
                "normalized_utc_rank_matches"
            ],
            "validator": "sealed_dataset_source_json_pointer_replay",
        },
        "model_packet_path_structure": {
            "t": {"n": len(bundle.question_ids), "valid": t_path_valid},
            "e": {
                "eligible": selected_root_eligible,
                "valid": e_path_valid,
                "expected_bound_exhaustion_without_selection": (
                    expected_bound_exhaustion_without_selection
                ),
            },
        },
        "answerability_calibration_e": {
            "n": len(bundle.question_ids),
            "correct": answerability_matches,
            "accuracy": answerability_matches / len(bundle.question_ids),
        },
        "model_payload_utf8_bytes_by_arm": packet_bytes,
        "t_e_shared_retrieval": {
            "identical": True,
            "questions": len(t_shared),
            "mapping_sha256": _sha256_bytes(_canonical_bytes(t_shared)),
        },
    }


def _answer_behavior_outcomes(
    bundle: A11ControllerBundle,
    *,
    gold: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, int]],
    payloads: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm in bundle.arms:
        counts = {
            "abstentions": 0,
            "correct_unanswerable_abstentions": 0,
            "false_abstentions_answerable": 0,
            "substantive_unanswerable_answers": 0,
            "answers_with_invalid_source_ids": 0,
            "answers_without_source_ids": 0,
            "unsupported_answers": 0,
            "correct": sum(labels[arm.name].values()),
        }
        for qid in bundle.question_ids:
            answer = _read_json(
                arm.out_dir
                / "questions"
                / codex_harness.slugify(qid)
                / "answer.json"
            )
            abstained = isinstance(answer.get("insufficiency_reason"), str) and bool(
                answer["insufficiency_reason"].strip()
            )
            if abstained:
                counts["abstentions"] += 1
                if gold[qid]["answerable"]:
                    counts["false_abstentions_answerable"] += 1
                else:
                    counts["correct_unanswerable_abstentions"] += 1
            elif not gold[qid]["answerable"]:
                counts["substantive_unanswerable_answers"] += 1
            source_ids = answer.get("source_resource_ids")
            if not isinstance(source_ids, list):
                raise ValueError("accepted A11 answer source IDs changed schema")
            if not source_ids and not abstained:
                counts["answers_without_source_ids"] += 1
            visible = _visible_resource_refs(payloads[arm.name][qid])
            if any(
                not isinstance(source_id, str)
                or _normalized_ref(source_id) not in visible
                for source_id in source_ids
            ):
                counts["answers_with_invalid_source_ids"] += 1
            unsupported = (
                (not abstained and not gold[qid]["answerable"])
                or (not abstained and not source_ids)
                or any(
                    not isinstance(source_id, str)
                    or _normalized_ref(source_id) not in visible
                    for source_id in source_ids
                )
            )
            if unsupported:
                counts["unsupported_answers"] += 1
        counts["unsupported_answer_definition"] = (
            "substantive answer on an unanswerable item, or substantive answer "
            "with no source ids, or any cited source id absent from the arm packet"
        )
        result[arm.name] = counts
    return result


def _compilation_economics(bundle: A11ControllerBundle) -> dict[str, Any]:
    timing = _read_json(_snapshot_path(bundle, "compilation_timing"))
    rows = timing["rows"]
    keys = sorted(
        {
            key
            for row in rows
            for key, value in row.items()
            if key.endswith("_ns") and isinstance(value, int) and not isinstance(value, bool)
        }
    )
    stages: dict[str, dict[str, int | float]] = {}
    for key in keys:
        values = sorted(int(row.get(key, 0)) for row in rows)
        midpoint = len(values) // 2
        median: int | float = (
            values[midpoint]
            if len(values) % 2
            else (values[midpoint - 1] + values[midpoint]) / 2
        )
        p95_index = max(0, ((95 * len(values) + 99) // 100) - 1)
        stages[key] = {
            "sum": sum(values),
            "min": values[0],
            "median": median,
            "p95_nearest_rank": values[p95_index],
            "max": values[-1],
        }
    return {
        "clock": timing["clock"],
        "v_full_144_production_ns": timing["v_full_144_production_ns"],
        "efficacy_questions": len(rows),
        "stages_ns": stages,
    }


def _panel_economics_reconciled(summary: Mapping[str, Any]) -> bool:
    accepted = summary.get("accepted")
    all_attempts = summary.get("all_attempts")
    if (
        isinstance(accepted, dict)
        and isinstance(all_attempts, dict)
        and accepted.get("calls") == 0
        and all_attempts.get("calls") == 0
    ):
        return True
    for scope in ("accepted", "all_attempts"):
        rollup = summary.get(scope)
        if not isinstance(rollup, dict) or not isinstance(rollup.get("completeness"), dict):
            return False
        completeness = rollup["completeness"]
        if not all(completeness.get(metric) is True for metric in ("input_tokens", "output_tokens", "total_tokens")):
            return False
        tokens = rollup.get("tokens")
        if not isinstance(tokens, dict) or tokens.get("total_tokens") != tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0):
            return False
    return True


def finalize_result(bundle: A11ControllerBundle) -> dict[str, Any]:
    """Assemble the registered result only after grading and panel completion."""

    import a11_grading

    coverage = build_completion_coverage(bundle)
    progress = a11_progress(bundle)
    require_reconciled_answer_economics(progress)
    grading_manifest, deterministic, panel_queue = _verified_grading_artifacts(bundle)
    panel_verdicts, panel_tokens = _verified_panel_verdicts(
        bundle,
        grading_manifest=grading_manifest,
        panel_queue=panel_queue,
    )
    gold_rows = [
        row
        for row in _read_jsonl(_snapshot_path(bundle, "dataset_gold_jsonl"))
        if row.get("question_id") in bundle.question_ids
    ]
    gold = a11_grading.load_gold_after_completion(
        coverage,
        gold_loader=lambda: gold_rows,
        receipt_validator=lambda receipt: validate_coverage_receipt(bundle, receipt),
    )
    questions = {
        row["question_id"]: row
        for row in _read_jsonl(_snapshot_path(bundle, "dataset_questions_jsonl"))
        if row.get("question_id") in bundle.question_ids
    }
    labels = a11_grading.final_labels(
        question_ids=bundle.question_ids,
        deterministic=deterministic,
        panel_queue=panel_queue,
        panel_verdicts=panel_verdicts,
    )
    payloads = _sealed_payloads(bundle)
    critical_failures: list[dict[str, Any]] = []
    if not _panel_economics_reconciled(panel_tokens):
        critical_failures.append(
            {
                "code": "unreconciled_panel_token_economics",
                "scope": "accepted_and_all_attempt_panel_calls",
            }
        )
    economics = {
        "answers": grading_manifest["answer_economics"],
        "panel": panel_tokens,
        "panel_reconciled": not critical_failures,
        "compilation": _compilation_economics(bundle),
    }
    panel_verdict_manifest_path = (
        Path(bundle.manifest["outputs"]["panel"])
        / "panel_verdicts.manifest.json"
    )
    input_hashes = {
        "controller_manifest_sha256": bundle.manifest_sha256,
        "dataset_manifest_sha256": bundle.manifest["dataset"]["manifest_sha256"],
        "answer_inputs_manifest_sha256": bundle.manifest["answer_inputs"][
            "manifest_sha256"
        ],
        "grading_manifest_sha256": _sha256_file(
            Path(bundle.manifest["outputs"]["grading"]) / "manifest.json"
        ),
        "panel_verdict_manifest_sha256": (
            _sha256_file(panel_verdict_manifest_path)
            if panel_verdict_manifest_path.is_file()
            else None
        ),
        "panel_disposition": (
            "completed_replayed_panel" if panel_queue else "panel_not_required_empty_queue"
        ),
    }
    result = a11_grading.assemble_result(
        question_ids=bundle.question_ids,
        questions=questions,
        gold=gold,
        labels=labels,
        critical_safety_failures=critical_failures,
        mechanism_outcomes=_mechanism_outcomes(
            bundle, gold=gold, payloads=payloads
        ),
        answer_behavior_outcomes=_answer_behavior_outcomes(
            bundle,
            gold=gold,
            labels=labels,
            payloads=payloads,
        ),
        economics=economics,
        input_hashes=input_hashes,
    )
    result_dir = Path(bundle.manifest["outputs"]["result"])
    if result_dir.exists() and any(result_dir.iterdir()):
        raise ValueError("A11 final result output already exists and is immutable")
    result_dir.mkdir(parents=True, exist_ok=True)
    result_bytes = a11_grading.canonical_json_bytes(result)
    _write_exclusive(result_dir / "result.json", result_bytes)
    final_manifest = {
        "schema_version": "a11-final-result-manifest-v1",
        "controller_manifest_sha256": bundle.manifest_sha256,
        "model_calls_during_finalization": 0,
        "result_sha256": _sha256_bytes(result_bytes),
        "result_bytes": len(result_bytes),
        "status": result["status"],
        "promotion": result["promotion_assessment"],
        "input_hashes": input_hashes,
    }
    _write_exclusive(result_dir / "manifest.json", _pretty_bytes(final_manifest))
    result_dir.chmod(0o555)
    return final_manifest


def run_live(bundle: A11ControllerBundle, *, lock_path: Path, max_attempts: int | None) -> int:
    _verify_loaded_code(bundle)
    sealed_lock = Path(bundle.manifest["integrity"]["singleton_lock"])
    if lock_path.resolve() != sealed_lock:
        raise ValueError("A11 live lock path differs from the controller seal")
    if bundle.manifest["execution"]["model"] != REGISTERED_MODEL or bundle.manifest[
        "execution"
    ]["reasoning_effort"] != REGISTERED_REASONING_EFFORT:
        raise ValueError("A11 registered execution changed")
    try:
        lock = transport._acquire_live_instance_lock(lock_path.resolve())
    except transport.AlreadyRunning as exc:
        print(f"ALREADY_RUNNING: {exc}")
        return transport.LOCK_BUSY_EXIT
    attempts_this_invocation = 0
    with lock:
        for question_id, arm in transport.interleaved_schedule(
            bundle.question_ids, list(bundle.arms)
        ):
            reason = a11_blocking_artifact_reason(bundle, arm, question_id)
            if reason is not None:
                print(
                    f"BLOCKED_ARTIFACT question={question_id} arm={arm.name} reason={reason}",
                    flush=True,
                )
                return 1
            if is_a11_terminal(bundle, arm, question_id):
                continue
            if transport._retry_cap_reached(arm, question_id):
                print(f"BLOCKED_RETRY_CAP question={question_id} arm={arm.name}", flush=True)
                return 1
            while True:
                if max_attempts is not None and attempts_this_invocation >= max_attempts:
                    print("MAX_ATTEMPTS_REACHED_INCOMPLETE", flush=True)
                    return PARTIAL_RUN_EXIT
                attempt_number = len(transport._attempt_receipts(arm, question_id)) + 1
                if attempt_number > REGISTERED_MAX_ATTEMPTS:
                    return 1
                command = _build_a11_harness_command(
                    bundle=bundle,
                    arm=arm,
                    question_id=question_id,
                )
                print(
                    f"RUN question={question_id} arm={arm.name} attempt={attempt_number}",
                    flush=True,
                )
                result, runtime_unchanged = _execute_a11_harness_command(
                    bundle=bundle,
                    command=command,
                )
                attempts_this_invocation += 1
                if not runtime_unchanged:
                    print(
                        "BLOCKED_CODEX_POSTCALL_INTEGRITY "
                        f"question={question_id} arm={arm.name}",
                        flush=True,
                    )
                    return 1
                receipt = transport._write_attempt_receipt(
                    arm=arm,
                    question_id=question_id,
                    controller_manifest_sha256=bundle.manifest_sha256,
                    returncode=result.returncode,
                    model=REGISTERED_MODEL,
                    reasoning_effort=REGISTERED_REASONING_EFFORT,
                    attempt_number=attempt_number,
                    schema_path=bundle.schema_path,
                )
                try:
                    receipt = _augment_attempt_receipt(
                        receipt,
                        bundle=bundle,
                        arm=arm,
                        question_id=question_id,
                    )
                except ValueError as exc:
                    print(
                        f"BLOCKED_A11_RECEIPT question={question_id} arm={arm.name} error={exc}",
                        flush=True,
                    )
                    return 1
                if receipt["status"] == "answered":
                    break
                failure_status = transport._failed_attempt_status(
                    receipt, transport._question_dir(arm, question_id)
                )
                if (
                    failure_status == "transient_failure"
                    and not is_a11_retryable_provider_failure(receipt)
                ):
                    failure_status = "model_failure"
                archive_receipt = {**receipt, "status": failure_status}
                if failure_status == "model_failure":
                    # Use the inherited transport's fail-closed terminal status
                    # while retaining the exact A11 semantic classification.
                    archive_receipt["status"] = "contaminated"
                    archive_receipt["a11_terminal_failure"] = "model_failure"
                archived = transport._archive_failed_attempt(
                    arm=arm,
                    question_id=question_id,
                    receipt=archive_receipt,
                )
                if failure_status == "model_failure":
                    print(
                        f"BLOCKED_MODEL_FAILURE question={question_id} arm={arm.name} "
                        f"attempt_receipt={archived['attempt_receipt_path']}",
                        flush=True,
                    )
                    return result.returncode or 1
                reason = a11_blocking_artifact_reason(bundle, arm, question_id)
                if reason is not None:
                    print(f"BLOCKED_ARCHIVE_INTEGRITY reason={reason}", flush=True)
                    return 1
                if failure_status != "transient_failure" or attempt_number >= REGISTERED_MAX_ATTEMPTS:
                    print(
                        f"BLOCKED_{failure_status.upper()} question={question_id} arm={arm.name} "
                        f"attempt_receipt={archived['attempt_receipt_path']}",
                        flush=True,
                    )
                    return result.returncode or 1
                print(
                    f"RETRY_TRANSIENT question={question_id} arm={arm.name} "
                    f"attempt_receipt={archived['attempt_receipt_path']}",
                    flush=True,
                )
    progress = a11_progress(bundle)
    print(json.dumps(progress, indent=2, sort_keys=True))
    return 0 if progress["fully_paired"] == len(bundle.question_ids) else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path)
    parser.add_argument("--answer-inputs-dir", type=Path)
    parser.add_argument("--compilation-timing", type=Path)
    parser.add_argument(
        "--controller-manifest",
        type=Path,
        default=Path("runs/a11-vte-controller/manifest.json"),
    )
    parser.add_argument(
        "--schema", type=Path, default=Path("schemas/codex_answer.schema.json")
    )
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--v-out", type=Path, default=Path("runs/codex-a11-v"))
    parser.add_argument("--t-out", type=Path, default=Path("runs/codex-a11-t"))
    parser.add_argument("--e-out", type=Path, default=Path("runs/codex-a11-e"))
    parser.add_argument("--grading-out", type=Path, default=Path("runs/a11-grading"))
    parser.add_argument("--panel-out", type=Path, default=Path("runs/a11-panel"))
    parser.add_argument("--result-out", type=Path, default=Path("runs/a11-result"))
    parser.add_argument("--lock", type=Path, default=Path("runs/.a11-vte.lock"))
    parser.add_argument("--max-attempts", type=int, default=None)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--seal", action="store_true")
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--prepare-grading", action="store_true")
    modes.add_argument("--finalize", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.seal:
        missing = [
            name
            for name, value in (
                ("--dataset-dir", args.dataset_dir),
                ("--answer-inputs-dir", args.answer_inputs_dir),
                ("--compilation-timing", args.compilation_timing),
            )
            if value is None
        ]
        if missing:
            raise SystemExit("--seal requires " + ", ".join(missing))
        bundle = seal_controller(
            dataset_dir=args.dataset_dir,
            answer_inputs_dir=args.answer_inputs_dir,
            compilation_timing=args.compilation_timing,
            controller_manifest=args.controller_manifest,
            schema_path=args.schema,
            codex_bin=args.codex_bin,
            arm_outputs={"v": args.v_out, "t": args.t_out, "e": args.e_out},
            grading_output=args.grading_out,
            panel_output=args.panel_out,
            result_output=args.result_out,
            lock_path=args.lock,
        )
        print(
            json.dumps(
                {
                    "sealed": True,
                    "model_calls": 0,
                    "manifest": str(bundle.manifest_path),
                    "manifest_sha256": bundle.manifest_sha256,
                    "questions": len(bundle.question_ids),
                    "scheduled_answers": len(bundle.question_ids) * len(bundle.arms),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if not args.controller_manifest.exists():
        raise SystemExit("A11 controller is not sealed; run --seal before --status/--live")
    bundle = load_controller(args.controller_manifest)
    if args.status:
        print(json.dumps(a11_progress(bundle), indent=2, sort_keys=True))
        return 0
    if args.prepare_grading:
        print(json.dumps(prepare_grading(bundle), indent=2, sort_keys=True))
        return 0
    if args.finalize:
        print(json.dumps(finalize_result(bundle), indent=2, sort_keys=True))
        return 0
    return run_live(bundle, lock_path=args.lock, max_attempts=args.max_attempts)


if __name__ == "__main__":
    raise SystemExit(main())
