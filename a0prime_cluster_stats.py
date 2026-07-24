#!/usr/bin/env python3
"""Recompute patient-clustered A0-prime matched-stratum statistics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import a0prime_verdict
import paired_stats


SCHEMA_VERSION = "a0prime-cluster-stats-v1"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "artifacts"
    / "a0prime-v1"
    / "cluster-stats.json"
)


def compute(
    artifact_path: Path,
    *,
    n_boot: int = 100_000,
    seed: int = 20260712,
) -> dict[str, Any]:
    payload = artifact_path.read_bytes()
    artifact = a0prime_verdict.load_artifact(artifact_path)
    matched = [
        row for row in artifact["questions"] if row["stratum"] == "matched"
    ]

    def contrast(field: str) -> dict[str, Any]:
        pairs = [
            (
                row["patient_fhir_id"],
                row[field],
                row["a0_correct"],
            )
            for row in matched
        ]
        return paired_stats.paired_summary(
            pairs,
            n_boot=n_boot,
            alpha=0.05,
            seed=seed,
        )

    return {
        "contrasts": {
            "a0prime_minus_a0": contrast("a0prime_correct"),
            "a5_minus_a0": contrast("a5_correct"),
        },
        "matched_patient_count": len(
            {row["patient_fhir_id"] for row in matched}
        ),
        "matched_question_count": len(matched),
        "schema_version": SCHEMA_VERSION,
        "score_artifact_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _canonical_line(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=a0prime_verdict.DEFAULT_ARTIFACT,
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--n-boot", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260712)
    args = parser.parse_args(argv)
    result = compute(args.artifact, n_boot=args.n_boot, seed=args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_line(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
