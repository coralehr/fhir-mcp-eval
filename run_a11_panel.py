#!/usr/bin/env python3
"""Sealed, arm-blind panel runner for the A11 efficacy experiment.

The runner deliberately owns its transport and retry ledger instead of using
``panel_grade.py``'s legacy cache.  Every Codex attempt retains stdout JSONL,
stderr, the structured verdict document, and a receipt bound to the A11
controller, queue, judge configuration, opaque batch, and token usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import codex_harness
import panel_grade
import run_lock
from run_lock import AlreadyRunning, LOCK_BUSY_EXIT, acquire_single_instance


PANEL_KIND = "a11_arm_blind_panel"
PANEL_MANIFEST_VERSION = "a11-panel-manifest-v1"
PANEL_ATTEMPT_VERSION = "a11-panel-attempt-v1"
PANEL_PROTOCOL_VERSION = "a11-acceptable-any-panel-v1"
OPAQUE_ID_VERSION = "a11-opaque-content-config-v1"
ORDERING_VERSION = "a11-opaque-round-robin-v1"
TRANSPORT_VERSION = "separated-stdout-jsonl-stderr-v2"

REGISTERED_MODEL = "gpt-5.6-sol"
REGISTERED_REASONING_EFFORT = "high"
REGISTERED_VOTES = 3
REGISTERED_BATCH_SIZE = 20
REGISTERED_TIMEOUT_SECONDS = 600
MAX_OPERATIONAL_ATTEMPTS_PER_BATCH = 3
REGISTERED_ARMS = frozenset({"v", "t", "e"})
CONTROLLER_PANEL_CONFIG_FIELDS = frozenset(
    {
        "model",
        "reasoning_effort",
        "votes",
        "batch_size",
        "timeout_seconds",
        "codex_bin",
        "codex_version",
        "codex_binary_sha256",
        "panel_source_sha256",
    }
)

ATTEMPT_RECEIPT_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "status",
        "controller_manifest_sha256",
        "queue_sha256",
        "judge_config_sha256",
        "vote_round",
        "batch_number",
        "attempt_number",
        "opaque_ids",
        "codex_binary",
        "codex_version",
        "codex_binary_sha256",
        "returncode",
        "error",
        "prompt",
        "schema",
        "event_stream",
        "event_integrity",
        "stderr",
        "verdict",
        "verdicts_sha256",
        "usage",
        "retryable_provider_failure",
    }
)

A11_JUDGE_PREAMBLE = """You are grading synthetic clinical question-answering outputs.
For each item decide whether the model answer is CORRECT.

Rules:
- Do not use tools, commands, or files. Judge only the ITEMS included below.
- `acceptable_any` is a list of independently acceptable categorical forms for
  the same answer. Matching ANY ONE value is sufficient; do not require all.
- Matching is case- and harmless-format-insensitive. A categorical code or its
  paired display text is acceptable when either appears in `acceptable_any`.
- The answer must unambiguously identify an acceptable value. Do not credit a
  competing value merely because an acceptable value appears in a disclaimer.
- An insufficiency or abstention is incorrect for these queued, answerable
  categorical items.
- Judge only correctness. Ignore style, length, and citation formatting.

Return JSON: {"verdicts": [{"item_id": "...", "correct": true|false}, ...]}
covering EVERY item exactly once.

