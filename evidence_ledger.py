#!/usr/bin/env python3
"""Validate and render the canonical aggregate-only experiment evidence ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "fhir-context-evidence-ledger-v1"
STATUS_VALUES = {
    "confirmatory_promoted",
    "confirmatory_supported_not_promotion_gated",
    "confirmatory_not_promoted",
    "exploratory_advanced_to_confirmation",
    "null_not_promoted",
    "invalid_for_claims",
    "exploratory_not_promoted",
    "development_ready_not_answered",
    "sealed_pending",
    "exploratory_supported_grading_sensitivity_pending",
    "exploratory_unresolved_grading_sensitivity_pending",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LedgerError(ValueError):
    """The evidence ledger is incomplete or internally inconsistent."""


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise LedgerError("ledger must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_ledger(path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    ledger = _load(path)
    root = repo_root or path.resolve().parents[2]
    if ledger.get("schema_version") != SCHEMA_VERSION:
        raise LedgerError("ledger schema version changed")
    entries = ledger.get("experiments")
    claims = ledger.get("claim_register")
    if not isinstance(entries, list) or not entries:
        raise LedgerError("ledger has no experiments")
    if not isinstance(claims, list) or not claims:
        raise LedgerError("ledger has no claim register")
    identifiers: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise LedgerError("experiment entry is not an object")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise LedgerError("experiment identifiers must be unique non-empty strings")
        identifiers.add(identifier)
        if entry.get("status") not in STATUS_VALUES:
            raise LedgerError(f"invalid experiment status: {identifier}")
        if not isinstance(entry.get("result"), str) or not entry["result"]:
            raise LedgerError(f"experiment result summary is missing: {identifier}")
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources:
            raise LedgerError(f"experiment sources are missing: {identifier}")
        for source in sources:
            if not isinstance(source, Mapping) or not SHA256.fullmatch(
                str(source.get("sha256", ""))
            ):
                raise LedgerError(f"source receipt is invalid: {identifier}")
            source_path = source.get("path")
            if source.get("kind") == "repo_file":
                if not isinstance(source_path, str):
                    raise LedgerError(f"repo source path is missing: {identifier}")
                candidate = root / source_path
                if not candidate.is_file() or _sha256(candidate) != source["sha256"]:
                    raise LedgerError(f"repo source receipt changed: {source_path}")
            elif source.get("kind") == "external_aggregate":
                if not isinstance(source_path, str) or not source.get("verified_at"):
                    raise LedgerError(f"external source receipt is incomplete: {identifier}")
            else:
                raise LedgerError(f"unknown source kind: {identifier}")
    for claim in claims:
        if not isinstance(claim, Mapping) or claim.get("disposition") not in {
            "licensed",
            "bounded_support",
            "not_established",
            "invalidated",
            "pending",
            "exploratory_supported_pending_sensitivity",
        }:
            raise LedgerError("claim register entry is invalid")
        evidence = claim.get("evidence")
        if not isinstance(evidence, list) or any(item not in identifiers for item in evidence):
            raise LedgerError("claim references an unknown experiment")
    return ledger


def render_markdown(ledger: Mapping[str, Any]) -> str:
    lines = [
        "# Canonical experiment evidence ledger",
        "",
        f"Updated: {ledger['updated_at']}",
        "",
        "This is an aggregate-only claim ledger. It does not contain answer text, raw",
        "clinical records, credentials, or hidden chain-of-thought. Numeric claims are",
        "licensed only at the scope shown below.",
        "",
        "## Experiments",
        "",
        "| Experiment | Status | Population | Registered result | Decision |",
        "|---|---|---:|---|---|",
    ]
    for row in ledger["experiments"]:
        lines.append(
            "| {name} | `{status}` | {population} | {result} | {decision} |".format(
                name=row["name"],
                status=row["status"],
                population=row["population"],
                result=row["result"].replace("|", "\\|"),
                decision=row["decision"].replace("|", "\\|"),
            )
        )
    lines.extend(["", "## Claim register", ""])
    for claim in ledger["claim_register"]:
        lines.extend(
            [
                f"### {claim['claim']}",
                "",
                f"Disposition: **{claim['disposition']}**.",
                "",
                claim["boundary"],
                "",
                "Evidence: " + ", ".join(f"`{item}`" for item in claim["evidence"]) + ".",
                "",
            ]
        )
    lines.extend(
        [
            "## Economics receipt coverage",
            "",
            "| Experiment | Accepted tokens | All-attempt tokens | Notes |",
            "|---|---:|---:|---|",
        ]
    )
    for row in ledger["experiments"]:
        economics = row.get("economics", {})
        accepted = economics.get("accepted_tokens")
        all_attempt = economics.get("all_attempt_tokens")
        lines.append(
            f"| {row['name']} | {accepted if accepted is not None else 'not retained'} "
            f"| {all_attempt if all_attempt is not None else 'not retained'} "
            f"| {economics.get('notes', '')} |"
        )
    lines.extend(
        [
            "",
            "## Known evidence gaps",
            "",
            *[f"- {item}" for item in ledger["known_gaps"]],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "render"))
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("docs/results/EXPERIMENT_EVIDENCE_LEDGER.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/results/EXPERIMENT_EVIDENCE_LEDGER.md"),
    )
    args = parser.parse_args()
    ledger = validate_ledger(args.ledger)
    if args.command == "render":
        args.output.write_text(render_markdown(ledger), encoding="utf-8")
    print(json.dumps({"experiments": len(ledger["experiments"]), "status": "valid"}))


if __name__ == "__main__":
    main()
