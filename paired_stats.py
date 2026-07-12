#!/usr/bin/env python3
"""Paired statistics for the A6a confirmatory analysis (prereg §6).

Exact paired McNemar (two-sided binomial on discordant pairs) and a
patient-cluster bootstrap CI on the accuracy difference. Pure stdlib —
deterministic, testable, no scipy dependency.
"""

from __future__ import annotations

import math
import random
from typing import Iterable


def exact_mcnemar_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value.

    b = pairs where arm A is correct and arm B wrong; c = the reverse.
    Under H0 discordant pairs are Binomial(b+c, 0.5).
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # two-sided: 2 * P(X <= k), capped at 1
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2.0**n)
    return min(1.0, 2.0 * tail)


def cluster_bootstrap_ci(
    pairs: Iterable[tuple[str, int, int]],
    *,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20260712,
) -> dict:
    """Percentile bootstrap CI on mean(acc_A - acc_B), resampling clusters.

    `pairs` = iterable of (cluster_id, a_correct, b_correct) with 0/1 values.
    Clusters (patients) are resampled with replacement; per-replicate the
    difference is recomputed over the resampled questions. Seeded for
    reproducibility (recorded in output).
    """
    by_cluster: dict[str, list[tuple[int, int]]] = {}
    for cid, a, b in pairs:
        by_cluster.setdefault(str(cid), []).append((int(a), int(b)))
    clusters = sorted(by_cluster)
    if not clusters:
        raise ValueError("no pairs")

    def diff_for(cluster_sample: list[str]) -> float:
        num_a = num_b = n = 0
        for cid in cluster_sample:
            for a, b in by_cluster[cid]:
                num_a += a
                num_b += b
                n += 1
        return (num_a - num_b) / n if n else 0.0

    point = diff_for(clusters)
    rng = random.Random(seed)
    reps = []
    for _ in range(n_boot):
        sample = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        reps.append(diff_for(sample))
    reps.sort()

    def pct(q: float) -> float:
        idx = q * (len(reps) - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        frac = idx - lo
        return reps[lo] * (1 - frac) + reps[hi] * frac

    return {
        "point_diff": point,
        "ci_low": pct(alpha / 2),
        "ci_high": pct(1 - alpha / 2),
        "alpha": alpha,
        "n_boot": n_boot,
        "n_clusters": len(clusters),
        "n_pairs": sum(len(v) for v in by_cluster.values()),
        "seed": seed,
    }


def paired_summary(pairs: list[tuple[str, int, int]], *, n_boot: int = 10_000, seed: int = 20260712) -> dict:
    """Full prereg §6 summary for (cluster_id, a_correct, b_correct) pairs."""
    n = len(pairs)
    a_acc = sum(a for _, a, _ in pairs) / n
    b_acc = sum(b for _, _, b in pairs) / n
    b_only = sum(1 for _, a, b in pairs if a == 1 and b == 0)
    c_only = sum(1 for _, a, b in pairs if a == 0 and b == 1)
    return {
        "n": n,
        "acc_a": a_acc,
        "acc_b": b_acc,
        "diff": a_acc - b_acc,
        "discordant_a_only": b_only,
        "discordant_b_only": c_only,
        "mcnemar_p": exact_mcnemar_p(b_only, c_only),
        "cluster_bootstrap": cluster_bootstrap_ci(pairs, n_boot=n_boot, seed=seed),
    }
