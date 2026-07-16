#!/usr/bin/env python3
"""Deterministic zero-model prospective power gate for A11b.

The gate intentionally accepts only design assumptions. It has no interface for
patient IDs, packets, gold, labels, answers, or observed arm outcomes.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from a11_evidence_core import canonical_bytes, sha256


POWER_SPEC_VERSION = "a11b-power-spec-v1"
POWER_RECEIPT_VERSION = "a11b-power-receipt-v1"
POWER_ALGORITHM = "exact-conditional-mcnemar-v1"
PRIMARY_CONTRAST = (
    "e1_event_groups_with_identical_aids-minus-"
    "t1_flat_traversal_with_aids"
)
SECONDARY_CONTRAST = (
    "t1_flat_traversal_with_aids-minus-t0_flat_traversal"
)
_SPEC_FIELDS = {
    "algorithm",
    "balance_multiple",
    "contrast_alpha",
    "development_patients",
    "discordance_ceiling",
    "discordance_ceiling_basis",
    "discordance_grid",
    "familywise_alpha",
    "max_efficacy_patients",
    "minimum_effect",
    "model_calls",
    "multiplicity_policy",
    "patient_cluster_policy",
    "primary_contrast",
    "schema_version",
    "secondary_contrast",
    "target_power",
}
_CONTRAST_KEYS = {"primary", "secondary"}
_PATIENT_POLICY_FIELDS = {
    "efficacy_questions_per_patient",
    "patient_assignment",
}
_DISCORDANCE_BASIS_FIELDS = {
    "observed_discordant_pairs",
    "observed_pairs",
    "prior_result_document_sha256",
    "prior_result_manifest_sha256",
    "rationale",
}
_DEPENDENCY_FILES = (
    "a11_evidence_core.py",
    "a11b_power_gate.py",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _number(value: object, name: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def spec_sha256(spec: object) -> str:
    """Hash the exact candidate spec without treating it as valid."""

    return sha256(canonical_bytes(spec))


def _dependency_receipt() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parent
    receipts = []
    for relative in sorted(_DEPENDENCY_FILES):
        payload = (root / relative).read_bytes()
        receipts.append(
            {"path": relative, "sha256": sha256(payload), "bytes": len(payload)}
        )
    return receipts


def _binomial_probabilities(n: int, probability: float) -> list[float]:
    """Return a stable normalized Binomial(n, probability) PMF."""

    if n < 0 or not 0.0 <= probability <= 1.0:
        raise ValueError("invalid binomial parameters")
    if probability == 0.0:
        return [1.0] + [0.0] * n
    if probability == 1.0:
        return [0.0] * n + [1.0]
    mode = min(n, int(math.floor((n + 1) * probability)))
    log_mode = (
        math.lgamma(n + 1)
        - math.lgamma(mode + 1)
        - math.lgamma(n - mode + 1)
        + mode * math.log(probability)
        + (n - mode) * math.log1p(-probability)
    )
    values = [0.0] * (n + 1)
    values[mode] = math.exp(log_mode)
    odds_down = (1.0 - probability) / probability
    for index in range(mode, 0, -1):
        values[index - 1] = (
            values[index]
            * index
            / (n - index + 1)
            * odds_down
        )
    odds_up = probability / (1.0 - probability)
    for index in range(mode, n):
        values[index + 1] = (
            values[index]
            * (n - index)
            / (index + 1)
            * odds_up
        )
    total = math.fsum(values)
    if not total:
        raise ValueError("binomial probability underflow")
    return [value / total for value in values]


@lru_cache(maxsize=None)
def _critical_treatment_only(discordant_pairs: int, alpha: float) -> int | None:
    """Smallest favorable discordant count rejected by exact two-sided McNemar."""

    probabilities = _binomial_probabilities(discordant_pairs, 0.5)
    cumulative: list[float] = []
    running = 0.0
    for probability in probabilities:
        running += probability
        cumulative.append(running)
    for reference_only in range((discordant_pairs - 1) // 2, -1, -1):
        if min(1.0, 2.0 * cumulative[reference_only]) < alpha:
            return discordant_pairs - reference_only
    return None


@lru_cache(maxsize=None)
def _conditional_rejection_probability(
    discordant_pairs: int, favorable_share: float, alpha: float
) -> float:
    critical = _critical_treatment_only(discordant_pairs, alpha)
    if critical is None:
        return 0.0
    probabilities = _binomial_probabilities(discordant_pairs, favorable_share)
    return math.fsum(probabilities[critical:])


@lru_cache(maxsize=None)
def exact_conditional_mcnemar_power(
    n_pairs: int,
    *,
    effect: float,
    discordance: float,
    alpha: float,
) -> float:
    """Exact power for a favorable paired difference under fixed discordance.

    `effect` is P(treatment-only) - P(reference-only), while `discordance` is
    their sum. The calculation marginalizes the exact conditional McNemar test
    over the Binomial number of discordant pairs.
    """

    if type(n_pairs) is not int or n_pairs <= 0:
        raise ValueError("n_pairs must be a positive integer")
    effect = _number(effect, "effect")
    discordance = _number(discordance, "discordance")
    alpha = _number(alpha, "alpha")
    if not 0.0 < effect <= discordance < 1.0:
        raise ValueError("discordance cannot be below effect")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between zero and one")
    favorable_share = (discordance + effect) / (2.0 * discordance)
    discordant_probabilities = _binomial_probabilities(n_pairs, discordance)
    return math.fsum(
        probability
        * _conditional_rejection_probability(
            discordant_pairs, favorable_share, alpha
        )
        for discordant_pairs, probability in enumerate(
            discordant_probabilities
        )
    )


def _validate_spec(spec: object) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) != _SPEC_FIELDS:
        raise ValueError("power spec fields are invalid")
    if spec.get("schema_version") != POWER_SPEC_VERSION:
        raise ValueError("power spec version is invalid")
    if spec.get("algorithm") != POWER_ALGORITHM:
        raise ValueError("power algorithm is invalid")
    if spec.get("model_calls") != 0:
        raise ValueError("power spec must have zero model calls")
    if spec.get("primary_contrast") != PRIMARY_CONTRAST:
        raise ValueError("primary contrast is invalid")
    if spec.get("secondary_contrast") != SECONDARY_CONTRAST:
        raise ValueError("secondary contrast is invalid")
    if spec.get("multiplicity_policy") != "fixed-equal-alpha-split":
        raise ValueError("multiplicity policy is invalid")

    familywise_alpha = _number(spec.get("familywise_alpha"), "familywise alpha")
    target_power = _number(spec.get("target_power"), "target power")
    if not 0.0 < familywise_alpha < 1.0 or not 0.0 < target_power < 1.0:
        raise ValueError("power target or familywise alpha is invalid")
    contrast_alpha = spec.get("contrast_alpha")
    if not isinstance(contrast_alpha, dict) or set(contrast_alpha) != _CONTRAST_KEYS:
        raise ValueError("alpha split fields are invalid")
    alpha_values = {
        key: _number(value, f"{key} alpha")
        for key, value in contrast_alpha.items()
    }
    if (
        not all(0.0 < value < 1.0 for value in alpha_values.values())
        or not math.isclose(
            math.fsum(alpha_values.values()), familywise_alpha, abs_tol=1e-15
        )
        or not math.isclose(
            alpha_values["primary"], alpha_values["secondary"], abs_tol=1e-15
        )
    ):
        raise ValueError("alpha split is invalid")

    effects = spec.get("minimum_effect")
    if not isinstance(effects, dict) or set(effects) != _CONTRAST_KEYS:
        raise ValueError("minimum effect fields are invalid")
    effect_values = {
        key: _number(value, f"{key} minimum effect")
        for key, value in effects.items()
    }
    if not all(0.0 < value < 1.0 for value in effect_values.values()):
        raise ValueError("minimum effect is invalid")

    grid = spec.get("discordance_grid")
    if not isinstance(grid, list) or not grid:
        raise ValueError("discordance grid is invalid")
    discordance_values = [
        _number(value, "discordance grid value") for value in grid
    ]
    ceiling = _number(spec.get("discordance_ceiling"), "discordance ceiling")
    basis = spec.get("discordance_ceiling_basis")
    if not isinstance(basis, dict) or set(basis) != _DISCORDANCE_BASIS_FIELDS:
        raise ValueError("discordance ceiling basis is invalid")
    observed = basis.get("observed_discordant_pairs")
    observed_pairs = basis.get("observed_pairs")
    if (
        type(observed) is not int
        or type(observed_pairs) is not int
        or observed < 0
        or observed_pairs <= 0
        or observed > observed_pairs
        or not isinstance(basis.get("rationale"), str)
        or not basis["rationale"].strip()
        or _SHA256.fullmatch(str(basis.get("prior_result_document_sha256") or ""))
        is None
        or _SHA256.fullmatch(str(basis.get("prior_result_manifest_sha256") or ""))
        is None
    ):
        raise ValueError("discordance ceiling basis is invalid")
    if (
        discordance_values != sorted(set(discordance_values))
        or any(not 0.0 < value < 1.0 for value in discordance_values)
        or any(
            value < effect
            for value in discordance_values
            for effect in effect_values.values()
        )
    ):
        if any(
            value < effect
            for value in discordance_values
            for effect in effect_values.values()
        ):
            raise ValueError("discordance cannot be below effect")
        raise ValueError("discordance grid is invalid")
    if not 0.0 < ceiling < 1.0 or not math.isclose(
        max(discordance_values), ceiling, abs_tol=1e-15
    ):
        raise ValueError("discordance ceiling is invalid")

    balance = spec.get("balance_multiple")
    development = spec.get("development_patients")
    maximum = spec.get("max_efficacy_patients")
    if type(balance) is not int or balance <= 0:
        raise ValueError("balance multiple is invalid")
    if type(development) is not int or development <= 0 or development % balance:
        raise ValueError("development patient balance is invalid")
    if type(maximum) is not int or maximum < balance:
        raise ValueError("maximum efficacy patients is invalid")

    patient_policy = spec.get("patient_cluster_policy")
    if (
        not isinstance(patient_policy, dict)
        or set(patient_policy) != _PATIENT_POLICY_FIELDS
    ):
        raise ValueError("patient cluster policy is invalid")
    if patient_policy.get("efficacy_questions_per_patient") != 1:
        raise ValueError("power gate requires one efficacy question per patient")
    if patient_policy.get("patient_assignment") != "domain-separated-sha256-order-v1":
        raise ValueError("patient assignment policy is invalid")

    return {
        "familywise_alpha": familywise_alpha,
        "target_power": target_power,
        "alpha": alpha_values,
        "effect": effect_values,
        "discordance_grid": discordance_values,
        "discordance_ceiling": ceiling,
        "discordance_ceiling_basis": copy.deepcopy(basis),
        "balance_multiple": balance,
        "development_patients": development,
        "max_efficacy_patients": maximum,
    }


def _registered_powers(
    n_pairs: int, validated: dict[str, Any], *, rounded: bool
) -> dict[str, list[dict[str, float]]]:
    return {
        contrast: [
            {
                "discordance": discordance,
                "power": (
                    round(power, 12) if rounded else power
                ),
            }
            for discordance in validated["discordance_grid"]
            for power in (
                exact_conditional_mcnemar_power(
                    n_pairs,
                    effect=validated["effect"][contrast],
                    discordance=discordance,
                    alpha=validated["alpha"][contrast],
                ),
            )
        ]
        for contrast in ("primary", "secondary")
    }


def _minimum_power(powers: dict[str, list[dict[str, float]]]) -> float:
    return min(
        cell["power"]
        for contrast in powers.values()
        for cell in contrast
    )


def compile_power_receipt(spec: object) -> dict[str, Any]:
    """Compile a prospective receipt without accepting any efficacy artifact."""

    validated = _validate_spec(spec)
    selected: int | None = None
    selected_raw_powers: dict[str, list[dict[str, float]]] | None = None
    for candidate in range(
        validated["balance_multiple"],
        validated["max_efficacy_patients"] + 1,
        validated["balance_multiple"],
    ):
        powers = _registered_powers(candidate, validated, rounded=False)
        if _minimum_power(powers) >= validated["target_power"]:
            selected = candidate
            selected_raw_powers = powers
            break
    if selected is None or selected_raw_powers is None:
        raise ValueError("no balanced sample size reaches target power")
    predecessor = selected - validated["balance_multiple"]
    predecessor_power = (
        0.0
        if predecessor <= 0
        else _minimum_power(
            _registered_powers(predecessor, validated, rounded=False)
        )
    )
    achieved = _minimum_power(selected_raw_powers)
    limiting = [
        {"contrast": contrast, "discordance": cell["discordance"]}
        for contrast, cells in selected_raw_powers.items()
        for cell in cells
        if math.isclose(cell["power"], achieved, abs_tol=1e-12)
    ]
    selected_powers = _registered_powers(selected, validated, rounded=True)
    assert isinstance(spec, dict)
    return {
        "schema_version": POWER_RECEIPT_VERSION,
        "spec_sha256": spec_sha256(spec),
        "algorithm_receipt": {
            "algorithm": POWER_ALGORITHM,
            "dependencies": _dependency_receipt(),
            "inference": "exact_two_sided_mcnemar",
            "simulation_replicates": 0,
        },
        "multiplicity": {
            "policy": spec["multiplicity_policy"],
            "familywise_alpha": validated["familywise_alpha"],
            "contrast_alpha": validated["alpha"],
        },
        "minimum_effect": validated["effect"],
        "target_power": validated["target_power"],
        "discordance_ceiling": validated["discordance_ceiling"],
        "discordance_ceiling_basis": validated["discordance_ceiling_basis"],
        "registered_power": selected_powers,
        "limiting_cells": limiting,
        "achieved_minimum_power": achieved,
        "predecessor_minimum_power": predecessor_power,
        "balance_multiple": validated["balance_multiple"],
        "required_efficacy_patients": selected,
        "required_development_patients": validated["development_patients"],
        "required_source_patients": selected + validated["development_patients"],
        "efficacy_questions_per_patient": 1,
        "patient_assignment": spec["patient_cluster_policy"]["patient_assignment"],
        "efficacy_artifacts_opened": False,
        "model_calls": 0,
    }


def verify_power_receipt(spec: object, receipt: object) -> None:
    expected = compile_power_receipt(spec)
    if not isinstance(receipt, dict) or canonical_bytes(receipt) != canonical_bytes(
        expected
    ):
        raise ValueError("power receipt does not match the frozen spec and algorithm")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--spec", type=Path, required=True)
    compile_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--spec", type=Path, required=True)
    verify_parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    spec = _load_json(args.spec)
    if args.command == "compile":
        receipt = compile_power_receipt(spec)
        args.output.write_bytes(
            json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        )
    else:
        verify_power_receipt(spec, _load_json(args.receipt))


if __name__ == "__main__":
    main()
