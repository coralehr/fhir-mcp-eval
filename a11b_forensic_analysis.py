#!/usr/bin/env python3
"""Content-free post-result reliability and mechanism audit for sealed A11b."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import a11b_grading


RAW_AUDIT_VERSION = "a11b-raw-forensic-audit-v1"
FINAL_AUDIT_VERSION = "a11b-final-forensic-report-v1"
EXPECTED_QUESTIONS = 384
EXPECTED_CALLS = 1152
EXPECTED_ARMS = ("t0", "t1", "e1")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_PROMPT_PATTERNS = {
    "gold_field": re.compile(
        rb'"(?:reference_answer|nonselected_reference_answer|failure_mode|answerable|'
        rb'terminal_resource_ref|selected_root_ref|patient_cluster_sha256)"\s*:'
    ),
    "gold_phrase": re.compile(rb"(?i)\b(?:gold answer|correct answer|benchmark label)\b"),
    "arm_identity": re.compile(rb"(?i)(?:\barm\s*[:=]|\b(?:T0|T1|E1)\s+arm\b)"),
}
ALLOWED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "item.completed",
    "turn.completed",
}


class ForensicError(ValueError):
    """A required receipt or content-free integrity condition failed."""


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ForensicError(f"JSON artifact is not an object: {path.name}")
    return value


def _receipt(payload: bytes) -> dict[str, Any]:
    return {"sha256": _sha256(payload), "bytes": len(payload)}


def _controller(path: Path) -> tuple[dict[str, Any], str]:
    payload = path.read_bytes()
    digest = _sha256(payload)
    if path.with_suffix(".sha256").read_text(encoding="ascii") != digest + "\n":
        raise ForensicError("controller sidecar changed")
    value = json.loads(payload)
    schedule = value.get("schedule") if isinstance(value, dict) else None
    items = schedule.get("items") if isinstance(schedule, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "a11-controller-v4"
        or value.get("experiment_profile") != "a11b-causal-isolation-v2"
        or value.get("inputs", {}).get("question_count") != EXPECTED_QUESTIONS
        or value.get("inputs", {}).get("answer_calls") != EXPECTED_CALLS
        or not isinstance(schedule, dict)
        or schedule.get("arms") != list(EXPECTED_ARMS)
        or not isinstance(items, list)
        or len(items) != EXPECTED_CALLS
    ):
        raise ForensicError("controller is not the registered A11b controller")
    schedule_indices = []
    arm_question_pairs = []
    for item in items:
        if not isinstance(item, dict):
            raise ForensicError("controller schedule item is not an object")
        index = item.get("schedule_index")
        arm = item.get("arm")
        question_id = item.get("question_id")
        if (
            type(index) is not int
            or arm not in EXPECTED_ARMS
            or not isinstance(question_id, str)
            or not question_id
            or not HEX64.fullmatch(str(item.get("prompt_sha256", "")))
        ):
            raise ForensicError("controller schedule item changed")
        schedule_indices.append(index)
        arm_question_pairs.append((arm, question_id))
    question_counts = Counter(question_id for _arm, question_id in arm_question_pairs)
    arm_counts = Counter(arm for arm, _question_id in arm_question_pairs)
    if (
        schedule_indices != list(range(EXPECTED_CALLS))
        or arm_counts != Counter({arm: EXPECTED_QUESTIONS for arm in EXPECTED_ARMS})
        or len(question_counts) != EXPECTED_QUESTIONS
        or set(question_counts.values()) != {len(EXPECTED_ARMS)}
        or len(set(arm_question_pairs)) != EXPECTED_CALLS
    ):
        raise ForensicError("controller schedule coverage changed")
    return value, digest


def _complete_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict) or value.get("complete") is not True:
        return None
    usage = {
        key: value.get(key)
        for key in ("input", "cached", "output", "reasoning", "total")
    }
    if (
        any(type(item) is not int or item < 0 for item in usage.values())
        or usage["cached"] > usage["input"]
        or usage["reasoning"] > usage["output"]
        or usage["total"] != usage["input"] + usage["output"]
    ):
        return None
    return usage


def _empty_usage_by_arm() -> dict[str, dict[str, int]]:
    return {
        arm: {key: 0 for key in ("input", "cached", "output", "reasoning", "total")}
        for arm in EXPECTED_ARMS
    }


def _add_usage(target: dict[str, int], usage: Mapping[str, int]) -> None:
    for key in target:
        target[key] += usage[key]


def audit_raw_execution(
    *, controller_path: Path, bundle_path: Path, executor_export_path: Path
) -> dict[str, Any]:
    """Scan sealed prompts and accepted event streams without retaining content."""

    controller, controller_sha = _controller(controller_path)
    bundle_payload = bundle_path.read_bytes()
    bundle = json.loads(bundle_payload)
    if not isinstance(bundle, dict):
        raise ForensicError("sealed invocation bundle is not an object")
    invocations = bundle.get("invocations")
    if (
        bundle.get("schema_version") != "experiment-executor-service-bundle-v1"
        or bundle.get("run_id") != controller.get("run_id")
        or not isinstance(invocations, list)
        or len(invocations) != EXPECTED_CALLS
    ):
        raise ForensicError("sealed invocation bundle is incomplete")
    schedule = controller["schedule"]["items"]
    prompt_mismatches = 0
    leakage_counts = {name: 0 for name in FORBIDDEN_PROMPT_PATTERNS}
    prompt_bytes = 0
    for index, (host, invocation) in enumerate(zip(schedule, invocations, strict=True)):
        if invocation.get("schedule_index") != index:
            raise ForensicError("invocation order changed")
        try:
            prompt = base64.b64decode(invocation["prompt_base64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ForensicError("sealed prompt encoding changed") from exc
        prompt_bytes += len(prompt)
        prompt_mismatches += int(_sha256(prompt) != host.get("prompt_sha256"))
        for name, pattern in FORBIDDEN_PROMPT_PATTERNS.items():
            leakage_counts[name] += len(pattern.findall(prompt))

    export_payload = executor_export_path.read_bytes()
    exported = json.loads(export_payload)
    if not isinstance(exported, dict):
        raise ForensicError("executor export is not an object")
    attempts = exported.get("attempts")
    if (
        exported.get("schema_version") != "experiment-run-export-v1"
        or exported.get("run_id") != controller.get("run_id")
        or not isinstance(attempts, list)
        or exported.get("schedule_length") != EXPECTED_CALLS
        or exported.get("accepted_slots") != EXPECTED_CALLS
        or exported.get("model_calls_reserved") != len(attempts)
        or exported.get("model_calls_closed") != len(attempts)
    ):
        raise ForensicError("executor export is not an exact completed run")
    by_slot: dict[int, list[dict[str, Any]]] = {}
    unknown_usage_attempts = 0
    accepted_usage_by_arm = _empty_usage_by_arm()
    all_usage_by_arm = _empty_usage_by_arm()
    attempts_by_arm = {arm: {"accepted": 0, "all": 0} for arm in EXPECTED_ARMS}
    for attempt in attempts:
        descriptor = attempt.get("descriptor") if isinstance(attempt, dict) else None
        index = descriptor.get("schedule_index") if isinstance(descriptor, dict) else None
        if type(index) is not int or not 0 <= index < EXPECTED_CALLS:
            raise ForensicError("executor attempt has an invalid schedule index")
        by_slot.setdefault(index, []).append(attempt)
        arm = schedule[index]["arm"]
        usage = _complete_usage(attempt.get("token_usage"))
        attempts_by_arm[arm]["all"] += 1
        if usage is None:
            unknown_usage_attempts += 1
        else:
            _add_usage(all_usage_by_arm[arm], usage)
            if attempt.get("outcome") == "accepted":
                _add_usage(accepted_usage_by_arm[arm], usage)
        if attempt.get("outcome") == "accepted":
            attempts_by_arm[arm]["accepted"] += 1
    if set(by_slot) != set(range(EXPECTED_CALLS)):
        raise ForensicError("executor attempt coverage is incomplete")

    accepted_events = 0
    event_parse_errors = 0
    tool_or_nonmessage_events = 0
    nonempty_accepted_stderr = 0
    multiple_usage_receipts = 0
    unexpected_agent_message_counts = 0
    event_usage_mismatches = 0
    for index in range(EXPECTED_CALLS):
        slot = sorted(
            by_slot[index], key=lambda row: row["descriptor"].get("attempt_number", -1)
        )
        attempt_numbers = [row["descriptor"].get("attempt_number") for row in slot]
        if (
            attempt_numbers != list(range(1, len(slot) + 1))
            or any(row.get("outcome") != "provider_failure" for row in slot[:-1])
            or slot[-1].get("outcome") != "accepted"
        ):
            raise ForensicError("executor attempt history changed")
        row = slot[-1]
        encoded = row.get("artifact_base64")
        if not isinstance(encoded, str):
            raise ForensicError("accepted artifact bytes are unavailable")
        raw = base64.b64decode(encoded, validate=True)
        if _receipt(raw) != {
            "sha256": row.get("artifact_sha256"),
            "bytes": row.get("artifact_bytes"),
        }:
            raise ForensicError("accepted artifact receipt changed")
        artifact = json.loads(raw)
        capture = artifact.get("capture") if isinstance(artifact, dict) else None
        files = capture.get("files_base64") if isinstance(capture, dict) else None
        if not isinstance(files, dict) or set(files) != {
            "answer.json",
            "events.jsonl",
            "stderr.log",
        }:
            raise ForensicError("accepted capture inventory changed")
        decoded = {
            name: base64.b64decode(value, validate=True) for name, value in files.items()
        }
        nonempty_accepted_stderr += int(bool(decoded["stderr.log"]))
        usage_receipts = 0
        agent_messages = 0
        event_usage: dict[str, int] | None = None
        for line in decoded["events.jsonl"].splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                event_parse_errors += 1
                continue
            accepted_events += 1
            event_type = event.get("type") if isinstance(event, dict) else None
            if event_type not in ALLOWED_EVENT_TYPES:
                tool_or_nonmessage_events += 1
            if event_type == "item.completed":
                item = event.get("item")
                if not isinstance(item, dict) or item.get("type") != "agent_message":
                    tool_or_nonmessage_events += 1
                else:
                    agent_messages += 1
            if event_type == "turn.completed":
                usage_receipts += 1
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    names = {
                        "input": "input_tokens",
                        "cached": "cached_input_tokens",
                        "output": "output_tokens",
                        "reasoning": "reasoning_output_tokens",
                    }
                    if all(type(raw_usage.get(source)) is int for source in names.values()):
                        event_usage = {
                            target: raw_usage[source] for target, source in names.items()
                        }
                        event_usage["total"] = (
                            event_usage["input"] + event_usage["output"]
                        )
        multiple_usage_receipts += int(usage_receipts != 1)
        unexpected_agent_message_counts += int(agent_messages != 1)
        event_usage_mismatches += int(
            event_usage != _complete_usage(row.get("token_usage"))
        )

    failures = {
        "prompt_hash_mismatches": prompt_mismatches,
        "prompt_leakage_matches": sum(leakage_counts.values()),
        "event_parse_errors": event_parse_errors,
        "tool_or_nonmessage_events": tool_or_nonmessage_events,
        "nonempty_accepted_stderr": nonempty_accepted_stderr,
        "accepted_slots_without_exactly_one_usage_receipt": multiple_usage_receipts,
        "accepted_slots_without_exactly_one_agent_message": (
            unexpected_agent_message_counts
        ),
        "event_executor_usage_mismatches": event_usage_mismatches,
        "unknown_usage_attempts": unknown_usage_attempts,
    }
    report = {
        "schema_version": RAW_AUDIT_VERSION,
        "controller_manifest_sha256": controller_sha,
        "bundle_sha256": _sha256(bundle_payload),
        "executor_export_sha256": _sha256(export_payload),
        "prompt_count": EXPECTED_CALLS,
        "prompt_bytes": prompt_bytes,
        "accepted_event_count": accepted_events,
        "attempts": len(attempts),
        "accepted_slots": EXPECTED_CALLS,
        "accepted_token_usage_by_arm": accepted_usage_by_arm,
        "all_attempt_token_usage_by_arm": all_usage_by_arm,
        "attempts_by_arm": attempts_by_arm,
        "leakage_match_counts": leakage_counts,
        "failure_counts": failures,
        "answer_content_retained": False,
        "all_checks_passed": not any(failures.values()),
    }
    return report


def _verify_result_root(root: Path, controller_sha: str) -> dict[str, Any]:
    manifest = _read_json(root / "manifest.json")
    artifacts = manifest.get("artifacts")
    if (
        manifest.get("schema_version") != "a11b-final-result-manifest-v1"
        or manifest.get("controller_manifest_sha256") != controller_sha
        or manifest.get("all_checks_passed") is not True
        or not isinstance(artifacts, dict)
        or set(artifacts) != {"result.json"}
    ):
        raise ForensicError("final result manifest changed")
    payload = (root / "result.json").read_bytes()
    if _receipt(payload) != artifacts["result.json"]:
        raise ForensicError("final result receipt changed")
    result = json.loads(payload)
    question_ids = result.get("question_ids") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("status") != "completed_registered_analysis"
        or result.get("arms") != list(EXPECTED_ARMS)
        or result.get("registered_contrasts") != ["e1_minus_t1", "t1_minus_t0"]
        or not isinstance(question_ids, list)
        or len(question_ids) != EXPECTED_QUESTIONS
        or len(set(question_ids)) != EXPECTED_QUESTIONS
    ):
        raise ForensicError("final result is not the registered A11b analysis")
    return result


def analyze_final_result(
    *, controller_path: Path, result_root: Path, raw_audit_path: Path
) -> dict[str, Any]:
    controller, controller_sha = _controller(controller_path)
    raw = _read_json(raw_audit_path)
    if (
        raw.get("schema_version") != RAW_AUDIT_VERSION
        or raw.get("controller_manifest_sha256") != controller_sha
        or raw.get("all_checks_passed") is not True
        or raw.get("answer_content_retained") is not False
    ):
        raise ForensicError("raw no-cheating audit is absent or failed")
    result = _verify_result_root(result_root, controller_sha)
    accuracy = result.get("accuracy_by_arm")
    if not isinstance(accuracy, dict) or set(accuracy) != set(EXPECTED_ARMS):
        raise ForensicError("final arm accuracy coverage changed")
    if any(row.get("n") != EXPECTED_QUESTIONS for row in accuracy.values()):
        raise ForensicError("final arm denominator changed")
    behavior = result.get("answer_behavior_outcomes")
    if not isinstance(behavior, dict):
        raise ForensicError("answer behavior outcomes changed")
    safety = a11b_grading.safety_comparisons(behavior)
    contrasts = result.get("contrasts")
    if not isinstance(contrasts, dict) or set(contrasts) != {
        "e1_minus_t1",
        "t1_minus_t0",
    }:
        raise ForensicError("registered contrasts changed")
    expected_promotion = a11b_grading.promotion_assessment(
        primary=contrasts["e1_minus_t1"],
        secondary=contrasts["t1_minus_t0"],
        safety_comparisons=safety,
    )
    if result.get("promotion_assessment") != expected_promotion:
        raise ForensicError("promotion decision does not replay")
    economics_root = result.get("economics")
    if not isinstance(economics_root, dict):
        raise ForensicError("token economics changed")
    economics = economics_root.get("answers")
    if not isinstance(economics, dict):
        raise ForensicError("answer token economics changed")
    attempts = economics.get("attempts_by_arm")
    if not isinstance(attempts, dict) or attempts != raw.get("attempts_by_arm"):
        raise ForensicError("accepted token receipt coverage changed")
    if economics.get("accepted_token_usage_by_arm") != raw.get(
        "accepted_token_usage_by_arm"
    ):
        raise ForensicError("accepted token economics changed")
    if economics.get("all_attempt_token_usage_by_arm") != raw.get(
        "all_attempt_token_usage_by_arm"
    ):
        raise ForensicError("all-attempt token economics changed")
    if economics.get("all_attempt_token_economics_reconciled") is not True:
        raise ForensicError("all-attempt token economics are not reconciled")
    decision = expected_promotion["decision"]
    interpretation = {
        "promote_e1": "event_grouping_supported_beyond_identical_aids",
        "promote_t1": "deterministic_aids_supported_without_event_grouping_claim",
        "do_not_promote": "stop_event_grouping_as_answer_accuracy_thesis",
    }[decision]
    return {
        "schema_version": FINAL_AUDIT_VERSION,
        "controller_manifest_sha256": controller_sha,
        "result_manifest_sha256": _sha256((result_root / "manifest.json").read_bytes()),
        "raw_audit_sha256": _sha256(raw_audit_path.read_bytes()),
        "questions": EXPECTED_QUESTIONS,
        "answer_slots": EXPECTED_CALLS,
        "accuracy_by_arm": accuracy,
        "contrasts": contrasts,
        "answer_behavior_outcomes": behavior,
        "economics": result["economics"],
        "promotion_assessment": expected_promotion,
        "interpretation": interpretation,
        "no_cheating_checks_passed": True,
        "all_checks_passed": True,
        "answer_content_retained": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    raw = subparsers.add_parser("raw-audit")
    raw.add_argument("--controller", required=True, type=Path)
    raw.add_argument("--bundle", required=True, type=Path)
    raw.add_argument("--executor-export", required=True, type=Path)
    raw.add_argument("--output", required=True, type=Path)
    final = subparsers.add_parser("final-report")
    final.add_argument("--controller", required=True, type=Path)
    final.add_argument("--result-root", required=True, type=Path)
    final.add_argument("--raw-audit", required=True, type=Path)
    final.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "raw-audit":
        report = audit_raw_execution(
            controller_path=args.controller,
            bundle_path=args.bundle,
            executor_export_path=args.executor_export,
        )
    else:
        report = analyze_final_result(
            controller_path=args.controller,
            result_root=args.result_root,
            raw_audit_path=args.raw_audit,
        )
    args.output.write_bytes(_canonical(report))
    print(json.dumps({"all_checks_passed": report["all_checks_passed"]}))


if __name__ == "__main__":
    main()