ITEMS:
"""

BATCH_SCHEMA = panel_grade.BATCH_SCHEMA


class PanelProtocolError(RuntimeError):
    """The sealed panel cannot continue without changing registered state."""


@dataclass(frozen=True)
class CodexIdentity:
    path: Path
    version: str
    sha256: str


@dataclass(frozen=True)
class AttemptOutcome:
    accepted: bool
    receipt: dict[str, Any]
    result: dict[str, bool] | None


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: object) -> None:
    _write_bytes_atomic(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def load_controller_codex_identity(
    controller_manifest: Path,
    *,
    expected_controller_sha256: str,
) -> tuple[str, CodexIdentity, Path]:
    """Bind the panel to the exact answer-controller Codex runtime."""

    if not _is_sha256(expected_controller_sha256):
        raise ValueError("expected controller SHA-256 must be lowercase hex")
    actual_controller_sha256 = sha256_file(controller_manifest)
    if actual_controller_sha256 != expected_controller_sha256:
        raise ValueError("controller manifest does not match expected SHA-256")
    sidecar = controller_manifest.with_suffix(".sha256")
    if (
        not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != actual_controller_sha256 + "\n"
    ):
        raise ValueError("controller manifest sidecar changed")
    controller = _read_json(controller_manifest)
    if not isinstance(controller, dict):
        raise ValueError("controller manifest must be an object")
    if controller.get("kind") != "a11_interleaved_controller_manifest":
        raise ValueError("controller manifest kind is not registered for A11")
    if controller.get("schema_version") != "a11-controller-v1":
        raise ValueError("controller manifest schema is not A11 v1")
    execution = controller.get("execution")
    if not isinstance(execution, dict):
        raise ValueError("controller manifest has no execution identity")
    if execution.get("model") != REGISTERED_MODEL or execution.get(
        "reasoning_effort"
    ) != REGISTERED_REASONING_EFFORT:
        raise ValueError("controller answer model is not the registered A11 model")

    raw_codex = execution.get("codex")
    if not isinstance(raw_codex, dict) or set(raw_codex) != {
        "path",
        "version",
        "sha256",
    }:
        raise ValueError("controller has no exact Codex identity")
    raw_path = raw_codex.get("path")
    version = raw_codex.get("version")
    binary_sha256 = raw_codex.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("controller has no absolute Codex path")
    path = Path(raw_path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("controller Codex path must be absolute and resolved")
    if not isinstance(version, str) or not version:
        raise ValueError("controller has no Codex version")
    if not _is_sha256(binary_sha256):
        raise ValueError("controller has no Codex binary SHA-256")
    if not path.is_file() or sha256_file(path) != binary_sha256:
        raise ValueError("controller Codex binary is missing or changed")

    observed_version = codex_version(path)
    if observed_version != version:
        raise ValueError("controller Codex version changed")
    grading = controller.get("grading")
    panel_config = grading.get("panel") if isinstance(grading, dict) else None
    if not isinstance(panel_config, dict) or set(panel_config) != CONTROLLER_PANEL_CONFIG_FIELDS:
        raise ValueError("controller panel configuration changed")
    expected_panel_config = {
        "model": REGISTERED_MODEL,
        "reasoning_effort": REGISTERED_REASONING_EFFORT,
        "votes": REGISTERED_VOTES,
        "batch_size": REGISTERED_BATCH_SIZE,
        "timeout_seconds": REGISTERED_TIMEOUT_SECONDS,
        "codex_bin": str(path),
        "codex_version": version,
        "codex_binary_sha256": binary_sha256,
    }
    if any(panel_config.get(key) != value for key, value in expected_panel_config.items()):
        raise ValueError("controller panel pins differ from the registered A11 panel")
    panel_source_sha256 = panel_config.get("panel_source_sha256")
    snapshots = controller.get("snapshots")
    panel_snapshot = (
        snapshots.get("run_a11_panel") if isinstance(snapshots, dict) else None
    )
    if not _is_sha256(panel_source_sha256) or not isinstance(panel_snapshot, dict):
        raise ValueError("loaded A11 panel source differs from the controller seal")
    loaded_runtime = {
        "run_a11_panel": Path(__file__).resolve(),
        "panel_grade": Path(panel_grade.__file__).resolve(),
        "codex_harness": Path(codex_harness.__file__).resolve(),
        "run_lock": Path(run_lock.__file__).resolve(),
    }
    for name, loaded_path in loaded_runtime.items():
        entry = snapshots.get(name) if isinstance(snapshots, dict) else None
        if (
            not isinstance(entry, dict)
            or not _is_sha256(entry.get("sha256"))
            or Path(str(entry.get("snapshot_path") or "")).resolve() != loaded_path
            or not loaded_path.is_file()
            or sha256_file(loaded_path) != entry.get("sha256")
            or loaded_path.stat().st_size != entry.get("bytes")
        ):
            raise ValueError(f"loaded A11 panel runtime differs from seal: {name}")
    if panel_snapshot.get("sha256") != panel_source_sha256:
        raise ValueError("loaded A11 panel source differs from the controller seal")
    outputs = controller.get("outputs")
    raw_panel_output = outputs.get("panel") if isinstance(outputs, dict) else None
    if not isinstance(raw_panel_output, str) or not raw_panel_output:
        raise ValueError("controller has no registered panel output")
    panel_output = Path(raw_panel_output)
    if not panel_output.is_absolute() or panel_output.resolve() != panel_output:
        raise ValueError("controller panel output must be absolute and resolved")
    return actual_controller_sha256, CodexIdentity(
        path=path,
        version=version,
        sha256=binary_sha256,
    ), panel_output


def validate_registered_grading_queue(
    *,
    controller_manifest: Path,
    controller_manifest_sha256: str,
    queue_path: Path,
) -> None:
    """Prove the queue is the immutable output of registered A11 grading."""

    controller = _read_json(controller_manifest)
    outputs = controller.get("outputs")
    raw_grading_output = outputs.get("grading") if isinstance(outputs, dict) else None
    if not isinstance(raw_grading_output, str) or not raw_grading_output:
        raise ValueError("controller has no registered grading output")
    grading_output = Path(raw_grading_output)
    if not grading_output.is_absolute() or grading_output.resolve() != grading_output:
        raise ValueError("controller grading output must be absolute and resolved")
    expected_queue = grading_output / "panel_queue.jsonl"
    if queue_path.resolve() != expected_queue:
        raise ValueError("queue is not the registered A11 grading panel queue")
    grading_manifest_path = grading_output / "manifest.json"
    try:
        grading_manifest = _read_json(grading_manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise PanelProtocolError("A11 grading manifest is unavailable") from exc
    if (
        not isinstance(grading_manifest, dict)
        or grading_manifest.get("schema_version") != "a11-grading-preparation-v1"
        or grading_manifest.get("controller_manifest_sha256")
        != controller_manifest_sha256
        or grading_manifest.get("model_calls") != 0
        or grading_manifest.get("all_checks_passed") is not True
        or grading_manifest.get("panel_config") != controller.get("grading", {}).get("panel")
    ):
        raise PanelProtocolError("A11 grading manifest binding changed")
    artifacts = grading_manifest.get("artifacts")
    queue_receipt = artifacts.get("panel_queue.jsonl") if isinstance(artifacts, dict) else None
    if (
        not isinstance(queue_receipt, dict)
        or set(queue_receipt) != {"sha256", "bytes"}
        or not queue_path.is_file()
        or queue_receipt.get("sha256") != sha256_file(queue_path)
        or queue_receipt.get("bytes") != queue_path.stat().st_size
    ):
        raise PanelProtocolError("A11 grading queue binding changed")


def panel_lock_path(out_dir: Path) -> Path:
    """Return the one canonical singleton lock for a registered panel output."""

    resolved = out_dir.resolve()
    return resolved.with_name(f".{resolved.name}.lock")


def codex_version(path: Path) -> str:
    process = subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    version = (process.stdout or process.stderr or "").strip()
    if process.returncode != 0 or not version:
        raise ValueError(f"could not determine Codex version for {path}")
    return version


def load_a11_queue(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    raw = path.read_bytes()
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid panel queue JSON at line {line_number}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"panel queue line {line_number} is not an object")
        if set(item) != {
            "arm",
            "question_id",
            "question",
            "gold",
            "answer",
            "insufficiency_reason",
        }:
            raise ValueError(f"panel queue line {line_number} fields changed")
        arm = item.get("arm")
        question_id = item.get("question_id")
        question = item.get("question")
        answer = item.get("answer")
        gold = item.get("gold")
        acceptable = gold.get("acceptable_any") if isinstance(gold, dict) else None
        insufficiency = item.get("insufficiency_reason")
        if arm not in REGISTERED_ARMS:
            raise ValueError(f"panel queue line {line_number} has an invalid arm")
        if (
            not isinstance(question_id, str)
            or not question_id
            or "|" in question_id
        ):
            raise ValueError(f"panel queue line {line_number} has an invalid question ID")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"panel queue line {line_number} has no question")
        if not isinstance(answer, str):
            raise ValueError(f"panel queue line {line_number} has no answer string")
        if insufficiency is not None and not isinstance(insufficiency, str):
            raise ValueError(
                f"panel queue line {line_number} has an invalid insufficiency reason"
            )
        if (
            not isinstance(gold, dict)
            or set(gold) != {"acceptable_any"}
            or not isinstance(acceptable, list)
            or not acceptable
            or any(not isinstance(value, str) or not value.strip() for value in acceptable)
            or len(set(acceptable)) != len(acceptable)
        ):
            raise ValueError(
                f"panel queue line {line_number} requires unique acceptable_any strings"
            )
        rows.append(
            {
                "arm": arm,
                "question_id": question_id,
                "question": question,
                "acceptable_any": list(acceptable),
                "answer": answer,
                "insufficiency_reason": insufficiency,
            }
        )
    if not rows:
        raise ValueError("A11 panel queue is empty")
    hosts = [(row["arm"], row["question_id"]) for row in rows]
    if len(hosts) != len(set(hosts)):
        raise ValueError("A11 panel queue contains duplicate arm/question items")
    return raw, rows


def build_judge_config(
    *,
    controller_manifest_sha256: str,
    codex: CodexIdentity,
) -> dict[str, Any]:
    return {
        "panel_protocol_version": PANEL_PROTOCOL_VERSION,
        "opaque_id_version": OPAQUE_ID_VERSION,
        "ordering_version": ORDERING_VERSION,
        "transport_version": TRANSPORT_VERSION,
        "judge_preamble_sha256": sha256_bytes(A11_JUDGE_PREAMBLE.encode("utf-8")),
        "output_schema_sha256": sha256_json(BATCH_SCHEMA),
        "controller_manifest_sha256": controller_manifest_sha256,
        "model": REGISTERED_MODEL,
        "reasoning_effort": REGISTERED_REASONING_EFFORT,
        "requested_votes": REGISTERED_VOTES,
        "batch_size": REGISTERED_BATCH_SIZE,
        "timeout_seconds": REGISTERED_TIMEOUT_SECONDS,
        "max_operational_attempts_per_batch": MAX_OPERATIONAL_ATTEMPTS_PER_BATCH,
        "empty_nonrepository_cwd": True,
        "tool_events_allowed": False,
        "codex_binary": str(codex.path),
        "codex_version": codex.version,
        "codex_binary_sha256": codex.sha256,
    }


def prepare_blinded_items(
    queue: list[dict[str, Any]], judge_config: dict[str, Any]
) -> list[dict[str, Any]]:
    config_sha256 = sha256_json(judge_config)
    opaque_ids: set[str] = set()
    blinded: list[dict[str, Any]] = []
    for item in queue:
        host = {"arm": item["arm"], "question_id": item["question_id"]}
        payload = {
            "question": item["question"],
            "acceptable_any": list(item["acceptable_any"]),
            "model_answer": item["answer"],
            "insufficiency_reason": item.get("insufficiency_reason"),
        }
        content_sha256 = sha256_json(
            {
                "binding_version": OPAQUE_ID_VERSION,
                "host": host,
                "judge_payload": payload,
            }
        )
        opaque_digest = sha256_json(
            {
                "content_sha256": content_sha256,
                "judge_config_sha256": config_sha256,
            }
        )
        opaque_id = f"a11panel_{opaque_digest[:32]}"
        if opaque_id in opaque_ids:
            raise ValueError("A11 opaque panel ID collision")
        opaque_ids.add(opaque_id)
        blinded.append(
            {
                "opaque_id": opaque_id,
                "host": host,
                "judge_payload": payload,
                "content_sha256": content_sha256,
            }
        )
    return blinded


def deterministic_interleave(
    items: list[dict[str, Any]], *, vote_round: int
) -> list[dict[str, Any]]:
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_arm.setdefault(item["host"]["arm"], []).append(item)
    for arm_items in by_arm.values():
        arm_items.sort(
            key=lambda item: sha256_json(
                {
                    "ordering_version": ORDERING_VERSION,
                    "vote_round": vote_round,
                    "opaque_id": item["opaque_id"],
                }
            )
        )
    arms = sorted(
        by_arm,
        key=lambda arm: sha256_json(
            {
                "ordering_version": ORDERING_VERSION,
                "vote_round": vote_round,
                "arm": arm,
            }
        ),
    )
    result: list[dict[str, Any]] = []
    positions = {arm: 0 for arm in arms}
    while len(result) < len(items):
        for arm in arms:
            position = positions[arm]
            if position < len(by_arm[arm]):
                result.append(by_arm[arm][position])
                positions[arm] += 1
    return result


def expected_batches(
    items: list[dict[str, Any]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    batches: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for vote_round in range(REGISTERED_VOTES):
        ordered = deterministic_interleave(items, vote_round=vote_round)
        for batch_number, start in enumerate(
            range(0, len(ordered), REGISTERED_BATCH_SIZE)
        ):
            batches[(vote_round, batch_number)] = ordered[
                start : start + REGISTERED_BATCH_SIZE
            ]
    return batches


def batch_prompt(batch: list[dict[str, Any]]) -> str:
    lines = [A11_JUDGE_PREAMBLE]
    for item in batch:
        payload = item["judge_payload"]
        lines.append(
            json.dumps(
                {
                    "item_id": item["opaque_id"],
                    "question": payload["question"],
                    "acceptable_any": payload["acceptable_any"],
                    "model_answer": payload["model_answer"],
                    "insufficiency_reason": payload["insufficiency_reason"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n".join(lines)


def build_manifest_identity(
    *,
    controller_manifest_sha256: str,
    queue_sha256: str,
    judge_config: dict[str, Any],
    blinded_items: list[dict[str, Any]],
) -> dict[str, Any]:
    bindings = sorted(
        (
            {
                "opaque_id": item["opaque_id"],
                "host": item["host"],
                "content_sha256": item["content_sha256"],
            }
            for item in blinded_items
        ),
        key=lambda item: item["opaque_id"],
    )
    return {
        "kind": PANEL_KIND,
        "schema_version": PANEL_MANIFEST_VERSION,
        "controller_manifest_sha256": controller_manifest_sha256,
        "queue_sha256": queue_sha256,
        "judge_config": judge_config,
        "judge_config_sha256": sha256_json(judge_config),
        "queue_binding_sha256": sha256_json(bindings),
        "item_count": len(bindings),
        "batch_count_per_vote": {
            str(vote_round): sum(
                key[0] == vote_round for key in expected_batches(blinded_items)
            )
            for vote_round in range(REGISTERED_VOTES)
        },
    }


def initialize_or_validate_bundle(
    out_dir: Path,
    *,
    identity: dict[str, Any],
    queue_bytes: bytes,
) -> dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    queue_snapshot = out_dir / "queue.snapshot.jsonl"
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        if manifest != identity:
            raise PanelProtocolError("immutable A11 panel manifest changed")
        if not queue_snapshot.exists() or sha256_file(queue_snapshot) != identity[
            "queue_sha256"
        ]:
            raise PanelProtocolError("A11 panel queue snapshot changed")
        return manifest

    if out_dir.exists() and any(out_dir.iterdir()):
        raise PanelProtocolError("panel artifacts exist without an immutable manifest")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomic(queue_snapshot, queue_bytes)
    _write_json_atomic(manifest_path, identity)
    if sha256_file(queue_snapshot) != identity["queue_sha256"]:
        raise PanelProtocolError("panel queue snapshot copy changed")
    return identity


def _attempt_root(out_dir: Path, vote_round: int, batch_number: int) -> Path:
    return out_dir / "attempts" / f"vote-{vote_round:02d}" / f"batch-{batch_number:04d}"


def _attempt_dirs(out_dir: Path, vote_round: int, batch_number: int) -> list[Path]:
    root = _attempt_root(out_dir, vote_round, batch_number)
    if not root.exists():
        return []
    result = [
        path
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith("attempt-")
    ]
    return sorted(result, key=lambda path: path.name)


def _validate_event_stream(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    codex_event_audit = codex_harness.audit_event_log(path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "valid": False,
            "reason": "not_utf8",
            "bytes": len(data),
            "codex_event_audit": codex_event_audit,
        }
    if not data or not data.endswith(b"\n"):
        return {
            "valid": False,
            "reason": "missing_terminal_newline",
            "bytes": len(data),
            "codex_event_audit": codex_event_audit,
        }
    count = 0
    for line in text.splitlines():
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return {
                "valid": False,
                "reason": "invalid_jsonl",
                "bytes": len(data),
                "codex_event_audit": codex_event_audit,
            }
        if not isinstance(event, dict):
            return {
                "valid": False,
                "reason": "non_object_event",
                "bytes": len(data),
                "codex_event_audit": codex_event_audit,
            }
        count += 1
    usage = panel_grade.parse_panel_usage(text)
    valid = (
        count > 0
        and usage["complete"] is True
        and codex_event_audit.get("contaminated") is False
    )
    return {
        "valid": valid,
        "reason": (
            None
            if valid
            else (
                "event_integrity"
                if codex_event_audit.get("contaminated") is True
                else "incomplete_usage"
            )
        ),
        "bytes": len(data),
        "events": count,
        "usage": usage,
        "codex_event_audit": codex_event_audit,
    }


def _retryable_provider_failure(
    *,
    returncode: int | None,
    error: str | None,
    event_integrity: dict[str, Any],
    stderr_empty: bool,
    verdict_exists: bool,
) -> bool:
    return (
        returncode not in (None, 0)
        and error == "invalid_transport"
        and stderr_empty
        and not verdict_exists
        and codex_harness.is_retryable_incomplete_packet_audit(
            event_integrity.get("codex_event_audit")
        )
    )


def _parse_verdict(path: Path, expected_ids: list[str]) -> dict[str, bool]:
    document = _read_json(path)
    if not isinstance(document, dict) or set(document) != {"verdicts"}:
        raise ValueError("panel verdict document fields changed")
    verdicts = document.get("verdicts")
    if not isinstance(verdicts, list):
        raise ValueError("panel verdicts must be a list")
    result: dict[str, bool] = {}
    for verdict in verdicts:
        if (
            not isinstance(verdict, dict)
            or set(verdict) != {"item_id", "correct"}
            or not isinstance(verdict.get("item_id"), str)
            or type(verdict.get("correct")) is not bool
        ):
            raise ValueError("panel verdict item fields changed")
        if verdict["item_id"] in result:
            raise ValueError("panel verdict contains a duplicate opaque ID")
        result[verdict["item_id"]] = verdict["correct"]
    if set(result) != set(expected_ids):
        raise ValueError("panel verdict does not cover the exact opaque batch")
    return result


def _artifact_receipt(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"sha256": None, "bytes": 0}
    return {"sha256": sha256_file(path), "bytes": path.stat().st_size}


def _validate_binary(codex: CodexIdentity) -> None:
    if not codex.path.is_file() or sha256_file(codex.path) != codex.sha256:
        raise PanelProtocolError("Codex binary changed before panel call")


def execute_attempt(
    *,
    attempt_dir: Path,
    batch: list[dict[str, Any]],
    vote_round: int,
    batch_number: int,
    attempt_number: int,
    manifest: dict[str, Any],
    codex: CodexIdentity,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AttemptOutcome:
    """Execute one panel call and persist a complete accepted/failed receipt."""

    _validate_binary(codex)
    attempt_dir.mkdir(parents=True, exist_ok=False)
    prompt_path = attempt_dir / "prompt.txt"
    schema_path = attempt_dir / "schema.json"
    event_path = attempt_dir / "events.jsonl"
    stderr_path = attempt_dir / "stderr.log"
    verdict_path = attempt_dir / "verdict.json"
    prompt = batch_prompt(batch)
    _write_bytes_atomic(prompt_path, prompt.encode("utf-8"))
    _write_json_atomic(schema_path, BATCH_SCHEMA)

    command = [
        str(codex.path),
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--output-schema",
        str(schema_path.resolve()),
        "--output-last-message",
        str(verdict_path.resolve()),
        "-C",
        "__A11_EMPTY_CWD__",
        "-s",
        "read-only",
        "-m",
        REGISTERED_MODEL,
        "-c",
        f'model_reasoning_effort="{REGISTERED_REASONING_EFFORT}"',
        "-",
    ]
    _validate_binary(codex)  # Required immediately before every model call.
    returncode: int | None = None
    error: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="a11-panel-empty-cwd-") as sandbox:
            command[command.index("__A11_EMPTY_CWD__")] = str(Path(sandbox).resolve())
            with event_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                process = run_process(
                    command,
                    input=prompt,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    timeout=REGISTERED_TIMEOUT_SECONDS,
                    check=False,
                )
                returncode = process.returncode
    except subprocess.TimeoutExpired:
        error = "timeout"
    except Exception as exc:  # Persist transport failures without leaking details.
        error = f"transport_{type(exc).__name__}"

    event_integrity = _validate_event_stream(event_path)
    stderr_receipt = _artifact_receipt(stderr_path)
    stderr_empty = stderr_receipt["bytes"] == 0
    expected_ids = [item["opaque_id"] for item in batch]
    result: dict[str, bool] | None = None
    if (
        error is None
        and returncode == 0
        and event_integrity["valid"] is True
        and stderr_empty
        and verdict_path.exists()
    ):
        try:
            result = _parse_verdict(verdict_path, expected_ids)
        except (OSError, ValueError, json.JSONDecodeError):
            error = "invalid_verdict"
    elif error is None:
        error = "invalid_transport"

    accepted = result is not None and error is None
    retryable_provider_failure = (
        not accepted
        and _retryable_provider_failure(
            returncode=returncode,
            error=error,
            event_integrity=event_integrity,
            stderr_empty=stderr_empty,
            verdict_exists=verdict_path.exists(),
        )
    )
    receipt = {
        "kind": "a11_panel_attempt",
        "schema_version": PANEL_ATTEMPT_VERSION,
        "status": "accepted" if accepted else "failed",
        "controller_manifest_sha256": manifest["controller_manifest_sha256"],
        "queue_sha256": manifest["queue_sha256"],
        "judge_config_sha256": manifest["judge_config_sha256"],
        "vote_round": vote_round,
        "batch_number": batch_number,
        "attempt_number": attempt_number,
        "opaque_ids": expected_ids,
        "codex_binary": str(codex.path),
        "codex_version": codex.version,
        "codex_binary_sha256": codex.sha256,
        "returncode": returncode,
        "error": error,
        "prompt": _artifact_receipt(prompt_path),
        "schema": _artifact_receipt(schema_path),
        "event_stream": _artifact_receipt(event_path),
        "event_integrity": {
            key: value for key, value in event_integrity.items() if key != "usage"
        },
        "stderr": {**stderr_receipt, "empty": stderr_empty},
        "verdict": _artifact_receipt(verdict_path),
        "verdicts_sha256": sha256_json(result) if result is not None else None,
        "usage": event_integrity.get("usage", panel_grade.parse_panel_usage("")),
        "retryable_provider_failure": retryable_provider_failure,
    }
    _write_json_atomic(attempt_dir / "receipt.json", receipt)
    return AttemptOutcome(accepted=accepted, receipt=receipt, result=result)


def _validate_attempt_receipt(
    attempt_dir: Path,
    *,
    manifest: dict[str, Any],
    vote_round: int,
    batch_number: int,
    batch: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, bool] | None]:
    receipt_path = attempt_dir / "receipt.json"
    if not receipt_path.exists():
        raise PanelProtocolError(f"panel attempt has no receipt: {attempt_dir}")
    receipt = _read_json(receipt_path)
    if not isinstance(receipt, dict) or set(receipt) != ATTEMPT_RECEIPT_FIELDS:
        raise PanelProtocolError(f"panel attempt receipt fields changed: {attempt_dir}")
    expected_ids = [item["opaque_id"] for item in batch]
    required = {
        "kind": "a11_panel_attempt",
        "schema_version": PANEL_ATTEMPT_VERSION,
        "controller_manifest_sha256": manifest["controller_manifest_sha256"],
        "queue_sha256": manifest["queue_sha256"],
        "judge_config_sha256": manifest["judge_config_sha256"],
        "vote_round": vote_round,
        "batch_number": batch_number,
        "opaque_ids": expected_ids,
        "codex_binary": manifest["judge_config"]["codex_binary"],
        "codex_version": manifest["judge_config"]["codex_version"],
        "codex_binary_sha256": manifest["judge_config"]["codex_binary_sha256"],
    }
    if any(receipt.get(k) != v for k, v in required.items()):
        raise PanelProtocolError(f"panel attempt receipt binding changed: {attempt_dir}")
    try:
        expected_attempt_number = int(attempt_dir.name.split("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise PanelProtocolError(f"invalid panel attempt directory: {attempt_dir}") from exc
    if receipt.get("attempt_number") != expected_attempt_number:
        raise PanelProtocolError(f"panel attempt number changed: {attempt_dir}")
    for receipt_key, filename in (
        ("prompt", "prompt.txt"),
        ("schema", "schema.json"),
        ("event_stream", "events.jsonl"),
        ("stderr", "stderr.log"),
        ("verdict", "verdict.json"),
    ):
        recorded = receipt.get(receipt_key)
        if not isinstance(recorded, dict):
            raise PanelProtocolError(f"panel artifact receipt is malformed: {attempt_dir}")
        actual = _artifact_receipt(attempt_dir / filename)
        if {key: recorded.get(key) for key in ("sha256", "bytes")} != actual:
            raise PanelProtocolError(f"panel attempt artifact changed: {attempt_dir / filename}")

    expected_prompt = batch_prompt(batch).encode("utf-8")
    if (attempt_dir / "prompt.txt").read_bytes() != expected_prompt:
        raise PanelProtocolError(f"panel attempt prompt changed: {attempt_dir}")
    try:
        schema = _read_json(attempt_dir / "schema.json")
    except (OSError, json.JSONDecodeError) as exc:
        raise PanelProtocolError(f"panel attempt schema changed: {attempt_dir}") from exc
    if schema != BATCH_SCHEMA:
        raise PanelProtocolError(f"panel attempt schema changed: {attempt_dir}")

    observed_event_integrity = _validate_event_stream(attempt_dir / "events.jsonl")
    observed_usage = observed_event_integrity.pop(
        "usage", panel_grade.parse_panel_usage("")
    )
    if receipt.get("event_integrity") != observed_event_integrity:
        raise PanelProtocolError(f"panel event integrity changed: {attempt_dir}")
    if receipt.get("usage") != observed_usage:
        raise PanelProtocolError(f"panel token usage changed: {attempt_dir}")
    observed_stderr_empty = (attempt_dir / "stderr.log").stat().st_size == 0
    if receipt.get("stderr", {}).get("empty") is not observed_stderr_empty:
        raise PanelProtocolError(f"panel stderr integrity changed: {attempt_dir}")

    status = receipt.get("status")
    if status not in {"accepted", "failed"}:
        raise PanelProtocolError(f"panel attempt status changed: {attempt_dir}")
    result = None
    if status == "accepted":
        try:
            result = _parse_verdict(attempt_dir / "verdict.json", expected_ids)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise PanelProtocolError(f"accepted panel verdict changed: {attempt_dir}") from exc
        if receipt.get("verdicts_sha256") != sha256_json(result):
            raise PanelProtocolError(f"accepted panel verdict binding changed: {attempt_dir}")
        if (
            receipt.get("returncode") != 0
            or receipt.get("error") is not None
            or observed_event_integrity.get("valid") is not True
            or not observed_stderr_empty
        ):
            raise PanelProtocolError(f"accepted panel transport changed: {attempt_dir}")
    elif receipt.get("verdicts_sha256") is not None:
        raise PanelProtocolError(f"failed panel attempt has a verdict binding: {attempt_dir}")
    elif (
        receipt.get("returncode") == 0
        and receipt.get("error") is None
        and observed_event_integrity.get("valid") is True
        and observed_stderr_empty
        and (attempt_dir / "verdict.json").exists()
    ):
        try:
            _parse_verdict(attempt_dir / "verdict.json", expected_ids)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        else:
            raise PanelProtocolError(f"valid panel vote was recorded as failed: {attempt_dir}")
    expected_retryable = (
        status == "failed"
        and _retryable_provider_failure(
            returncode=receipt.get("returncode"),
            error=receipt.get("error"),
            event_integrity=observed_event_integrity,
            stderr_empty=observed_stderr_empty,
            verdict_exists=(attempt_dir / "verdict.json").exists(),
        )
    )
    if receipt.get("retryable_provider_failure") is not expected_retryable:
        raise PanelProtocolError(f"panel retry classification changed: {attempt_dir}")
    return receipt, result


def collect_panel_state(
    out_dir: Path,
    *,
    manifest: dict[str, Any],
    blinded_items: list[dict[str, Any]],
) -> tuple[dict[tuple[int, int], list[dict[str, Any]]], dict[str, list[bool]]]:
    batches = expected_batches(blinded_items)
    votes = {item["opaque_id"]: [] for item in blinded_items}
    accepted_rounds = {item["opaque_id"]: [] for item in blinded_items}
    for (vote_round, batch_number), batch in batches.items():
        attempts = _attempt_dirs(out_dir, vote_round, batch_number)
        if len(attempts) > MAX_OPERATIONAL_ATTEMPTS_PER_BATCH:
            raise PanelProtocolError("panel batch exceeds its registered attempt cap")
        accepted_result: dict[str, bool] | None = None
        accepted_index: int | None = None
        for index, attempt_dir in enumerate(attempts):
            receipt, result = _validate_attempt_receipt(
                attempt_dir,
                manifest=manifest,
                vote_round=vote_round,
                batch_number=batch_number,
                batch=batch,
            )
            if result is not None:
                if accepted_result is not None:
                    raise PanelProtocolError("panel batch has multiple accepted attempts")
                accepted_result = result
                accepted_index = index
            elif receipt.get("retryable_provider_failure") is not True:
                raise PanelProtocolError(
                    f"nonretryable panel failure is a hard stop: {attempt_dir}"
                )
        if accepted_index is not None and accepted_index != len(attempts) - 1:
            raise PanelProtocolError("panel batch has attempts after an accepted vote")
        if accepted_result is not None:
            for opaque_id in [item["opaque_id"] for item in batch]:
                votes[opaque_id].append(accepted_result[opaque_id])
                accepted_rounds[opaque_id].append(vote_round)

    for opaque_id, item_votes in votes.items():
        if len(item_votes) > REGISTERED_VOTES:
            raise PanelProtocolError(f"too many panel votes for {opaque_id}")
        if accepted_rounds[opaque_id] != list(range(len(item_votes))):
            raise PanelProtocolError(f"panel vote rounds are not contiguous for {opaque_id}")
    return batches, votes


def _write_final_outputs(
    out_dir: Path,
    *,
    manifest: dict[str, Any],
    blinded_items: list[dict[str, Any]],
    votes: dict[str, list[bool]],
) -> None:
    verdicts, final_manifest = _derived_final_outputs(
        out_dir,
        manifest=manifest,
        blinded_items=blinded_items,
        votes=votes,
    )
    _write_json_atomic(out_dir / "panel_verdicts.json", verdicts)
    _write_json_atomic(out_dir / "panel_verdicts.manifest.json", final_manifest)
    for path in sorted(out_dir.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    out_dir.chmod(0o555)


def _derived_final_outputs(
    out_dir: Path,
    *,
    manifest: dict[str, Any],
    blinded_items: list[dict[str, Any]],
    votes: dict[str, list[bool]],
) -> tuple[dict[str, int], dict[str, Any]]:
    if any(len(item_votes) != REGISTERED_VOTES for item_votes in votes.values()):
        raise PanelProtocolError("cannot finalize an incomplete A11 panel")
    hosts = {item["opaque_id"]: item["host"] for item in blinded_items}
    verdicts = {
        f"{hosts[opaque_id]['arm']}|{hosts[opaque_id]['question_id']}": int(
            sum(item_votes) * 2 > len(item_votes)
        )
        for opaque_id, item_votes in votes.items()
    }
    verdicts = dict(sorted(verdicts.items()))
    expected_attempt_dirs = [
        attempt_dir
        for vote_batch in expected_batches(blinded_items)
        for attempt_dir in _attempt_dirs(out_dir, *vote_batch)
    ]
    attempt_receipts = sorted(
        attempt_dir / "receipt.json" for attempt_dir in expected_attempt_dirs
    )
    discovered_receipts = sorted((out_dir / "attempts").rglob("receipt.json"))
    if attempt_receipts != discovered_receipts:
        raise PanelProtocolError("panel attempt inventory contains an unregistered receipt")
    receipts = [_read_json(path) for path in attempt_receipts]
    final_manifest = {
        "panel_manifest_sha256": sha256_file(out_dir / "manifest.json"),
        "controller_manifest_sha256": manifest["controller_manifest_sha256"],
        "queue_sha256": manifest["queue_sha256"],
        "judge_config_sha256": manifest["judge_config_sha256"],
        "attempt_receipt_sha256": {
            str(path.relative_to(out_dir)): sha256_file(path) for path in attempt_receipts
        },
        "verdicts_sha256": sha256_json(verdicts),
        "verdict_count": len(verdicts),
        "panel_token_usage": panel_grade.panel_token_summary(
            {"usage_receipts": receipts}
        ),
    }
    return verdicts, final_manifest


def _validate_final_outputs(
    out_dir: Path,
    *,
    expected_verdicts: dict[str, int],
    expected_manifest: dict[str, Any],
) -> None:
    if (
        _read_json(out_dir / "panel_verdicts.json") != expected_verdicts
        or _read_json(out_dir / "panel_verdicts.manifest.json") != expected_manifest
    ):
        raise PanelProtocolError("A11 panel final outputs differ from replayed votes")


def audit_completed_panel(
    *,
    queue_path: Path,
    controller_manifest: Path,
    expected_controller_sha256: str,
    out_dir: Path,
) -> dict[str, Any]:
    """Replay every sealed attempt and derive the exact majority result."""

    controller_sha256, codex, registered_panel_output = load_controller_codex_identity(
        controller_manifest.resolve(),
        expected_controller_sha256=expected_controller_sha256,
    )
    out_dir = out_dir.resolve()
    if out_dir != registered_panel_output:
        raise ValueError("out_dir differs from the registered panel output")
    validate_registered_grading_queue(
        controller_manifest=controller_manifest.resolve(),
        controller_manifest_sha256=controller_sha256,
        queue_path=queue_path,
    )
    queue_bytes, queue = load_a11_queue(queue_path)
    judge_config = build_judge_config(
        controller_manifest_sha256=controller_sha256,
        codex=codex,
    )
    blinded = prepare_blinded_items(queue, judge_config)
    identity = build_manifest_identity(
        controller_manifest_sha256=controller_sha256,
        queue_sha256=sha256_bytes(queue_bytes),
        judge_config=judge_config,
        blinded_items=blinded,
    )
    with acquire_single_instance(panel_lock_path(out_dir)):
        manifest = _read_json(out_dir / "manifest.json")
        if manifest != identity:
            raise PanelProtocolError("immutable A11 panel manifest changed")
        _batches, votes = collect_panel_state(
            out_dir, manifest=manifest, blinded_items=blinded
        )
        verdicts, final_manifest = _derived_final_outputs(
            out_dir,
            manifest=manifest,
            blinded_items=blinded,
            votes=votes,
        )
        _validate_final_outputs(
            out_dir,
            expected_verdicts=verdicts,
            expected_manifest=final_manifest,
        )
    return {
        "schema_version": "a11-panel-replay-audit-v1",
        "controller_manifest_sha256": controller_sha256,
        "queue_sha256": sha256_bytes(queue_bytes),
        "items": len(blinded),
        "votes_per_item": REGISTERED_VOTES,
        "verdicts": verdicts,
        "verdicts_sha256": sha256_json(verdicts),
        "panel_token_usage": final_manifest["panel_token_usage"],
        "attempt_receipt_sha256": final_manifest["attempt_receipt_sha256"],
        "all_checks_passed": True,
    }


def run_panel(
    *,
    queue_path: Path,
    controller_manifest: Path,
    expected_controller_sha256: str,
    out_dir: Path,
    live: bool,
    run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    controller_manifest = controller_manifest.resolve()
    controller_sha256, codex, registered_panel_output = load_controller_codex_identity(
        controller_manifest,
        expected_controller_sha256=expected_controller_sha256,
    )
    out_dir = out_dir.resolve()
    if out_dir != registered_panel_output:
        raise ValueError("out_dir differs from the registered panel output")
    validate_registered_grading_queue(
        controller_manifest=controller_manifest,
        controller_manifest_sha256=controller_sha256,
        queue_path=queue_path,
    )
    queue_bytes, queue = load_a11_queue(queue_path)
    judge_config = build_judge_config(
        controller_manifest_sha256=controller_sha256,
        codex=codex,
    )
    blinded = prepare_blinded_items(queue, judge_config)
    identity = build_manifest_identity(
        controller_manifest_sha256=controller_sha256,
        queue_sha256=sha256_bytes(queue_bytes),
        judge_config=judge_config,
        blinded_items=blinded,
    )

    if not live:
        if (out_dir / "manifest.json").exists():
            initialize_or_validate_bundle(
                out_dir, identity=identity, queue_bytes=queue_bytes
            )
            manifest = identity
            _batches, votes = collect_panel_state(
                out_dir, manifest=manifest, blinded_items=blinded
            )
        else:
            votes = {item["opaque_id"]: [] for item in blinded}
        return {
            "items": len(blinded),
            "fully_voted": sum(
                len(item_votes) == REGISTERED_VOTES for item_votes in votes.values()
            ),
            "live": False,
            "manifest_sha256": sha256_json(identity),
        }

    with acquire_single_instance(panel_lock_path(out_dir)):
        manifest = initialize_or_validate_bundle(
            out_dir, identity=identity, queue_bytes=queue_bytes
        )
        batches, votes = collect_panel_state(
            out_dir, manifest=manifest, blinded_items=blinded
        )
        for (vote_round, batch_number), batch in sorted(batches.items()):
            opaque_ids = [item["opaque_id"] for item in batch]
            if all(len(votes[opaque_id]) > vote_round for opaque_id in opaque_ids):
                continue
            attempts = _attempt_dirs(out_dir, vote_round, batch_number)
            if len(attempts) >= MAX_OPERATIONAL_ATTEMPTS_PER_BATCH:
                raise PanelProtocolError(
                    f"panel retry cap reached for vote={vote_round} batch={batch_number}"
                )
            outcome = execute_attempt(
                attempt_dir=_attempt_root(out_dir, vote_round, batch_number)
                / f"attempt-{len(attempts) + 1:03d}",
                batch=batch,
                vote_round=vote_round,
                batch_number=batch_number,
                attempt_number=len(attempts) + 1,
                manifest=manifest,
                codex=codex,
                run_process=run_process,
            )
            if not outcome.accepted:
                if outcome.receipt.get("retryable_provider_failure") is not True:
                    raise PanelProtocolError(
                        "nonretryable panel failure is a hard stop"
                    )
                return {
                    "items": len(blinded),
                    "fully_voted": sum(
                        len(item_votes) == REGISTERED_VOTES
                        for item_votes in votes.values()
                    ),
                    "live": True,
                    "status": "operational_attempt_failed",
                    "vote_round": vote_round,
                    "batch_number": batch_number,
                    "attempt_number": len(attempts) + 1,
                }
            for opaque_id, value in outcome.result.items():
                votes[opaque_id].append(value)

        expected_verdicts, expected_final_manifest = _derived_final_outputs(
            out_dir,
            manifest=manifest,
            blinded_items=blinded,
            votes=votes,
        )
        if (out_dir / "panel_verdicts.manifest.json").exists():
            _validate_final_outputs(
                out_dir,
                expected_verdicts=expected_verdicts,
                expected_manifest=expected_final_manifest,
            )
        else:
            _write_final_outputs(
                out_dir,
                manifest=manifest,
                blinded_items=blinded,
                votes=votes,
            )
        return {
            "items": len(blinded),
            "fully_voted": len(blinded),
            "live": True,
            "status": "complete",
            "verdicts": str(out_dir / "panel_verdicts.json"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--controller-manifest", type=Path, required=True)
    parser.add_argument("--expected-controller-sha256", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    try:
        if args.audit:
            if args.live:
                parser.error("--audit and --live are mutually exclusive")
            result = audit_completed_panel(
                queue_path=args.queue,
                controller_manifest=args.controller_manifest,
                expected_controller_sha256=args.expected_controller_sha256,
                out_dir=args.out_dir,
            )
        else:
            result = run_panel(
                queue_path=args.queue,
                controller_manifest=args.controller_manifest,
                expected_controller_sha256=args.expected_controller_sha256,
                out_dir=args.out_dir,
                live=args.live,
            )
    except AlreadyRunning as exc:
        print(f"ALREADY_RUNNING: {exc}")
        return LOCK_BUSY_EXIT
    except (OSError, ValueError, PanelProtocolError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("status") == "operational_attempt_failed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
