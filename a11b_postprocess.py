#!/usr/bin/env python3
"""Trusted post-answer export, grading, witnessed panel, and finalization for A11b."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import a11b_grading
import experiment_executor
import experiment_executor_service as service
import experiment_witness as witness
import paired_stats
import panel_grade
import run_a11b_panel
import run_lock
import trusted_codex_driver


EXPORT_SCHEMA_VERSION = "a11b-answer-export-v1"
GRADING_SCHEMA_VERSION = "a11-grading-preparation-v1"
PANEL_SCHEMA_VERSION = "a11b-witnessed-panel-v1"
FINAL_SCHEMA_VERSION = "a11b-final-result-manifest-v1"
PANEL_WITNESS_IDENTITY = "a11b-panel-witness-2026-07-16"
FIXED_AUDIT_ROOT = service.PRODUCTION_BUNDLE_DIR / "audit-input"


class PostprocessError(ValueError):
    """A sealed post-answer artifact or derivation failed validation."""


def _canonical(value: object) -> bytes:
    return service.canonical_json_line(value)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _receipt(payload: bytes) -> dict[str, Any]:
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def _read_json(path: Path) -> Any:
    payload = path.read_bytes()
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostprocessError(f"invalid JSON artifact: {path.name}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line:
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PostprocessError(
                f"invalid JSONL artifact: {path.name}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise PostprocessError(f"non-object JSONL row: {path.name}:{line_number}")
        rows.append(value)
    return rows


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_directory(root: Path, artifacts: Mapping[str, bytes]) -> dict[str, Any]:
    if root.exists() or root.is_symlink():
        raise PostprocessError(f"immutable output already exists: {root.name}")
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.publish-", dir=root.parent))
    temporary.chmod(0o700)
    receipts: dict[str, Any] = {}
    try:
        for name, payload in sorted(artifacts.items()):
            _write_exclusive(temporary / name, payload)
            receipts[name] = _receipt(payload)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.rename(temporary, root)
        parent = os.open(root.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return receipts


def _publish_with_manifest(
    root: Path,
    artifacts: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    receipts = {name: _receipt(payload) for name, payload in artifacts.items()}
    complete = {**dict(manifest), "artifacts": receipts}
    _publish_directory(
        root,
        {**dict(artifacts), "manifest.json": _canonical(complete)},
    )
    return complete


def _load_controller(path: Path) -> tuple[dict[str, Any], str]:
    payload = path.read_bytes()
    digest = _sha256(payload)
    sidecar = path.with_suffix(".sha256")
    if sidecar.read_text(encoding="ascii") != digest + "\n":
        raise PostprocessError("controller sidecar changed")
    value = json.loads(payload)
    profile = value.get("experiment_profile") if isinstance(value, dict) else None
    registered_shape = (
        profile == "a11b-causal-isolation-v2"
        and value.get("inputs", {}).get("question_count") == 384
        and value.get("inputs", {}).get("answer_calls") == 1152
        and set(value.get("outputs", {}))
        == {"answer_export", "grading", "panel", "result"}
    ) or (
        profile == "a11b-successor-development-v1"
        and value.get("inputs", {}).get("question_count") == 64
        and value.get("inputs", {}).get("answer_calls") == 192
        and set(value.get("outputs", {})) == {"result"}
    )
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "a11-controller-v4"
        or not registered_shape
        or value.get("schedule", {}).get("arms") != ["t0", "t1", "e1"]
        or len(value.get("schedule", {}).get("items", []))
        != value.get("inputs", {}).get("answer_calls")
    ):
        raise PostprocessError("controller is not the registered A11b controller")
    outputs = value.get("outputs")
    if not isinstance(outputs, dict):
        raise PostprocessError("controller output inventory changed")
    for raw in outputs.values():
        candidate = Path(raw)
        if not candidate.is_absolute() or candidate.resolve() != candidate:
            raise PostprocessError("controller output path is not absolute")
    return value, digest


def _verify_installed_postprocess_sources(
    *, controller: Mapping[str, Any], bundle_root: Path
) -> None:
    """Fail closed unless every grading dependency matches its sealed snapshot."""

    modules = {
        "a11_grading": Path(a11b_grading.__file__).resolve(),
        "a11b_postprocess": Path(__file__).resolve(),
        "paired_stats": Path(paired_stats.__file__).resolve(),
        "panel_grade": Path(panel_grade.__file__).resolve(),
        "run_a11_panel": Path(run_a11b_panel.__file__).resolve(),
        "run_lock": Path(run_lock.__file__).resolve(),
    }
    if controller.get("experiment_profile") == "a11b-successor-development-v1":
        import a11_evidence_core
        import a11b_answer_contract
        import a11b_successor_dev_gate
        import a11b_successor_development_grading
        import a11b_successor_development_postprocess

        modules = {
            "a11_evidence_core": Path(a11_evidence_core.__file__).resolve(),
            "a11b_answer_contract": Path(a11b_answer_contract.__file__).resolve(),
            "a11b_postprocess": Path(__file__).resolve(),
            "a11b_successor_dev_gate": Path(
                a11b_successor_dev_gate.__file__
            ).resolve(),
            "a11b_successor_development_grading": Path(
                a11b_successor_development_grading.__file__
            ).resolve(),
            "a11b_successor_development_postprocess": Path(
                a11b_successor_development_postprocess.__file__
            ).resolve(),
            "run_lock": Path(run_lock.__file__).resolve(),
        }
    snapshots = controller.get("snapshots")
    if not isinstance(snapshots, dict) or not set(modules).issubset(snapshots):
        raise PostprocessError("sealed postprocess source inventory changed")
    for logical_name, installed_path in sorted(modules.items()):
        receipt = snapshots[logical_name]
        snapshot_path = bundle_root / "snapshots" / Path(
            str(receipt.get("snapshot_path", ""))
        ).name
        if not installed_path.is_file() or not snapshot_path.is_file():
            raise PostprocessError(f"sealed {logical_name} source is missing")
        installed = installed_path.read_bytes()
        snapshot = snapshot_path.read_bytes()
        expected = {
            "sha256": receipt.get("sha256"),
            "bytes": receipt.get("bytes"),
        }
        if _receipt(installed) != expected or installed != snapshot:
            raise PostprocessError(f"sealed {logical_name} source changed")


def _decode_artifact(row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    encoded = row.get("artifact_base64")
    if not isinstance(encoded, str):
        raise PostprocessError("executor export has no artifact bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise PostprocessError("executor export artifact encoding changed") from exc
    if _receipt(raw) != {
        "sha256": row.get("artifact_sha256"),
        "bytes": row.get("artifact_bytes"),
    }:
        raise PostprocessError("executor export artifact receipt changed")
    artifact = json.loads(raw)
    capture = artifact.get("capture") if isinstance(artifact, dict) else None
    encoded_files = capture.get("files_base64") if isinstance(capture, dict) else None
    if not isinstance(encoded_files, dict):
        raise PostprocessError("executor export capture changed")
    files: dict[str, bytes] = {}
    for name, value in encoded_files.items():
        if name not in {"answer.json", "events.jsonl", "stderr.log"} or not isinstance(
            value, str
        ):
            raise PostprocessError("executor export capture inventory changed")
        files[name] = base64.b64decode(value, validate=True)
    if set(files) != {"answer.json", "events.jsonl", "stderr.log"}:
        raise PostprocessError("accepted executor export is incomplete")
    if files["stderr.log"]:
        raise PostprocessError("accepted executor stderr is not empty")
    return artifact, files


def prepare_answer_export(
    *,
    controller_path: Path,
    trusted_executor: experiment_executor.ExperimentExecutor,
) -> dict[str, Any]:
    controller, controller_sha = _load_controller(controller_path)
    exported = trusted_executor.export_completed_run()
    if (
        exported.get("run_id") != controller.get("run_id")
        or exported.get("schedule_length") != 1152
        or exported.get("accepted_slots") != 1152
    ):
        raise PostprocessError("completed answer export differs from controller")
    schedule = controller["schedule"]["items"]
    attempts = exported.get("attempts")
    if not isinstance(attempts, list):
        raise PostprocessError("completed answer attempt export changed")
    by_slot: dict[int, list[dict[str, Any]]] = {}
    for row in attempts:
        descriptor = row.get("descriptor") if isinstance(row, dict) else None
        index = descriptor.get("schedule_index") if isinstance(descriptor, dict) else None
        if type(index) is not int or not 0 <= index < 1152:
            raise PostprocessError("answer export schedule index changed")
        by_slot.setdefault(index, []).append(row)
    if set(by_slot) != set(range(1152)):
        raise PostprocessError("answer export slot coverage is incomplete")

    schema_sha = controller["grading"]["answer_schema_sha256"]
    completion_receipts: list[dict[str, Any]] = []
    accepted_answers: list[dict[str, Any]] = []
    all_attempts: list[dict[str, Any]] = []
    for index, host in enumerate(schedule):
        slot = sorted(
            by_slot[index], key=lambda row: row["descriptor"]["attempt_number"]
        )
        for row in slot:
            all_attempts.append(
                {
                    "schedule_index": index,
                    "arm": host["arm"],
                    "question_id": host["question_id"],
                    "attempt_number": row["descriptor"]["attempt_number"],
                    "outcome": row["outcome"],
                    "token_usage": row["token_usage"],
                    "artifact_root_commitment": row["artifact_root_commitment"],
                    "witness_head": row["witness_head"],
                }
            )
        accepted = slot[-1]
        if accepted.get("outcome") != "accepted":
            raise PostprocessError("completed answer slot is not accepted")
        artifact, files = _decode_artifact(accepted)
        answer = json.loads(files["answer.json"])
        if not isinstance(answer, dict):
            raise PostprocessError("accepted answer is not an object")
        attempt_number = accepted["descriptor"]["attempt_number"]
        receipt = {
            "kind": a11b_grading.COMPLETION_KIND,
            "schema_version": a11b_grading.COMPLETION_SCHEMA_VERSION,
            "controller_manifest_sha256": controller_sha,
            "arm": host["arm"],
            "question_id": host["question_id"],
            "status": "answered",
            "attempt_number": attempt_number,
            "answer_sha256": _sha256(files["answer.json"]),
            "event_log_sha256": _sha256(files["events.jsonl"]),
            "prompt_sha256": host["prompt_sha256"],
            "stderr_log_sha256": _sha256(files["stderr.log"]),
            "model_input_sha256": host["prompt_sha256"],
            "schema_sha256": schema_sha,
        }
        completion_receipts.append(receipt)
        accepted_answers.append(
            {
                "schedule_index": index,
                "arm": host["arm"],
                "question_id": host["question_id"],
                "answer": answer,
                "completion_receipt": receipt,
                "token_usage": accepted["token_usage"],
                "artifact_root_commitment": accepted["artifact_root_commitment"],
                "artifact_kind": artifact["kind"],
            }
        )
    question_ids = list(dict.fromkeys(item["question_id"] for item in schedule))
    coverage = {
        "schema_version": a11b_grading.COMPLETION_COVERAGE_VERSION,
        "controller_manifest_sha256": controller_sha,
        "question_ids": question_ids,
        "arms": list(a11b_grading.ARMS),
        "receipts": completion_receipts,
    }
    accepted_by_host = {
        (row["arm"], row["question_id"]): row for row in accepted_answers
    }
    a11b_grading.prove_exact_completion_coverage(
        coverage,
        receipt_validator=lambda receipt: (
            accepted_by_host.get((receipt["arm"], receipt["question_id"]), {}).get(
                "completion_receipt"
            )
            == receipt
        ),
    )
    export_document = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "controller_manifest_sha256": controller_sha,
        "run_id": exported["run_id"],
        "final_witness_head": exported["witness_head"],
        "accepted_answers": accepted_answers,
        "all_attempts": all_attempts,
        "signed_witness_receipts": exported["signed_receipts"],
        "model_calls_reserved": exported["model_calls_reserved"],
        "model_calls_closed": exported["model_calls_closed"],
        "gold_opened": False,
    }
    export_bytes = _canonical(export_document)
    coverage_bytes = _canonical(coverage)
    output = Path(controller["outputs"]["answer_export"])
    artifacts = {
        "completion_coverage.json": coverage_bytes,
        "export.json": export_bytes,
    }
    manifest = _publish_with_manifest(output, artifacts, {
        "schema_version": "a11b-answer-export-manifest-v1",
        "controller_manifest_sha256": controller_sha,
        "run_id": exported["run_id"],
        "final_witness_head": exported["witness_head"],
        "accepted_answers": 1152,
        "all_attempts": len(all_attempts),
        "gold_opened": False,
        "all_checks_passed": True,
    })
    return manifest


def _verified_export(controller: Mapping[str, Any], controller_sha: str) -> tuple[
    dict[str, Any], dict[str, Any]
]:
    root = Path(controller["outputs"]["answer_export"])
    manifest = _read_json(root / "manifest.json")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "a11b-answer-export-manifest-v1"
        or manifest.get("controller_manifest_sha256") != controller_sha
        or manifest.get("accepted_answers") != 1152
        or manifest.get("gold_opened") is not False
        or manifest.get("all_checks_passed") is not True
    ):
        raise PostprocessError("answer export manifest changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "completion_coverage.json",
        "export.json",
    }:
        raise PostprocessError("answer export artifact inventory changed")
    for name, receipt in artifacts.items():
        payload = (root / name).read_bytes()
        if _receipt(payload) != receipt:
            raise PostprocessError(f"answer export artifact changed: {name}")
    coverage = _read_json(root / "completion_coverage.json")
    export = _read_json(root / "export.json")
    if export.get("gold_opened") is not False:
        raise PostprocessError("answer export claims gold was opened")
    accepted = {
        (row["arm"], row["question_id"]): row
        for row in export.get("accepted_answers", [])
        if isinstance(row, dict)
    }
    a11b_grading.prove_exact_completion_coverage(
        coverage,
        receipt_validator=lambda receipt: (
            accepted.get((receipt["arm"], receipt["question_id"]), {}).get(
                "completion_receipt"
            )
            == receipt
        ),
    )
    return coverage, export


def _verify_audit_tree(root: Path, expected_sha256: str) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise PostprocessError("audit root is unavailable")
    manifest_payload = (root / "manifest.json").read_bytes()
    if _sha256(manifest_payload) != expected_sha256:
        raise PostprocessError("audit manifest differs from the controller")
    if (root / "manifest.sha256").read_text(encoding="ascii") != expected_sha256 + "\n":
        raise PostprocessError("audit manifest sidecar changed")
    manifest = json.loads(manifest_payload)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict):
        raise PostprocessError("audit manifest artifact inventory changed")
    expected_files = {"manifest.json", "manifest.sha256", *artifacts}
    observed_files: set[str] = set()
    for path in root.rglob("*"):
        status = path.lstat()
        if path.is_symlink() or (
            not stat.S_ISDIR(status.st_mode) and not stat.S_ISREG(status.st_mode)
        ):
            raise PostprocessError("audit tree contains an unsafe entry")
        if stat.S_ISREG(status.st_mode):
            observed_files.add(path.relative_to(root).as_posix())
    if observed_files != expected_files:
        raise PostprocessError("audit tree inventory changed")
    for name, receipt in artifacts.items():
        payload = (root / name).read_bytes()
        if _receipt(payload) != receipt:
            raise PostprocessError(f"audit artifact changed: {name}")
    return manifest


def _questions_from_snapshot(controller: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    snapshot = controller["snapshots"]["answer_input"]["snapshot_path"]
    with Path(snapshot).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    by_id = {row["question_id"]: dict(row) for row in rows}
    if len(by_id) != 384:
        raise PostprocessError("sealed efficacy question coverage changed")
    return by_id


def _answer_economics(export: Mapping[str, Any]) -> dict[str, Any]:
    accepted_totals = {arm: {} for arm in a11b_grading.ARMS}
    all_totals = {arm: {} for arm in a11b_grading.ARMS}
    retries = {arm: 0 for arm in a11b_grading.ARMS}
    recovered = {arm: 0 for arm in a11b_grading.ARMS}
    accepted_attempts = {arm: 0 for arm in a11b_grading.ARMS}
    all_attempts = {arm: 0 for arm in a11b_grading.ARMS}
    unknown_usage_attempts = {arm: 0 for arm in a11b_grading.ARMS}
    for row in export["all_attempts"]:
        arm = row["arm"]
        all_attempts[arm] += 1
        usage = row["token_usage"]
        usage_complete = usage.get("complete") is True and all(
            type(usage.get(key)) is int
            for key in ("input", "cached", "output", "reasoning", "total")
        )
        if not usage_complete:
            unknown_usage_attempts[arm] += 1
        for key in ("input", "cached", "output", "reasoning", "total"):
            value = usage.get(key)
            if type(value) is int:
                all_totals[arm][key] = all_totals[arm].get(key, 0) + value
        if row["outcome"] == "accepted":
            accepted_attempts[arm] += 1
            for key in ("input", "cached", "output", "reasoning", "total"):
                value = usage.get(key)
                if type(value) is int:
                    accepted_totals[arm][key] = accepted_totals[arm].get(key, 0) + value
            if row["attempt_number"] > 1:
                recovered[arm] += 1
        else:
            retries[arm] += 1
    return {
        "accepted_token_usage_by_arm": accepted_totals,
        "all_attempt_token_usage_by_arm": all_totals,
        "attempts_by_arm": {
            arm: {"accepted": accepted_attempts[arm], "all": all_attempts[arm]}
            for arm in a11b_grading.ARMS
        },
        "retry_yield_by_arm": {
            arm: {
                "retry_attempts": retries[arm],
                "questions_recovered_after_retry": recovered[arm],
            }
            for arm in a11b_grading.ARMS
        },
        "unknown_usage_attempts_by_arm": unknown_usage_attempts,
        "all_attempt_token_economics_reconciled": not any(
            unknown_usage_attempts.values()
        ),
        "all_attempt_token_usage_is_lower_bound": any(
            unknown_usage_attempts.values()
        ),
    }


def prepare_grading(
    *, controller_path: Path, audit_root: Path = FIXED_AUDIT_ROOT
) -> dict[str, Any]:
    controller, controller_sha = _load_controller(controller_path)
    coverage, export = _verified_export(controller, controller_sha)
    accepted = {
        (row["arm"], row["question_id"]): row
        for row in export["accepted_answers"]
    }
    # The exact completion proof above is deliberately before the first audit read.
    audit_manifest = _verify_audit_tree(
        audit_root,
        controller["inputs"]["audit_manifest_sha256"],
    )
    gold_rows = _read_jsonl(audit_root / "efficacy/gold.jsonl")
    audit_rows = _read_jsonl(audit_root / "efficacy/audit.jsonl")
    gold = a11b_grading.load_gold_after_completion(
        coverage,
        gold_loader=lambda: gold_rows,
        receipt_validator=lambda receipt: (
            accepted.get((receipt["arm"], receipt["question_id"]), {}).get(
                "completion_receipt"
            )
            == receipt
        ),
    )
    questions = _questions_from_snapshot(controller)
    audit_by_id = {row["question_id"]: row for row in audit_rows}
    if set(questions) != set(gold) or set(audit_by_id) != set(gold):
        raise PostprocessError("audit/public question coverage changed")
    for question_id in questions:
        questions[question_id].update(
            {
                "patient_cluster_sha256": gold[question_id][
                    "patient_cluster_sha256"
                ],
                "family": audit_by_id[question_id]["family"],
                "depth": audit_by_id[question_id]["depth"],
                "difficulty": audit_by_id[question_id]["difficulty"],
                "temporal_policy": audit_by_id[question_id]["temporal_policy"],
            }
        )

    deterministic = {arm: {} for arm in a11b_grading.ARMS}
    panel_queue: list[dict[str, Any]] = []
    for question_id in coverage["question_ids"]:
        for arm in a11b_grading.ARMS:
            answer = accepted[(arm, question_id)]["answer"]
            verdict, panel_item = a11b_grading.deterministic_partition(
                question=questions[question_id],
                gold=gold[question_id],
                answer=answer,
            )
            if verdict is not None:
                deterministic[arm][question_id] = verdict
            elif panel_item is not None:
                panel_queue.append({"arm": arm, **panel_item})
            else:
                raise PostprocessError("grading partition produced no label")
    coverage_bytes = _canonical(coverage)
    deterministic_bytes = _canonical(deterministic)
    queue_bytes = b"".join(_canonical(row) for row in panel_queue)
    output = Path(controller["outputs"]["grading"])
    artifacts = {
        "completion_coverage.json": coverage_bytes,
        "deterministic_labels.json": deterministic_bytes,
        "panel_queue.jsonl": queue_bytes,
    }
    manifest = _publish_with_manifest(output, artifacts, {
        "schema_version": GRADING_SCHEMA_VERSION,
        "controller_manifest_sha256": controller_sha,
        "model_calls": 0,
        "completed_answers": 1152,
        "deterministic_labels": sum(len(rows) for rows in deterministic.values()),
        "panel_items": len(panel_queue),
        "panel_config": controller["grading"]["panel"],
        "answer_economics": _answer_economics(export),
        "audit_manifest_sha256": _sha256(
            (audit_root / "manifest.json").read_bytes()
        ),
        "audit_artifact_count": len(audit_manifest["artifacts"]),
        "all_checks_passed": True,
    })
    return manifest


def _panel_schema_bytes() -> bytes:
    return _canonical(run_a11b_panel.BATCH_SCHEMA)


def _panel_attempt_answer(row: Mapping[str, Any]) -> dict[str, bool]:
    _artifact, files = _decode_artifact(row)
    value = json.loads(files["answer.json"])
    verdicts = value.get("verdicts") if isinstance(value, dict) else None
    if not isinstance(verdicts, list):
        raise PostprocessError("panel verdict document changed")
    result: dict[str, bool] = {}
    for verdict in verdicts:
        if (
            not isinstance(verdict, dict)
            or set(verdict) != {"item_id", "correct"}
            or not isinstance(verdict.get("item_id"), str)
            or type(verdict.get("correct")) is not bool
            or verdict["item_id"] in result
        ):
            raise PostprocessError("panel verdict item changed")
        result[verdict["item_id"]] = verdict["correct"]
    return result


def run_witnessed_panel(
    *,
    controller_path: Path,
    bundle_root: Path = service.PRODUCTION_BUNDLE_DIR,
    driver: experiment_executor.ModelDriver | None = None,
) -> dict[str, Any]:
    controller, controller_sha = _load_controller(controller_path)
    grading_root = Path(controller["outputs"]["grading"])
    grading_manifest = _read_json(grading_root / "manifest.json")
    if (
        grading_manifest.get("controller_manifest_sha256") != controller_sha
        or grading_manifest.get("all_checks_passed") is not True
    ):
        raise PostprocessError("grading manifest is not complete")
    queue_path = grading_root / "panel_queue.jsonl"
    queue_bytes = queue_path.read_bytes()
    output = Path(controller["outputs"]["panel"])
    if not queue_bytes:
        verdicts_bytes = _canonical({})
        artifacts = {"panel_verdicts.json": verdicts_bytes}
        manifest = _publish_with_manifest(output, artifacts, {
            "schema_version": PANEL_SCHEMA_VERSION,
            "controller_manifest_sha256": controller_sha,
            "queue_sha256": _sha256(queue_bytes),
            "panel_items": 0,
            "model_calls_reserved": 0,
            "model_calls_closed": 0,
            "token_usage": {
                "accepted": {},
                "all_attempts": {},
                "unknown_usage_attempts": 0,
                "all_attempts_reconciled": True,
                "all_attempts_are_lower_bound": False,
            },
            "all_checks_passed": True,
        })
        return manifest

    raw_queue, queue = run_a11b_panel.load_a11_queue(queue_path)
    codex = run_a11b_panel.CodexIdentity(
        path=Path(controller["grading"]["panel"]["codex_bin"]),
        version=controller["grading"]["panel"]["codex_version"],
        sha256=controller["grading"]["panel"]["codex_binary_sha256"],
    )
    judge_config = run_a11b_panel.build_judge_config(
        controller_manifest_sha256=controller_sha,
        codex=codex,
    )
    blinded = run_a11b_panel.prepare_blinded_items(queue, judge_config)
    batches = run_a11b_panel.expected_batches(blinded)
    schema_bytes = _panel_schema_bytes()
    runtime_sha = codex.sha256
    invocations: list[experiment_executor.SealedInvocation] = []
    index_to_batch: dict[int, tuple[int, int, list[dict[str, Any]]]] = {}
    for schedule_index, ((vote_round, batch_number), batch) in enumerate(
        batches.items()
    ):
        invocation = experiment_executor.SealedInvocation(
            phase="panel",
            schedule_index=schedule_index,
            prompt=run_a11b_panel.batch_prompt(batch).encode("utf-8"),
            output_schema=schema_bytes,
            model=a11b_grading.PANEL_MODEL,
            reasoning_effort=a11b_grading.PANEL_EFFORT,
            runtime_path=str(codex.path),
            runtime_sha256=runtime_sha,
            timeout_seconds=a11b_grading.PANEL_TIMEOUT_SECONDS,
        )
        invocations.append(invocation)
        index_to_batch[schedule_index] = (vote_round, batch_number, batch)
    commitment_key = (bundle_root / "commitment.key").read_bytes()
    authenticator = witness.SshEd25519Authenticator(
        private_key_path=bundle_root / "witness_ed25519",
        identity=PANEL_WITNESS_IDENTITY,
    )
    schedule = tuple(
        witness.ScheduleItem(
            phase=invocation.phase,
            schedule_index=invocation.schedule_index,
            call_commitment=invocation.call_commitment(commitment_key),
            max_attempts=3,
        )
        for invocation in invocations
    )
    panel_run_id = _sha256(
        _canonical(
            {
                "schema_version": "a11b-derived-panel-run-v1",
                "controller_manifest_sha256": controller_sha,
                "queue_sha256": _sha256(raw_queue),
                "judge_config": judge_config,
                "schedule": [
                    {
                        "phase": item.phase,
                        "schedule_index": item.schedule_index,
                        "call_commitment": item.call_commitment,
                        "max_attempts": item.max_attempts,
                    }
                    for item in schedule
                ],
            }
        )
    )
    ledger = witness.WitnessLedger(
        bundle_root / "state/panel-witness",
        run_id=panel_run_id,
        schedule=schedule,
        authenticator=authenticator,
        clock=lambda: dt.datetime.now(dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    )
    if driver is None:
        sandbox = controller["execution"]["trusted_executor"]["sandbox"]
        driver = trusted_codex_driver.TrustedCodexDriver(
            account_home=bundle_root,
            codex_home=bundle_root / "codex-home",
            scratch_root=bundle_root / "scratch",
            sandbox_exec_path=Path(sandbox["path"]),
            sandbox_exec_sha256=sandbox["sha256"],
        )
    panel_executor = experiment_executor.ExperimentExecutor(
        bundle_root / "state/panel-executor",
        ledger=ledger,
        invocations=tuple(invocations),
        commitment_key=commitment_key,
        driver=driver,
    )
    while (status := ledger.status())["state"] != "complete":
        if status["state"] != "active":
            raise PostprocessError("witnessed panel reached a terminal unsafe state")
        result = panel_executor.execute_next(
            run_id=panel_run_id,
            expected_head=status["head"],
        )
        if result.outcome not in {"accepted", "provider_failure"}:
            raise PostprocessError("witnessed panel produced an unsafe outcome")
    exported = panel_executor.export_completed_run()
    by_slot: dict[int, list[dict[str, Any]]] = {}
    for row in exported["attempts"]:
        by_slot.setdefault(row["descriptor"]["schedule_index"], []).append(row)
    votes = {item["opaque_id"]: [] for item in blinded}
    for schedule_index in range(len(invocations)):
        accepted = sorted(
            by_slot[schedule_index],
            key=lambda row: row["descriptor"]["attempt_number"],
        )[-1]
        verdicts = _panel_attempt_answer(accepted)
        vote_round, _batch_number, batch = index_to_batch[schedule_index]
        expected_ids = [item["opaque_id"] for item in batch]
        if set(verdicts) != set(expected_ids):
            raise PostprocessError("panel verdict coverage changed")
        for opaque_id in expected_ids:
            if len(votes[opaque_id]) != vote_round:
                raise PostprocessError("panel vote ordering changed")
            votes[opaque_id].append(verdicts[opaque_id])
    hosts = {item["opaque_id"]: item["host"] for item in blinded}
    final_verdicts = {
        f"{hosts[opaque_id]['arm']}|{hosts[opaque_id]['question_id']}": int(
            sum(values) * 2 > len(values)
        )
        for opaque_id, values in sorted(votes.items())
    }
    if any(len(values) != 3 for values in votes.values()):
        raise PostprocessError("panel vote coverage is incomplete")
    verdict_bytes = _canonical(final_verdicts)
    witness_bytes = _canonical(exported)
    artifacts = {
        "panel_verdicts.json": verdict_bytes,
        "witnessed_panel_export.json": witness_bytes,
    }
    accepted_usage: dict[str, int] = {}
    all_usage: dict[str, int] = {}
    unknown_usage_attempts = 0
    for row in exported["attempts"]:
        usage = row["token_usage"]
        if usage.get("complete") is not True or any(
            type(usage.get(name)) is not int
            for name in ("input", "cached", "output", "reasoning", "total")
        ):
            unknown_usage_attempts += 1
        for name, value in row["token_usage"].items():
            if type(value) is int:
                all_usage[name] = all_usage.get(name, 0) + value
                if row["outcome"] == "accepted":
                    accepted_usage[name] = accepted_usage.get(name, 0) + value
    manifest = _publish_with_manifest(output, artifacts, {
        "schema_version": PANEL_SCHEMA_VERSION,
        "controller_manifest_sha256": controller_sha,
        "panel_run_id": panel_run_id,
        "queue_sha256": _sha256(raw_queue),
        "judge_config": judge_config,
        "panel_items": len(blinded),
        "verdict_count": len(final_verdicts),
        "model_calls_reserved": exported["model_calls_reserved"],
        "model_calls_closed": exported["model_calls_closed"],
        "final_witness_head": exported["witness_head"],
        "token_usage": {
            "accepted": accepted_usage,
            "all_attempts": all_usage,
            "unknown_usage_attempts": unknown_usage_attempts,
            "all_attempts_reconciled": unknown_usage_attempts == 0,
            "all_attempts_are_lower_bound": unknown_usage_attempts > 0,
        },
        "all_checks_passed": True,
    })
    return manifest


def _packet_visible_refs(controller: Mapping[str, Any]) -> dict[str, dict[str, set[str]]]:
    result: dict[str, dict[str, set[str]]] = {}
    for arm, snapshot_name in (("t0", "packet_v"), ("t1", "packet_t"), ("e1", "packet_e")):
        by_id: dict[str, set[str]] = {}
        for row in _read_jsonl(Path(controller["snapshots"][snapshot_name]["snapshot_path"])):
            payload = json.loads(row["model_payload_json"])
            evidence = payload.get("evidence")
            if not isinstance(evidence, dict):
                raise PostprocessError("compiled packet evidence changed")
            refs: set[str] = set()
            resources = evidence.get("resources")
            if not isinstance(resources, list):
                raise PostprocessError("compiled packet resources changed")
            for resource in resources:
                if isinstance(resource, dict) and isinstance(
                    resource.get("resourceType"), str
                ) and isinstance(resource.get("id"), str):
                    refs.add(f"{resource['resourceType']}/{resource['id']}")
            groups = payload.get("event_groups", [])
            if not isinstance(groups, list):
                raise PostprocessError("compiled event groups changed")
            for group in groups:
                if not isinstance(group, dict):
                    raise PostprocessError("compiled event group changed")
                root_ref = group.get("root_ref")
                if isinstance(root_ref, str):
                    refs.add(root_ref)
                member_refs = group.get("member_refs")
                if not isinstance(member_refs, list):
                    raise PostprocessError("compiled event members changed")
                for member in member_refs:
                    if not isinstance(member, dict) or not isinstance(
                        member.get("reference"), str
                    ):
                        raise PostprocessError("compiled event member changed")
                    refs.add(member["reference"])
            by_id[row["question_id"]] = refs
        result[arm] = by_id
    return result


def _verified_grading(controller: Mapping[str, Any], controller_sha: str) -> tuple[
    dict[str, Any], dict[str, Any], list[dict[str, Any]]
]:
    root = Path(controller["outputs"]["grading"])
    manifest = _read_json(root / "manifest.json")
    if (
        manifest.get("schema_version") != GRADING_SCHEMA_VERSION
        or manifest.get("controller_manifest_sha256") != controller_sha
        or manifest.get("all_checks_passed") is not True
    ):
        raise PostprocessError("grading manifest changed")
    for name, receipt in manifest["artifacts"].items():
        if _receipt((root / name).read_bytes()) != receipt:
            raise PostprocessError(f"grading artifact changed: {name}")
    deterministic = _read_json(root / "deterministic_labels.json")
    queue = _read_jsonl(root / "panel_queue.jsonl")
    return manifest, deterministic, queue


def _verified_panel(
    controller: Mapping[str, Any],
    controller_sha: str,
    *,
    bundle_root: Path | None = None,
) -> tuple[
    dict[str, Any], dict[str, int]
]:
    root = Path(controller["outputs"]["panel"])
    manifest = _read_json(root / "manifest.json")
    if (
        manifest.get("schema_version") != PANEL_SCHEMA_VERSION
        or manifest.get("controller_manifest_sha256") != controller_sha
        or manifest.get("all_checks_passed") is not True
    ):
        raise PostprocessError("witnessed panel manifest changed")
    artifacts = manifest.get("artifacts")
    panel_items = manifest.get("panel_items")
    expected_artifacts = (
        {"panel_verdicts.json"}
        if panel_items == 0
        else {"panel_verdicts.json", "witnessed_panel_export.json"}
    )
    if not isinstance(artifacts, dict) or set(artifacts) != expected_artifacts:
        raise PostprocessError("witnessed panel artifact inventory changed")
    for name, receipt in artifacts.items():
        if _receipt((root / name).read_bytes()) != receipt:
            raise PostprocessError(f"witnessed panel artifact changed: {name}")
    verdicts = _read_json(root / "panel_verdicts.json")
    if not isinstance(verdicts, dict) or any(
        not isinstance(key, str) or type(value) is not int or value not in {0, 1}
        for key, value in verdicts.items()
    ):
        raise PostprocessError("panel verdict map changed")
    if panel_items == 0:
        if verdicts or manifest.get("model_calls_reserved") != 0:
            raise PostprocessError("empty panel result changed")
        return manifest, verdicts

    if bundle_root is None:
        bundle_root = Path(
            controller["snapshots"]["a11b_postprocess"]["snapshot_path"]
        ).parent.parent
    raw_queue, queue = run_a11b_panel.load_a11_queue(
        Path(controller["outputs"]["grading"]) / "panel_queue.jsonl"
    )
    codex = run_a11b_panel.CodexIdentity(
        path=Path(controller["grading"]["panel"]["codex_bin"]),
        version=controller["grading"]["panel"]["codex_version"],
        sha256=controller["grading"]["panel"]["codex_binary_sha256"],
    )
    judge_config = run_a11b_panel.build_judge_config(
        controller_manifest_sha256=controller_sha,
        codex=codex,
    )
    blinded = run_a11b_panel.prepare_blinded_items(queue, judge_config)
    batches = run_a11b_panel.expected_batches(blinded)
    schema_bytes = _panel_schema_bytes()
    invocations: list[experiment_executor.SealedInvocation] = []
    index_to_batch: dict[int, tuple[int, int, list[dict[str, Any]]]] = {}
    for schedule_index, ((vote_round, batch_number), batch) in enumerate(
        batches.items()
    ):
        invocations.append(
            experiment_executor.SealedInvocation(
                phase="panel",
                schedule_index=schedule_index,
                prompt=run_a11b_panel.batch_prompt(batch).encode("utf-8"),
                output_schema=schema_bytes,
                model=a11b_grading.PANEL_MODEL,
                reasoning_effort=a11b_grading.PANEL_EFFORT,
                runtime_path=str(codex.path),
                runtime_sha256=codex.sha256,
                timeout_seconds=a11b_grading.PANEL_TIMEOUT_SECONDS,
            )
        )
        index_to_batch[schedule_index] = (vote_round, batch_number, batch)
    commitment_key = (bundle_root / "commitment.key").read_bytes()
    authenticator = witness.SshEd25519Authenticator(
        private_key_path=bundle_root / "witness_ed25519",
        identity=PANEL_WITNESS_IDENTITY,
    )
    schedule = tuple(
        witness.ScheduleItem(
            phase=invocation.phase,
            schedule_index=invocation.schedule_index,
            call_commitment=invocation.call_commitment(commitment_key),
            max_attempts=3,
        )
        for invocation in invocations
    )
    panel_run_id = _sha256(
        _canonical(
            {
                "schema_version": "a11b-derived-panel-run-v1",
                "controller_manifest_sha256": controller_sha,
                "queue_sha256": _sha256(raw_queue),
                "judge_config": judge_config,
                "schedule": [
                    {
                        "phase": item.phase,
                        "schedule_index": item.schedule_index,
                        "call_commitment": item.call_commitment,
                        "max_attempts": item.max_attempts,
                    }
                    for item in schedule
                ],
            }
        )
    )
    ledger = witness.WitnessLedger(
        bundle_root / "state/panel-witness",
        run_id=panel_run_id,
        schedule=schedule,
        authenticator=authenticator,
        clock=lambda: "verification-only",
    )
    panel_executor = experiment_executor.ExperimentExecutor(
        bundle_root / "state/panel-executor",
        ledger=ledger,
        invocations=tuple(invocations),
        commitment_key=commitment_key,
        driver=object(),
    )
    exported = panel_executor.export_completed_run()
    witnessed_export = (root / "witnessed_panel_export.json").read_bytes()
    if witnessed_export != _canonical(exported):
        raise PostprocessError("witnessed panel export changed")
    if (
        manifest.get("panel_run_id") != panel_run_id
        or manifest.get("queue_sha256") != _sha256(raw_queue)
        or manifest.get("judge_config") != judge_config
        or manifest.get("model_calls_reserved") != exported["model_calls_reserved"]
        or manifest.get("model_calls_closed") != exported["model_calls_closed"]
        or manifest.get("final_witness_head") != exported["witness_head"]
    ):
        raise PostprocessError("witnessed panel summary changed")
    by_slot: dict[int, list[dict[str, Any]]] = {}
    for row in exported["attempts"]:
        by_slot.setdefault(row["descriptor"]["schedule_index"], []).append(row)
    votes = {item["opaque_id"]: [] for item in blinded}
    for schedule_index in range(len(invocations)):
        accepted = [
            row for row in by_slot.get(schedule_index, []) if row["outcome"] == "accepted"
        ]
        if len(accepted) != 1:
            raise PostprocessError("panel accepted-slot coverage changed")
        attempt_verdicts = _panel_attempt_answer(accepted[0])
        vote_round, _batch_number, batch = index_to_batch[schedule_index]
        expected_ids = [item["opaque_id"] for item in batch]
        if set(attempt_verdicts) != set(expected_ids):
            raise PostprocessError("panel verdict coverage changed")
        for opaque_id in expected_ids:
            if len(votes[opaque_id]) != vote_round:
                raise PostprocessError("panel vote ordering changed")
            votes[opaque_id].append(attempt_verdicts[opaque_id])
    hosts = {item["opaque_id"]: item["host"] for item in blinded}
    derived_verdicts = {
        f"{hosts[opaque_id]['arm']}|{hosts[opaque_id]['question_id']}": int(
            sum(values) * 2 > len(values)
        )
        for opaque_id, values in sorted(votes.items())
    }
    if derived_verdicts != verdicts:
        raise PostprocessError("panel verdict majority changed")
    return manifest, verdicts


def finalize(
    *, controller_path: Path, audit_root: Path = FIXED_AUDIT_ROOT
) -> dict[str, Any]:
    controller, controller_sha = _load_controller(controller_path)
    coverage, export = _verified_export(controller, controller_sha)
    grading_manifest, deterministic, queue = _verified_grading(
        controller, controller_sha
    )
    panel_manifest, panel_verdicts = _verified_panel(
        controller, controller_sha, bundle_root=controller_path.parent
    )
    audit_manifest = _verify_audit_tree(
        audit_root,
        controller["inputs"]["audit_manifest_sha256"],
    )
    accepted = {
        (row["arm"], row["question_id"]): row
        for row in export["accepted_answers"]
    }
    gold = a11b_grading.load_gold_after_completion(
        coverage,
        gold_loader=lambda: _read_jsonl(audit_root / "efficacy/gold.jsonl"),
        receipt_validator=lambda receipt: (
            accepted.get((receipt["arm"], receipt["question_id"]), {}).get(
                "completion_receipt"
            )
            == receipt
        ),
    )
    audit_by_id = {
        row["question_id"]: row
        for row in _read_jsonl(audit_root / "efficacy/audit.jsonl")
    }
    questions = _questions_from_snapshot(controller)
    for question_id in questions:
        questions[question_id].update(
            {
                "patient_cluster_sha256": gold[question_id][
                    "patient_cluster_sha256"
                ],
                "family": audit_by_id[question_id]["family"],
                "depth": audit_by_id[question_id]["depth"],
                "difficulty": audit_by_id[question_id]["difficulty"],
                "temporal_policy": audit_by_id[question_id]["temporal_policy"],
            }
        )
    labels = a11b_grading.final_labels(
        question_ids=coverage["question_ids"],
        deterministic=deterministic,
        panel_queue=queue,
        panel_verdicts=panel_verdicts,
    )
    visible = _packet_visible_refs(controller)
    behavior: dict[str, dict[str, Any]] = {}
    for arm in a11b_grading.ARMS:
        counts = {
            "abstentions": 0,
            "false_abstentions_answerable": 0,
            "unsupported_answers": 0,
            "citation_failures": 0,
            "temporal_binding_errors": 0,
            "correct": sum(labels[arm].values()),
        }
        for question_id in coverage["question_ids"]:
            answer = accepted[(arm, question_id)]["answer"]
            reason = answer.get("insufficiency_reason")
            abstained = isinstance(reason, str) and bool(reason.strip())
            sources = answer.get("source_resource_ids")
            if not isinstance(sources, list):
                raise PostprocessError("accepted source-resource IDs changed")
            if abstained:
                counts["abstentions"] += 1
                if gold[question_id]["answerable"]:
                    counts["false_abstentions_answerable"] += 1
                continue
            invalid_citation = not sources or any(
                not isinstance(source, str) or source not in visible[arm][question_id]
                for source in sources
            )
            unsupported = not gold[question_id]["answerable"] or invalid_citation
            temporal_error = (
                gold[question_id]["answerable"]
                and not set(sources).intersection(gold[question_id]["selected_path_refs"])
            )
            counts["citation_failures"] += int(invalid_citation)
            counts["unsupported_answers"] += int(unsupported)
            counts["temporal_binding_errors"] += int(temporal_error)
        behavior[arm] = counts
    mechanism = {
        "unique_patient_clusters": len(
            {gold[question_id]["patient_cluster_sha256"] for question_id in gold}
        ),
        "audit_artifacts_verified": len(audit_manifest["artifacts"]),
        "packet_utf8_bytes_by_arm": {
            arm: controller["snapshots"][snapshot]["bytes"]
            for arm, snapshot in (("t0", "packet_v"), ("t1", "packet_t"), ("e1", "packet_e"))
        },
        "compilation_latency": {
            "status": "separate_zero_model_benchmark_required",
            "promotion_gate": False,
        },
    }
    result = a11b_grading.assemble_result(
        question_ids=coverage["question_ids"],
        questions=questions,
        gold=gold,
        labels=labels,
        mechanism_outcomes=mechanism,
        answer_behavior_outcomes=behavior,
        economics={
            "answers": grading_manifest["answer_economics"],
            "panel": panel_manifest["token_usage"],
        },
        input_hashes={
            "controller_manifest_sha256": controller_sha,
            "public_manifest_sha256": controller["inputs"][
                "public_manifest_sha256"
            ],
            "audit_manifest_sha256": controller["inputs"]["audit_manifest_sha256"],
            "answer_export_manifest_sha256": _sha256(
                (
                    Path(controller["outputs"]["answer_export"]) / "manifest.json"
                ).read_bytes()
            ),
            "grading_manifest_sha256": _sha256(
                (Path(controller["outputs"]["grading"]) / "manifest.json").read_bytes()
            ),
            "panel_manifest_sha256": _sha256(
                (Path(controller["outputs"]["panel"]) / "manifest.json").read_bytes()
            ),
        },
    )
    result_bytes = _canonical(result)
    output = Path(controller["outputs"]["result"])
    artifacts = {"result.json": result_bytes}
    manifest = _publish_with_manifest(output, artifacts, {
        "schema_version": FINAL_SCHEMA_VERSION,
        "controller_manifest_sha256": controller_sha,
        "model_calls_during_finalization": 0,
        "status": result["status"],
        "promotion": result["promotion_assessment"],
        "all_checks_passed": True,
    })
    return manifest


def _verified_final(controller: Mapping[str, Any], controller_sha: str) -> dict[str, Any]:
    root = Path(controller["outputs"]["result"])
    manifest = _read_json(root / "manifest.json")
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if (
        manifest.get("schema_version") != FINAL_SCHEMA_VERSION
        or manifest.get("controller_manifest_sha256") != controller_sha
        or manifest.get("all_checks_passed") is not True
        or not isinstance(artifacts, dict)
        or set(artifacts) != {"result.json"}
    ):
        raise PostprocessError("final result manifest changed")
    if _receipt((root / "result.json").read_bytes()) != artifacts["result.json"]:
        raise PostprocessError("final result artifact changed")
    return manifest


def _clock() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def run_all(
    *,
    bundle_root: Path,
    audit_root: Path,
    trusted_executor: experiment_executor.ExperimentExecutor | None = None,
) -> dict[str, Any]:
    controller_path = bundle_root / "controller.json"
    controller, _ = _load_controller(controller_path)
    _verify_installed_postprocess_sources(
        controller=controller,
        bundle_root=bundle_root,
    )
    controller_sha = _sha256(controller_path.read_bytes())
    if trusted_executor is None:
        restricted = service.load_sealed_service(bundle_root, clock=_clock)
        trusted_executor = restricted._executor
    answer_root = Path(controller["outputs"]["answer_export"])
    if answer_root.exists():
        _verified_export(controller, controller_sha)
    else:
        prepare_answer_export(
            controller_path=controller_path,
            trusted_executor=trusted_executor,
        )
    grading_root = Path(controller["outputs"]["grading"])
    if grading_root.exists():
        _verified_grading(controller, controller_sha)
    else:
        prepare_grading(controller_path=controller_path, audit_root=audit_root)
    panel_root = Path(controller["outputs"]["panel"])
    if panel_root.exists():
        _verified_panel(controller, controller_sha, bundle_root=bundle_root)
    else:
        run_witnessed_panel(controller_path=controller_path, bundle_root=bundle_root)
    final_root = Path(controller["outputs"]["result"])
    if final_root.exists():
        return _verified_final(controller, controller_sha)
    return finalize(controller_path=controller_path, audit_root=audit_root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, default=service.PRODUCTION_BUNDLE_DIR)
    parser.add_argument("--audit-root", type=Path, default=FIXED_AUDIT_ROOT)
    args = parser.parse_args()
    print(
        json.dumps(
            run_all(bundle_root=args.bundle_root, audit_root=args.audit_root),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
