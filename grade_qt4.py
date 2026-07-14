#!/usr/bin/env python3
"""Prepare the sealed QT-4 deterministic verdicts and one blinded panel queue.

This command spends no model quota. Run ``panel_grade.py`` on the emitted
``panel_queue.jsonl`` before invoking ``final_qt4_result.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qt4_analysis import ARM_NAMES, ArmArtifacts, prepare_grading


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
    parser.add_argument("--input", type=Path, required=True)
    for arm in ARM_NAMES:
        parser.add_argument(f"--{arm}-packets", type=Path, required=True)
        parser.add_argument(f"--{arm}-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = prepare_grading(
            controller_manifest=args.controller_manifest,
            question_spec=args.question_spec,
            input_path=args.input,
            arms=_arms(args),
            out_dir=args.out,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "sealed_answers": manifest["sealed_completion"]["accepted_answers"],
                "panel_queue": manifest["outputs"]["panel_queue_count"],
                "out": str(args.out),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
