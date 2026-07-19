"""Deterministic grounding metrics for future A11b component screens."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import a11b_answer_contract
import a11b_successor_development_grading
from a11_evidence_core import canonical_bytes, resource_ref, sha256


GROUNDING_VERSION = "a11b-grounding-metrics-v1"


def _fraction(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _gold_index(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = row.get("question_id")
        answerable = row.get("answerable")
        terminal = row.get("selected_terminal_resource_ref")
        path = row.get("selected_path_refs")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in result
            or type(answerable) is not bool
            or not isinstance(path, list)
            or any(not isinstance(ref, str) or not ref for ref in path)
            or len(path) != len(set(path))
            or (
                answerable
                and (not isinstance(terminal, str) or terminal not in path)
            )
            or (not answerable and (terminal is not None or path))
        ):
            raise ValueError("grounding gold contract is invalid")
        result[question_id] = dict(row)
    if not result:
        raise ValueError("grounding gold is empty")
    return result


def _registered_values(values: Sequence[str], *, label: str) -> tuple[str, ...]:
    result = tuple(values)
    if (
        not result
        or len(result) != len(set(result))
        or any(not isinstance(value, str) or not value for value in result)
    ):
        raise ValueError(f"registered grounding {label} are invalid")
    return result


def _packet_index(
    rows: list[Mapping[str, Any]],
    *,
    expected: set[tuple[str, str]],
    registered_receipts: Mapping[tuple[str, str], str],
) -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        if set(row) != {"question_id", "arm", "packet"}:
            raise ValueError("grounding packet fields changed")
        question_id = row.get("question_id")
        arm = row.get("arm")
        identity = (question_id, arm)
        packet = row.get("packet")
        if (
            identity not in expected
            or identity in result
            or not isinstance(packet, Mapping)
            or packet.get("schema_version") != "a11b-component-screen-v1"
            or registered_receipts.get(identity) != sha256(canonical_bytes(packet))
        ):
            raise ValueError("grounding packet identity or receipt is invalid")
        evidence = packet.get("evidence")
        resources = evidence.get("resources") if isinstance(evidence, Mapping) else None
        if not isinstance(resources, list):
            raise ValueError("grounding packet evidence is invalid")
        try:
            refs = [resource_ref(resource) for resource in resources]
        except (KeyError, TypeError) as exc:
            raise ValueError("grounding packet resource is invalid") from exc
        if len(refs) != len(set(refs)):
            raise ValueError("grounding packet has duplicate resources")
        result[(str(question_id), str(arm))] = set(refs)
    if set(result) != expected:
        raise ValueError("grounding packet coverage is incomplete")
    return result


def _receipt_index(
    rows: list[Mapping[str, Any]],
    *,
    expected: set[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for row in rows:
        if set(row) != {"question_id", "arm", "packet_sha256"}:
            raise ValueError("registered packet receipt fields changed")
        identity = (row.get("question_id"), row.get("arm"))
        digest = row.get("packet_sha256")
        if (
            identity not in expected
            or identity in result
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("registered packet receipt is invalid")
        result[(str(identity[0]), str(identity[1]))] = digest
    if set(result) != expected:
        raise ValueError("registered packet receipt coverage is incomplete")
    return result


def compile_grounding_report(
    *,
    gold_rows: list[Mapping[str, Any]],
    accepted_answers: list[Mapping[str, Any]],
    packet_rows: list[Mapping[str, Any]],
    registered_packet_receipts: list[Mapping[str, Any]],
    registered_question_ids: Sequence[str],
    registered_arms: Sequence[str],
) -> dict[str, Any]:
    """Score citation and answerability behavior without a judge or model call."""

    question_ids = _registered_values(
        registered_question_ids, label="question identities"
    )
    arms = _registered_values(registered_arms, label="arms")
    gold = _gold_index(gold_rows)
    if set(gold) != set(question_ids):
        raise ValueError("grounding gold coverage differs from registration")
    expected = {
        (question_id, arm) for question_id in question_ids for arm in arms
    }
    receipts = _receipt_index(registered_packet_receipts, expected=expected)
    visible = _packet_index(
        packet_rows, expected=expected, registered_receipts=receipts
    )
    for question_id, arm in expected:
        if not set(gold[question_id]["selected_path_refs"]).issubset(
            visible[(question_id, arm)]
        ):
            raise ValueError("registered selected path is not packet-visible")

    answers: dict[tuple[str, str], dict[str, Any]] = {}
    for row in accepted_answers:
        if set(row) != {"question_id", "arm", "answer"}:
            raise ValueError("grounding answer fields changed")
        identity = (row.get("question_id"), row.get("arm"))
        if identity not in visible or identity in answers:
            raise ValueError("grounding answer coverage changed")
        answer = row.get("answer")
        if not isinstance(answer, Mapping):
            raise ValueError("grounding answer is invalid")
        answers[(str(identity[0]), str(identity[1]))] = (
            a11b_answer_contract.validate_answer(answer)
        )
    if set(answers) != set(visible):
        raise ValueError("grounding answer coverage is incomplete")

    outcomes: list[dict[str, Any]] = []
    for question_id, arm in sorted(answers):
        answer = answers[(question_id, arm)]
        row = gold[question_id]
        answerable = bool(row["answerable"])
        cited = set(answer["source_resource_ids"])
        path = set(row["selected_path_refs"])
        path_supported = cited & path
        invalid = cited - visible[(question_id, arm)]
        terminal_hit = (
            row["selected_terminal_resource_ref"] in cited
            if answerable
            else None
        )
        citation_supported = (
            bool(terminal_hit) and not invalid if answerable else None
        )
        correct = a11b_successor_development_grading.is_correct(
            gold=row, answer=answer
        )
        outcomes.append(
            {
                "answerability_state_correct": answer["status"]
                == (
                    a11b_answer_contract.ANSWERED
                    if answerable
                    else a11b_answer_contract.INSUFFICIENT
                ),
                "arm": arm,
                "citation_precision": (
                    _fraction(len(path_supported), len(cited))
                    if answerable
                    else None
                ),
                "citation_recall": (
                    _fraction(len(path_supported), len(path))
                    if answerable
                    else None
                ),
                "citation_supported": citation_supported,
                "correct": correct,
                "correct_and_citation_supported": (
                    correct and bool(citation_supported)
                    if answerable
                    else None
                ),
                "invalid_citation_count": len(invalid),
                "question_id": question_id,
                "any_selected_path_ref_hit": (
                    bool(path_supported) if answerable else None
                ),
                "full_selected_path_coverage": (
                    len(path_supported) == len(path) if answerable else None
                ),
                "selected_terminal_hit": terminal_hit,
                "unsupported_correct": (
                    correct and not bool(citation_supported)
                    if answerable
                    else None
                ),
            }
        )

    by_arm_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        by_arm_rows[row["arm"]].append(row)
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in sorted(by_arm_rows):
        rows = by_arm_rows[arm]
        answerable_rows = [
            row for row in rows if row["selected_terminal_hit"] is not None
        ]
        cited_total = sum(
            row["citation_precision"]["denominator"]
            for row in answerable_rows
        )
        supported_total = sum(
            row["citation_precision"]["numerator"]
            for row in answerable_rows
        )
        expected_path_total = sum(
            row["citation_recall"]["denominator"] for row in answerable_rows
        )
        path_hit_total = sum(
            row["citation_recall"]["numerator"] for row in answerable_rows
        )
        invalid_total = sum(row["invalid_citation_count"] for row in rows)
        all_citations_total = sum(
            len(answers[(row["question_id"], arm)]["source_resource_ids"])
            for row in rows
        )
        by_arm[arm] = {
            "answerability_state_correct": _fraction(
                sum(row["answerability_state_correct"] for row in rows),
                len(rows),
            ),
            "citation_precision": _fraction(supported_total, cited_total),
            "citation_recall": _fraction(path_hit_total, expected_path_total),
            "correct": _fraction(sum(row["correct"] for row in rows), len(rows)),
            "correct_and_citation_supported": _fraction(
                sum(
                    bool(row["correct_and_citation_supported"])
                    for row in answerable_rows
                ),
                len(answerable_rows),
            ),
            "invalid_citation_rate": _fraction(
                invalid_total, all_citations_total
            ),
            "any_selected_path_ref_hit": _fraction(
                sum(
                    bool(row["any_selected_path_ref_hit"])
                    for row in answerable_rows
                ),
                len(answerable_rows),
            ),
            "full_selected_path_coverage": _fraction(
                sum(
                    bool(row["full_selected_path_coverage"])
                    for row in answerable_rows
                ),
                len(answerable_rows),
            ),
            "selected_terminal_hit": _fraction(
                sum(bool(row["selected_terminal_hit"]) for row in answerable_rows),
                len(answerable_rows),
            ),
            "unsupported_correct": _fraction(
                sum(bool(row["unsupported_correct"]) for row in answerable_rows),
                len(answerable_rows),
            ),
        }
    return {
        "schema_version": GROUNDING_VERSION,
        "arms": sorted(arms),
        "by_arm": by_arm,
        "outcomes": outcomes,
        "model_calls": 0,
    }
