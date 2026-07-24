#!/usr/bin/env python3
"""Prospective analytic power screen from the corrected C3G addendum."""

from __future__ import annotations

import argparse
import json
import math
from statistics import NormalDist


SCHEMA_VERSION = "c3g-analytic-power-screen-v1"


def analytic_screen(
    *,
    discordance_upper: float,
    contrast_icc: float,
    mean_cluster_size: float,
    cluster_size_cv: float,
    delta: float = 0.08,
    family_alpha: float = 0.05,
    holm_family_size: int = 3,
    target_power: float = 0.80,
) -> dict[str, float | int | str]:
    if not 0 < delta < 1:
        raise ValueError("delta must be between zero and one")
    if not delta <= discordance_upper <= 1:
        raise ValueError("discordance_upper must be at least delta and at most one")
    if not 0 < family_alpha < 1 or holm_family_size < 1:
        raise ValueError("Holm family configuration is invalid")
    if not 0 < target_power < 1:
        raise ValueError("target_power must be between zero and one")
    if mean_cluster_size < 1 or cluster_size_cv < 0:
        raise ValueError("cluster layout parameters are invalid")

    alpha = family_alpha / holm_family_size
    z_alpha = NormalDist().inv_cdf(1 - alpha)
    z_power = NormalDist().inv_cdf(target_power)
    variance_term = max(discordance_upper, delta) - delta**2
    n_iid = math.ceil(((z_alpha + z_power) ** 2 * variance_term) / delta**2)
    nonnegative_icc = max(contrast_icc, 0.0)
    design_effect = 1 + (
        ((1 + cluster_size_cv**2) * mean_cluster_size) - 1
    ) * nonnegative_icc
    n_required = math.ceil(n_iid * design_effect)
    return {
        "schema_version": SCHEMA_VERSION,
        "delta": delta,
        "family_alpha": family_alpha,
        "holm_family_size": holm_family_size,
        "first_step_one_sided_alpha": alpha,
        "target_power": target_power,
        "discordance_upper": discordance_upper,
        "contrast_icc_input": contrast_icc,
        "contrast_icc_used": nonnegative_icc,
        "mean_cluster_size": mean_cluster_size,
        "cluster_size_cv": cluster_size_cv,
        "z_one_minus_alpha": z_alpha,
        "z_target_power": z_power,
        "n_iid": n_iid,
        "design_effect": design_effect,
        "n_required": n_required,
        "decision": "analytic_screen_only_monte_carlo_still_required",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discordance-upper", type=float, required=True)
    parser.add_argument("--contrast-icc", type=float, required=True)
    parser.add_argument("--mean-cluster-size", type=float, required=True)
    parser.add_argument("--cluster-size-cv", type=float, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            analytic_screen(
                discordance_upper=args.discordance_upper,
                contrast_icc=args.contrast_icc,
                mean_cluster_size=args.mean_cluster_size,
                cluster_size_cv=args.cluster_size_cv,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
