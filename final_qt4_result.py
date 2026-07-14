#!/usr/bin/env python3
"""Assemble the sealed QT-4 contrasts and token/packet economics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qt4_analysis import ARM_NAMES, ArmArtifacts, assemble_result


def _arms(args: argparse.Namespace) -> dict[str, ArmArtifacts]:
    return {
        arm: ArmArtifacts(
            name=arm,
            packet_path=getattr(args, f"{arm}_packets"),
            run_dir=getattr(args, f"{arm}_dir"),
        )
        for arm in ARM_NAMES
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-manifest", type=Path, required=True)
    parser.add_argument("--question-spec", type=Path, required=True)
    parser.add_argument(
        "--input", type=Path, default=Path("final_dataset/full_test409.csv")
    )
    for arm in ARM_NAMES:
        parser.add_argument(f"--{arm}-packets", type=Path, required=True)
        parser.add_argument(f"--{arm}-dir", type=Path, required=True)
    parser.add_argument(
        "--grading-dir", type=Path, default=Path("runs/qt4-micro42-grading")
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=10_000)
    args = parser.parse_args()
    try:
        result = assemble_result(
            controller_manifest=args.controller_manifest,
            question_spec=args.question_spec,
            input_path=args.input,
            arms=_arms(args),
            grading_dir=args.grading_dir,
            n_boot=args.bootstrap_replicates,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print((args.grading_dir / "final_result.txt").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "json": str(args.grading_dir / "final_result.json"),
                "text": str(args.grading_dir / "final_result.txt"),
                "registered_contrasts": result["registered_contrasts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
