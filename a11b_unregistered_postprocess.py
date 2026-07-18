#!/usr/bin/env python3
"""Grade and panel the completed A11b r3 exploratory preview.

This adapter can only write below the explicitly unregistered preview root. It
selects the answer artifact named by each validated acceptance marker, proves
exact 384 x 3 coverage before opening gold, applies the sealed deterministic
grading primitives, and runs the sealed arm-blind panel protocol with the
exact staged Codex binary pinned by the r3 controller.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

import a11b_grading
import a11b_unregistered_preview as preview
import codex_harness
import panel_grade
import run_a11b_panel
from run_lock import acquire_single_instance


CONTROLLER_SHA256 = preview.CONTROLLER_SHA256
BUNDLE_SHA256 = preview.BUNDLE_SHA256
EXPECTED_CALLS = preview.EXPECTED_CALLS
EXPECTED_ARMS = preview.EXPECTED_ARMS
GRADING_DIR = "grading-unregistered-v1"
PANEL_DIR = "panel-unregistered-v1"
RESULT_DIR = "result-unregistered-v1"
GRADING_SCHEMA_VERSION = "a11b-unregistered-grading-preparation-v1"
RESULT_SCHEMA_VERSION = "a11b-unregistered-final-result-v1"


class UnregisteredPostprocessError(RuntimeError):
    """An exploratory grading or panel invariant failed closed."""


def _canonical(value: object) -> bytes:
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


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _receipt(payload: bytes) -> dict[str, Any]:
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise UnregisteredPostprocessError(f"JSON artifact is not an object: {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line:
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UnregisteredPostprocessError(
                f"invalid JSONL artifact: {path.name}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise UnregisteredPostprocessError(
                f"non-object JSONL row: {path.name}:{line_number}"
            )
        rows.append(value)
    return rows


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode
    )
    try:
        sent = 0
        while sent < len(payload):
            sent += os.write(descriptor, payload[sent:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_with_manifest(
    root: Path, artifacts: Mapping[str, bytes], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    if root.exists() or root.is_symlink():
        raise UnregisteredPostprocessError(f"immutable output already exists: {root.name}")
    receipts = {name: _receipt(payload) for name, payload in artifacts.items()}
    complete = {**dict(manifest), "artifacts": receipts}
    published = {**dict(artifacts), "manifest.json": _canonical(complete)}
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.publish-", dir=root.parent))
    temporary.chmod(0o700)
    try:
        for name, payload in sorted(published.items()):
            _write_exclusive(temporary / name, payload)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.rename(temporary, root)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return complete


def _verify_audit_tree(root: Path, expected_sha256: str) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise UnregisteredPostprocessError("audit root is unavailable")
    manifest_payload = (root / "manifest.json").read_bytes()
    if _sha256(manifest_payload) != expected_sha256:
        raise UnregisteredPostprocessError("audit manifest differs from controller")
    if (root / "manifest.sha256").read_text(encoding="ascii") != expected_sha256 + "\n":
        raise UnregisteredPostprocessError("audit manifest sidecar changed")
    manifest = json.loads(manifest_payload)
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict):
        raise UnregisteredPostprocessError("audit manifest inventory changed")
    expected_files = {"manifest.json", "manifest.sha256", *artifacts}
    observed_files: set[str] = set()
    for path in root.rglob("*"):
        status = path.lstat()
        if path.is_symlink() or (
            not stat.S_ISDIR(status.st_mode) and not stat.S_ISREG(status.st_mode)
        ):
            raise UnregisteredPostprocessError("audit tree contains an unsafe entry")
        if stat.S_ISREG(status.st_mode):
            observed_files.add(path.relative_to(root).as_posix())
    if observed_files != expected_files:
        raise UnregisteredPostprocessError("audit tree inventory changed")
    for name, receipt in artifacts.items():
        if _receipt((root / name).read_bytes()) != receipt:
            raise UnregisteredPostprocessError(f"audit artifact changed: {name}")
    return manifest


def _source_sha256() -> str:
    return _sha256(Path(__file__).read_bytes())


def _output_path(preview_root: Path, name: str) -> Path:
    root = preview_root.resolve()
    output = (root / name).resolve()
    if output.parent != root:
        raise UnregisteredPostprocessError("exploratory output escaped preview root")
    return output


def _load_context(
    *, controller_path: Path, bundle_path: Path, preview_root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    controller, invocations = preview._load_inputs(controller_path, bundle_path)
    if _sha256(controller_path.read_bytes()) != CONTROLLER_SHA256:
        raise UnregisteredPostprocessError("controller changed")
    if _sha256(bundle_path.read_bytes()) != BUNDLE_SHA256:
        raise UnregisteredPostprocessError("bundle changed")
    root = preview_root.resolve()
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
        raise UnregisteredPostprocessError("preview root is not a private directory")
    sentinel = _read_object(root / "UNREGISTERED_EXPLORATORY_PREVIEW.json")
    if (
        sentinel.get("registered") is not False
        or sentinel.get("confirmatory_use_prohibited") is not True
        or sentinel.get("controller_sha256") != CONTROLLER_SHA256
        or sentinel.get("bundle_sha256") != BUNDLE_SHA256
    ):
        raise UnregisteredPostprocessError("preview sentinel changed")
    status = preview._status(root, controller["schedule"]["items"])
    if (
        status.get("complete") is not True
        or status.get("accepted_calls") != EXPECTED_CALLS
        or status.get("accepted_by_arm") != {arm: 384 for arm in EXPECTED_ARMS}
    ):
        raise UnregisteredPostprocessError("preview completion proof is incomplete")
    return controller, invocations


def _selected_answer(
    *, slot_dir: Path, index: int, host: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not preview._accepted_marker_valid(slot_dir, index, str(host["prompt_sha256"])):
        raise UnregisteredPostprocessError(f"invalid acceptance marker at slot {index}")
    marker = _read_object(slot_dir / "accepted.json")
    attempt_number = marker.get("attempt_number")
    if type(attempt_number) is not int or attempt_number < 1:
        raise UnregisteredPostprocessError("accepted attempt number changed")
    attempt_dir = slot_dir / f"attempt-{attempt_number}"
    artifact_name = marker.get("answer_artifact", "answer.json")
    if artifact_name not in {"answer.json", "normalized-answer.json"}:
        raise UnregisteredPostprocessError("selected answer artifact changed")
    answer_path = attempt_dir / artifact_name
    schema_path = attempt_dir / "registered-schema.json"
    answer_payload = answer_path.read_bytes()
    if not codex_harness.answer_matches_schema(answer_path, schema_path):
        raise UnregisteredPostprocessError("selected answer fails the full registered schema")
    answer = json.loads(answer_payload)
    if not isinstance(answer, dict):
        raise UnregisteredPostprocessError("selected answer is not an object")
    attempt_receipt = _read_object(attempt_dir / "receipt.json")
    acceptance_mode = marker.get("acceptance_mode", "direct")
    selected_receipt = {
        "registered": False,
        "schedule_index": index,
        "arm": host["arm"],
        "question_id": host["question_id"],
        "attempt_number": attempt_number,
        "acceptance_mode": acceptance_mode,
        "answer_artifact": artifact_name,
        "selected_answer_sha256": _sha256(answer_payload),
        "raw_answer_sha256": attempt_receipt["answer.json"]["sha256"],
        "event_log_sha256": attempt_receipt["events.jsonl"]["sha256"],
        "stderr_log_sha256": attempt_receipt["stderr.log"]["sha256"],
        "registered_schema_sha256": marker["registered_schema_sha256"],
        "acceptance_marker_sha256": _sha256((slot_dir / "accepted.json").read_bytes()),
    }
    if acceptance_mode == "deterministic_normalization":
        selected_receipt["normalization_receipt_sha256"] = _sha256(
            (attempt_dir / "normalization.json").read_bytes()
        )
    return answer, selected_receipt, attempt_receipt


def _empty_usage_by_arm() -> dict[str, dict[str, int]]:
    return {
        arm: {key: 0 for key in preview.TOKEN_KEYS}
        for arm in EXPECTED_ARMS
    }


def _add_usage(target: dict[str, int], usage: Mapping[str, Any]) -> bool:
    if any(type(usage.get(key)) is not int for key in preview.TOKEN_KEYS):
        return False
    for key in preview.TOKEN_KEYS:
        target[key] += int(usage[key])
    return True


def _economics(
    *,
    preview_root: Path,
    schedule: list[dict[str, Any]],
    selected_attempts: Mapping[int, int],
) -> dict[str, Any]:
    accepted_tokens = _empty_usage_by_arm()
    all_tokens = _empty_usage_by_arm()
    attempts = {arm: {"accepted": 0, "all": 0} for arm in EXPECTED_ARMS}
    unknown = {arm: 0 for arm in EXPECTED_ARMS}
    normalizations = {arm: 0 for arm in EXPECTED_ARMS}
    for index, host in enumerate(schedule):
        arm = host["arm"]
        selected_number = selected_attempts[index]
        slot_dir = preview_root / "slots" / f"{index:04d}"
        marker = _read_object(slot_dir / "accepted.json")
        normalizations[arm] += int(
            marker.get("acceptance_mode") == "deterministic_normalization"
        )
        for receipt_path in sorted(slot_dir.glob("attempt-*/receipt.json")):
            receipt = _read_object(receipt_path)
            attempts[arm]["all"] += 1
            usage = receipt.get("token_usage")
            if not isinstance(usage, dict) or not _add_usage(all_tokens[arm], usage):
                unknown[arm] += 1
            attempt_number = int(receipt_path.parent.name.split("-", 1)[1])
            if attempt_number == selected_number:
                attempts[arm]["accepted"] += 1
                if not isinstance(usage, dict) or not _add_usage(
                    accepted_tokens[arm], usage
                ):
                    raise UnregisteredPostprocessError(
                        "selected answer attempt has no complete token receipt"
                    )
    return {
        "registered": False,
        "accepted_token_usage_by_arm": accepted_tokens,
        "all_attempt_token_usage_by_arm": all_tokens,
        "attempts_by_arm": attempts,
        "unknown_usage_attempts_by_arm": unknown,
        "deterministic_normalizations_by_arm": normalizations,
        "all_attempt_token_economics_reconciled": not any(unknown.values()),
        "all_attempt_token_usage_is_lower_bound": any(unknown.values()),
    }


def _verify_snapshot(controller: Mapping[str, Any], bundle_root: Path, name: str) -> Path:
    entry = controller.get("snapshots", {}).get(name)
    if not isinstance(entry, dict):
        raise UnregisteredPostprocessError(f"missing sealed snapshot: {name}")
    path = bundle_root / "snapshots" / Path(str(entry.get("snapshot_path", ""))).name
    payload = path.read_bytes()
    if _receipt(payload) != {
        "sha256": entry.get("sha256"),
        "bytes": entry.get("bytes"),
    }:
        raise UnregisteredPostprocessError(f"sealed snapshot changed: {name}")
    return path


def _questions(controller: Mapping[str, Any], bundle_root: Path) -> dict[str, dict[str, Any]]:
    path = _verify_snapshot(controller, bundle_root, "answer_input")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result = {row["question_id"]: dict(row) for row in rows}
    if len(result) != 384:
        raise UnregisteredPostprocessError("sealed question coverage changed")
    return result


def _verify_published(
    root: Path, *, schema_version: str, artifact_names: set[str]
) -> dict[str, Any]:
    manifest = _read_object(root / "manifest.json")
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("schema_version") != schema_version
        or manifest.get("registered") is not False
        or manifest.get("confirmatory_use_prohibited") is not True
        or manifest.get("controller_manifest_sha256") != CONTROLLER_SHA256
        or manifest.get("all_checks_passed") is not True
        or not isinstance(artifacts, dict)
        or set(artifacts) != artifact_names
    ):
        raise UnregisteredPostprocessError("published exploratory manifest changed")
    for name, receipt in artifacts.items():
        if _receipt((root / name).read_bytes()) != receipt:
            raise UnregisteredPostprocessError(f"published artifact changed: {name}")
    return manifest


def prepare_grading(
    *,
    controller_path: Path,
    bundle_path: Path,
    preview_root: Path,
    audit_root: Path,
) -> dict[str, Any]:
    controller, _invocations = _load_context(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
    )
    bundle_root = bundle_path.parent
    schedule = controller["schedule"]["items"]
    grading_root = _output_path(preview_root, GRADING_DIR)
    artifact_names = {
        "accepted_answers.json",
        "completion_coverage.json",
        "deterministic_labels.json",
        "economics.json",
        "panel_queue.jsonl",
        "selected_answer_receipts.json",
    }
    if grading_root.exists():
        return _verify_published(
            grading_root,
            schema_version=GRADING_SCHEMA_VERSION,
            artifact_names=artifact_names,
        )

    accepted: dict[tuple[str, str], dict[str, Any]] = {}
    accepted_rows: list[dict[str, Any]] = []
    selected_receipts: list[dict[str, Any]] = []
    completion_receipts: list[dict[str, Any]] = []
    selected_attempts: dict[int, int] = {}
    schema_sha = controller["grading"]["answer_schema_sha256"]
    for index, host in enumerate(schedule):
        slot_dir = preview_root / "slots" / f"{index:04d}"
        answer, selected_receipt, _attempt_receipt = _selected_answer(
            slot_dir=slot_dir, index=index, host=host
        )
        selected_attempts[index] = selected_receipt["attempt_number"]
        completion = {
            "kind": a11b_grading.COMPLETION_KIND,
            "schema_version": a11b_grading.COMPLETION_SCHEMA_VERSION,
            "registered": False,
            "controller_manifest_sha256": CONTROLLER_SHA256,
            "arm": host["arm"],
            "question_id": host["question_id"],
            "status": "answered",
            "attempt_number": selected_receipt["attempt_number"],
            "answer_sha256": selected_receipt["selected_answer_sha256"],
            "event_log_sha256": selected_receipt["event_log_sha256"],
            "prompt_sha256": host["prompt_sha256"],
            "stderr_log_sha256": selected_receipt["stderr_log_sha256"],
            "model_input_sha256": host["prompt_sha256"],
            "schema_sha256": schema_sha,
            "acceptance_mode": selected_receipt["acceptance_mode"],
            "acceptance_marker_sha256": selected_receipt[
                "acceptance_marker_sha256"
            ],
        }
        row = {
            "schedule_index": index,
            "arm": host["arm"],
            "question_id": host["question_id"],
            "answer": answer,
            "completion_receipt": completion,
        }
        key = (host["arm"], host["question_id"])
        if key in accepted:
            raise UnregisteredPostprocessError("accepted host duplicated")
        accepted[key] = row
        accepted_rows.append(row)
        selected_receipts.append(selected_receipt)
        completion_receipts.append(completion)

    question_ids = list(dict.fromkeys(host["question_id"] for host in schedule))
    coverage = {
        "schema_version": a11b_grading.COMPLETION_COVERAGE_VERSION,
        "registered": False,
        "controller_manifest_sha256": CONTROLLER_SHA256,
        "question_ids": question_ids,
        "arms": list(a11b_grading.ARMS),
        "receipts": completion_receipts,
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

    audit_manifest = _verify_audit_tree(
        audit_root, controller["inputs"]["audit_manifest_sha256"]
    )
    gold_rows = _read_jsonl(audit_root / "efficacy/gold.jsonl")
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
    audit_rows = _read_jsonl(audit_root / "efficacy/audit.jsonl")
    audit_by_id = {row["question_id"]: row for row in audit_rows}
    questions = _questions(controller, bundle_root)
    if set(questions) != set(gold) or set(audit_by_id) != set(gold):
        raise UnregisteredPostprocessError("audit/public question coverage changed")
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
    for question_id in question_ids:
        for arm in a11b_grading.ARMS:
            verdict, panel_item = a11b_grading.deterministic_partition(
                question=questions[question_id],
                gold=gold[question_id],
                answer=accepted[(arm, question_id)]["answer"],
            )
            if verdict is not None:
                deterministic[arm][question_id] = verdict
            elif panel_item is not None:
                panel_queue.append({"arm": arm, **panel_item})
            else:
                raise UnregisteredPostprocessError("grading partition produced no label")

    economics = _economics(
        preview_root=preview_root,
        schedule=schedule,
        selected_attempts=selected_attempts,
    )
    artifacts = {
        "accepted_answers.json": _canonical(accepted_rows),
        "completion_coverage.json": _canonical(coverage),
        "deterministic_labels.json": _canonical(deterministic),
        "economics.json": _canonical(economics),
        "panel_queue.jsonl": b"".join(_canonical(row) for row in panel_queue),
        "selected_answer_receipts.json": _canonical(selected_receipts),
    }
    manifest = _publish_with_manifest(
        grading_root,
        artifacts,
        {
            "schema_version": GRADING_SCHEMA_VERSION,
            "registered": False,
            "confirmatory_use_prohibited": True,
            "controller_manifest_sha256": CONTROLLER_SHA256,
            "bundle_sha256": BUNDLE_SHA256,
            "adapter_source_sha256": _source_sha256(),
            "model_calls": 0,
            "completed_answers": EXPECTED_CALLS,
            "deterministic_labels": sum(
                len(rows) for rows in deterministic.values()
            ),
            "panel_items": len(panel_queue),
            "panel_config": controller["grading"]["panel"],
            "normalizations": sum(
                economics["deterministic_normalizations_by_arm"].values()
            ),
            "audit_manifest_sha256": _sha256(
                (audit_root / "manifest.json").read_bytes()
            ),
            "audit_artifact_count": len(audit_manifest["artifacts"]),
            "all_checks_passed": True,
        },
    )
    return manifest


def _load_grading(preview_root: Path) -> tuple[dict[str, Any], Path]:
    root = _output_path(preview_root, GRADING_DIR)
    manifest = _verify_published(
        root,
        schema_version=GRADING_SCHEMA_VERSION,
        artifact_names={
            "accepted_answers.json",
            "completion_coverage.json",
            "deterministic_labels.json",
            "economics.json",
            "panel_queue.jsonl",
            "selected_answer_receipts.json",
        },
    )
    return manifest, root


def _codex_identity(controller: Mapping[str, Any], codex_bin: Path) -> run_a11b_panel.CodexIdentity:
    panel = controller["grading"]["panel"]
    payload = codex_bin.read_bytes()
    digest = _sha256(payload)
    version_process = subprocess.run(
        [str(codex_bin), "--version"], capture_output=True, text=True, timeout=10
    )
    version = (version_process.stdout or version_process.stderr).strip()
    if (
        version_process.returncode != 0
        or digest != panel["codex_binary_sha256"]
        or version != panel["codex_version"]
        or panel.get("model") != run_a11b_panel.REGISTERED_MODEL
        or panel.get("reasoning_effort")
        != run_a11b_panel.REGISTERED_REASONING_EFFORT
        or panel.get("votes") != run_a11b_panel.REGISTERED_VOTES
        or panel.get("batch_size") != run_a11b_panel.REGISTERED_BATCH_SIZE
        or panel.get("timeout_seconds")
        != run_a11b_panel.REGISTERED_TIMEOUT_SECONDS
    ):
        raise UnregisteredPostprocessError("staged panel runtime differs from r3 pins")
    return run_a11b_panel.CodexIdentity(
        path=codex_bin.resolve(), version=version, sha256=digest
    )


def _panel_context(
    *,
    controller: Mapping[str, Any],
    preview_root: Path,
    codex_bin: Path,
) -> tuple[
    Path,
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    run_a11b_panel.CodexIdentity,
]:
    grading_manifest, grading_root = _load_grading(preview_root)
    queue_path = grading_root / "panel_queue.jsonl"
    queue_bytes, queue = run_a11b_panel.load_a11_queue(queue_path)
    codex = _codex_identity(controller, codex_bin)
    judge_config = run_a11b_panel.build_judge_config(
        controller_manifest_sha256=CONTROLLER_SHA256, codex=codex
    )
    blinded = run_a11b_panel.prepare_blinded_items(queue, judge_config)
    identity = run_a11b_panel.build_manifest_identity(
        controller_manifest_sha256=CONTROLLER_SHA256,
        queue_sha256=_sha256(queue_bytes),
        judge_config=judge_config,
        blinded_items=blinded,
    )
    identity.update(
        {
            "registered": False,
            "confirmatory_use_prohibited": True,
            "source_grading_manifest_sha256": _sha256(
                (grading_root / "manifest.json").read_bytes()
            ),
            "adapter_source_sha256": grading_manifest["adapter_source_sha256"],
        }
    )
    return queue_path, queue, identity, blinded, judge_config, codex


def _panel_progress(
    *, out_dir: Path, manifest: dict[str, Any], blinded: list[dict[str, Any]]
) -> tuple[dict[str, list[bool]], int]:
    """Validate completed receipts while tolerating one live attempt directory."""

    batches = run_a11b_panel.expected_batches(blinded)
    votes = {item["opaque_id"]: [] for item in blinded}
    active_attempts = 0
    for (vote_round, batch_number), batch in batches.items():
        attempts = run_a11b_panel._attempt_dirs(out_dir, vote_round, batch_number)
        accepted_result: dict[str, bool] | None = None
        for position, attempt_dir in enumerate(attempts):
            if not (attempt_dir / "receipt.json").exists():
                if position != len(attempts) - 1:
                    raise UnregisteredPostprocessError(
                        "nonterminal panel attempt has no receipt"
                    )
                active_attempts += 1
                continue
            receipt, result = run_a11b_panel._validate_attempt_receipt(
                attempt_dir,
                manifest=manifest,
                vote_round=vote_round,
                batch_number=batch_number,
                batch=batch,
            )
            if result is not None:
                if accepted_result is not None:
                    raise UnregisteredPostprocessError(
                        "panel batch has multiple accepted attempts"
                    )
                accepted_result = result
            elif receipt.get("retryable_provider_failure") is not True:
                raise UnregisteredPostprocessError(
                    "nonretryable completed panel failure"
                )
        if accepted_result is not None:
            for item in batch:
                votes[item["opaque_id"]].append(
                    accepted_result[item["opaque_id"]]
                )
    if active_attempts > 1:
        raise UnregisteredPostprocessError("multiple panel attempts are in progress")
    return votes, active_attempts


def panel_status(
    *,
    controller_path: Path,
    bundle_path: Path,
    preview_root: Path,
    codex_bin: Path,
) -> dict[str, Any]:
    controller, _invocations = _load_context(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
    )
    _queue_path, queue, identity, blinded, _judge_config, _codex = _panel_context(
        controller=controller, preview_root=preview_root, codex_bin=codex_bin
    )
    out_dir = _output_path(preview_root, PANEL_DIR)
    batches = run_a11b_panel.expected_batches(blinded)
    if not (out_dir / "manifest.json").exists():
        return {
            "registered": False,
            "items": len(queue),
            "votes_expected": len(queue) * run_a11b_panel.REGISTERED_VOTES,
            "votes_accepted": 0,
            "batches_expected": len(batches),
            "batches_accepted": 0,
            "complete": False,
        }
    if _read_object(out_dir / "manifest.json") != identity:
        raise UnregisteredPostprocessError("panel manifest changed")
    votes, active_attempts = _panel_progress(
        out_dir=out_dir, manifest=identity, blinded=blinded
    )
    accepted_batches = 0
    for (vote_round, _batch_number), batch in batches.items():
        if all(len(votes[item["opaque_id"]]) > vote_round for item in batch):
            accepted_batches += 1
    receipts = [
        _read_object(path) for path in sorted((out_dir / "attempts").rglob("receipt.json"))
    ]
    usage = panel_grade.panel_token_summary({"usage_receipts": receipts})
    complete = all(
        len(item_votes) == run_a11b_panel.REGISTERED_VOTES
        for item_votes in votes.values()
    )
    return {
        "registered": False,
        "items": len(queue),
        "votes_expected": len(queue) * run_a11b_panel.REGISTERED_VOTES,
        "votes_accepted": sum(len(item_votes) for item_votes in votes.values()),
        "batches_expected": len(batches),
        "batches_accepted": accepted_batches,
        "attempts": len(receipts),
        "active_attempts": active_attempts,
        "token_usage": usage,
        "complete": complete,
    }


def run_panel(
    *,
    controller_path: Path,
    bundle_path: Path,
    preview_root: Path,
    codex_bin: Path,
) -> dict[str, Any]:
    controller, _invocations = _load_context(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
    )
    queue_path, queue, identity, blinded, _judge_config, codex = _panel_context(
        controller=controller, preview_root=preview_root, codex_bin=codex_bin
    )
    out_dir = _output_path(preview_root, PANEL_DIR)
    queue_bytes = queue_path.read_bytes()
    with acquire_single_instance(run_a11b_panel.panel_lock_path(out_dir)):
        manifest = run_a11b_panel.initialize_or_validate_bundle(
            out_dir, identity=identity, queue_bytes=queue_bytes
        )
        batches, votes = run_a11b_panel.collect_panel_state(
            out_dir, manifest=manifest, blinded_items=blinded
        )
        for (vote_round, batch_number), batch in sorted(batches.items()):
            opaque_ids = [item["opaque_id"] for item in batch]
            if all(len(votes[opaque_id]) > vote_round for opaque_id in opaque_ids):
                continue
            while True:
                attempts = run_a11b_panel._attempt_dirs(
                    out_dir, vote_round, batch_number
                )
                if len(attempts) >= run_a11b_panel.MAX_OPERATIONAL_ATTEMPTS_PER_BATCH:
                    raise UnregisteredPostprocessError("panel retry cap reached")
                outcome = run_a11b_panel.execute_attempt(
                    attempt_dir=run_a11b_panel._attempt_root(
                        out_dir, vote_round, batch_number
                    )
                    / f"attempt-{len(attempts) + 1:03d}",
                    batch=batch,
                    vote_round=vote_round,
                    batch_number=batch_number,
                    attempt_number=len(attempts) + 1,
                    manifest=manifest,
                    codex=codex,
                )
                if outcome.accepted:
                    assert outcome.result is not None
                    for opaque_id, value in outcome.result.items():
                        votes[opaque_id].append(value)
                    break
                if outcome.receipt.get("retryable_provider_failure") is not True:
                    raise UnregisteredPostprocessError(
                        "nonretryable panel failure is a hard stop"
                    )
        expected_verdicts, expected_manifest = run_a11b_panel._derived_final_outputs(
            out_dir, manifest=manifest, blinded_items=blinded, votes=votes
        )
        if (out_dir / "panel_verdicts.manifest.json").exists():
            run_a11b_panel._validate_final_outputs(
                out_dir,
                expected_verdicts=expected_verdicts,
                expected_manifest=expected_manifest,
            )
        else:
            run_a11b_panel._write_final_outputs(
                out_dir, manifest=manifest, blinded_items=blinded, votes=votes
            )
    return panel_status(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
        codex_bin=codex_bin,
    )


def _packet_visible_refs(
    controller: Mapping[str, Any], bundle_root: Path
) -> dict[str, dict[str, set[str]]]:
    result: dict[str, dict[str, set[str]]] = {}
    for arm, snapshot_name in (
        ("t0", "packet_v"),
        ("t1", "packet_t"),
        ("e1", "packet_e"),
    ):
        by_id: dict[str, set[str]] = {}
        snapshot = _verify_snapshot(controller, bundle_root, snapshot_name)
        for row in _read_jsonl(snapshot):
            payload = json.loads(row["model_payload_json"])
            evidence = payload.get("evidence")
            if not isinstance(evidence, dict):
                raise UnregisteredPostprocessError("compiled packet evidence changed")
            refs: set[str] = set()
            resources = evidence.get("resources")
            if not isinstance(resources, list):
                raise UnregisteredPostprocessError("compiled packet resources changed")
            for resource in resources:
                if (
                    isinstance(resource, dict)
                    and isinstance(resource.get("resourceType"), str)
                    and isinstance(resource.get("id"), str)
                ):
                    refs.add(f"{resource['resourceType']}/{resource['id']}")
            groups = payload.get("event_groups", [])
            if not isinstance(groups, list):
                raise UnregisteredPostprocessError("compiled event groups changed")
            for group in groups:
                if not isinstance(group, dict):
                    raise UnregisteredPostprocessError("compiled event group changed")
                root_ref = group.get("root_ref")
                if isinstance(root_ref, str):
                    refs.add(root_ref)
                members = group.get("member_refs")
                if not isinstance(members, list):
                    raise UnregisteredPostprocessError("compiled event members changed")
                for member in members:
                    if not isinstance(member, dict) or not isinstance(
                        member.get("reference"), str
                    ):
                        raise UnregisteredPostprocessError(
                            "compiled event member changed"
                        )
                    refs.add(member["reference"])
            by_id[row["question_id"]] = refs
        result[arm] = by_id
    return result


def finalize(
    *,
    controller_path: Path,
    bundle_path: Path,
    preview_root: Path,
    audit_root: Path,
    codex_bin: Path,
) -> dict[str, Any]:
    controller, _invocations = _load_context(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
    )
    grading_manifest, grading_root = _load_grading(preview_root)
    status = panel_status(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
        codex_bin=codex_bin,
    )
    if status.get("complete") is not True:
        raise UnregisteredPostprocessError("panel is incomplete")
    result_root = _output_path(preview_root, RESULT_DIR)
    if result_root.exists():
        return _verify_published(
            result_root,
            schema_version=RESULT_SCHEMA_VERSION,
            artifact_names={"result.json"},
        )

    coverage = _read_object(grading_root / "completion_coverage.json")
    deterministic = _read_object(grading_root / "deterministic_labels.json")
    queue = _read_jsonl(grading_root / "panel_queue.jsonl")
    accepted_rows = json.loads((grading_root / "accepted_answers.json").read_bytes())
    if not isinstance(accepted_rows, list) or len(accepted_rows) != EXPECTED_CALLS:
        raise UnregisteredPostprocessError("accepted answer export changed")
    accepted = {
        (row["arm"], row["question_id"]): row
        for row in accepted_rows
        if isinstance(row, dict)
    }
    panel_root = _output_path(preview_root, PANEL_DIR)
    panel_verdicts = _read_object(panel_root / "panel_verdicts.json")
    labels = a11b_grading.final_labels(
        question_ids=coverage["question_ids"],
        deterministic=deterministic,
        panel_queue=queue,
        panel_verdicts=panel_verdicts,
    )
    audit_manifest = _verify_audit_tree(
        audit_root, controller["inputs"]["audit_manifest_sha256"]
    )
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
    questions = _questions(controller, bundle_path.parent)
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
    visible = _packet_visible_refs(controller, bundle_path.parent)
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
                raise UnregisteredPostprocessError("accepted citations changed")
            if abstained:
                counts["abstentions"] += 1
                if gold[question_id]["answerable"]:
                    counts["false_abstentions_answerable"] += 1
                continue
            invalid_citation = not sources or any(
                not isinstance(source, str)
                or source not in visible[arm][question_id]
                for source in sources
            )
            unsupported = not gold[question_id]["answerable"] or invalid_citation
            temporal_error = (
                gold[question_id]["answerable"]
                and not set(sources).intersection(
                    gold[question_id]["selected_path_refs"]
                )
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
            for arm, snapshot in (
                ("t0", "packet_v"),
                ("t1", "packet_t"),
                ("e1", "packet_e"),
            )
        },
        "compilation_latency": {
            "status": "separate_zero_model_benchmark_required",
            "promotion_gate": False,
        },
    }
    economics = _read_object(grading_root / "economics.json")
    panel_final_manifest = _read_object(
        panel_root / "panel_verdicts.manifest.json"
    )
    result = a11b_grading.assemble_result(
        question_ids=coverage["question_ids"],
        questions=questions,
        gold=gold,
        labels=labels,
        mechanism_outcomes=mechanism,
        answer_behavior_outcomes=behavior,
        economics={
            "answers": economics,
            "panel": panel_final_manifest["panel_token_usage"],
        },
        input_hashes={
            "controller_manifest_sha256": CONTROLLER_SHA256,
            "public_manifest_sha256": controller["inputs"][
                "public_manifest_sha256"
            ],
            "audit_manifest_sha256": controller["inputs"][
                "audit_manifest_sha256"
            ],
            "answer_export_manifest_sha256": _sha256(
                (grading_root / "manifest.json").read_bytes()
            ),
            "grading_manifest_sha256": _sha256(
                (grading_root / "manifest.json").read_bytes()
            ),
            "panel_manifest_sha256": _sha256(
                (panel_root / "manifest.json").read_bytes()
            ),
        },
    )
    result.update(
        {
            "registered": False,
            "confirmatory_use_prohibited": True,
            "registered_analysis_function_replayed": True,
            "status": "completed_unregistered_exploratory_analysis",
            "source_preview": "a11b-r3-unregistered-exploratory-preview",
            "deterministic_normalizations": grading_manifest["normalizations"],
        }
    )
    result_bytes = _canonical(result)
    manifest = _publish_with_manifest(
        result_root,
        {"result.json": result_bytes},
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "registered": False,
            "confirmatory_use_prohibited": True,
            "controller_manifest_sha256": CONTROLLER_SHA256,
            "bundle_sha256": BUNDLE_SHA256,
            "adapter_source_sha256": _source_sha256(),
            "model_calls_during_finalization": 0,
            "status": result["status"],
            "exploratory_promotion_assessment": result["promotion_assessment"],
            "all_checks_passed": True,
        },
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("prepare", "panel", "status", "finalize", "run-all")
    )
    parser.add_argument("--controller", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--preview-root", required=True, type=Path)
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--codex-bin", required=True, type=Path)
    args = parser.parse_args()
    common = {
        "controller_path": args.controller,
        "bundle_path": args.bundle,
        "preview_root": args.preview_root,
    }
    if args.command == "prepare":
        result = prepare_grading(**common, audit_root=args.audit_root)
    elif args.command == "panel":
        result = run_panel(**common, codex_bin=args.codex_bin)
    elif args.command == "status":
        result = panel_status(**common, codex_bin=args.codex_bin)
    elif args.command == "finalize":
        result = finalize(
            **common, audit_root=args.audit_root, codex_bin=args.codex_bin
        )
    else:
        prepare_grading(**common, audit_root=args.audit_root)
        result = run_panel(**common, codex_bin=args.codex_bin)
        if result.get("complete") is True:
            result = finalize(
                **common, audit_root=args.audit_root, codex_bin=args.codex_bin
            )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        UnregisteredPostprocessError,
        run_a11b_panel.PanelProtocolError,
    ) as exc:
        print(
            json.dumps({"error": str(exc), "registered": False}, sort_keys=True),
            file=os.sys.stderr,
        )
        raise SystemExit(1)
