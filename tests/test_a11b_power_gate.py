from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from unittest import mock

import a11b_power_gate as gate
from paired_stats import exact_mcnemar_p


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "fixtures" / "a11b_power_spec.json"


def _spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


class A11bPowerGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = gate.compile_power_receipt(_spec())

    def test_frozen_spec_derives_balanced_independent_patient_count(self) -> None:
        receipt = self.receipt
        self.assertEqual(receipt["required_efficacy_patients"], 384)
        self.assertEqual(receipt["required_development_patients"], 64)
        self.assertEqual(receipt["required_source_patients"], 448)
        self.assertEqual(receipt["efficacy_questions_per_patient"], 1)
        self.assertEqual(receipt["model_calls"], 0)
        self.assertGreaterEqual(receipt["achieved_minimum_power"], 0.9)
        self.assertLess(receipt["predecessor_minimum_power"], 0.9)

    def test_receipt_binds_all_registered_nuisance_cells(self) -> None:
        powers = self.receipt["registered_power"]
        self.assertEqual(set(powers), {"primary", "secondary"})
        for contrast in powers.values():
            self.assertEqual(
                [cell["discordance"] for cell in contrast],
                [0.1, 0.15, 0.2, 0.25, 0.3],
            )
            self.assertTrue(all(cell["power"] >= 0.9 for cell in contrast))
        self.assertEqual(
            self.receipt["limiting_cells"],
            [
                {"contrast": "primary", "discordance": 0.3},
                {"contrast": "secondary", "discordance": 0.3},
            ],
        )
        self.assertEqual(self.receipt["discordance_ceiling"], 0.3)
        self.assertEqual(
            self.receipt["discordance_ceiling_basis"],
            _spec()["discordance_ceiling_basis"],
        )

    def test_algorithm_receipt_binds_every_executable_dependency(self) -> None:
        dependencies = self.receipt["algorithm_receipt"]["dependencies"]
        self.assertEqual(
            [dependency["path"] for dependency in dependencies],
            ["a11_evidence_core.py", "a11b_power_gate.py"],
        )
        self.assertTrue(
            all(
                len(dependency["sha256"]) == 64 and dependency["bytes"] > 0
                for dependency in dependencies
            )
        )
        tampered = copy.deepcopy(dependencies)
        tampered[0]["sha256"] = "0" * 64
        with mock.patch.object(gate, "_dependency_receipt", return_value=tampered):
            with self.assertRaisesRegex(ValueError, "power receipt does not match"):
                gate.verify_power_receipt(_spec(), self.receipt)

    def test_exact_power_is_monotone_in_n_for_registered_cell(self) -> None:
        at_368 = gate.exact_conditional_mcnemar_power(
            368, effect=0.1, discordance=0.3, alpha=0.025
        )
        at_384 = gate.exact_conditional_mcnemar_power(
            384, effect=0.1, discordance=0.3, alpha=0.025
        )
        self.assertLess(at_368, 0.9)
        self.assertGreaterEqual(at_384, 0.9)
        self.assertGreater(at_384, at_368)

    def test_declared_maximum_has_no_numeric_overflow(self) -> None:
        power = gate.exact_conditional_mcnemar_power(
            1024, effect=0.1, discordance=0.3, alpha=0.025
        )
        self.assertGreater(power, 0.9)
        self.assertLessEqual(power, 1.0)

    def test_exact_power_matches_independent_multinomial_enumeration(self) -> None:
        n_pairs = 20
        treatment_only_rate = 0.25
        reference_only_rate = 0.05
        discordance = treatment_only_rate + reference_only_rate
        direct = 0.0
        for treatment_only in range(n_pairs + 1):
            for reference_only in range(n_pairs - treatment_only + 1):
                concordant = n_pairs - treatment_only - reference_only
                probability = (
                    math.factorial(n_pairs)
                    / (
                        math.factorial(treatment_only)
                        * math.factorial(reference_only)
                        * math.factorial(concordant)
                    )
                    * treatment_only_rate**treatment_only
                    * reference_only_rate**reference_only
                    * (1.0 - discordance) ** concordant
                )
                if (
                    treatment_only > reference_only
                    and exact_mcnemar_p(treatment_only, reference_only) < 0.05
                ):
                    direct += probability
        conditional = gate.exact_conditional_mcnemar_power(
            n_pairs,
            effect=treatment_only_rate - reference_only_rate,
            discordance=discordance,
            alpha=0.05,
        )
        self.assertTrue(math.isclose(direct, conditional, abs_tol=1e-14))

    def test_compile_is_byte_deterministic_and_verifiable(self) -> None:
        left = gate.compile_power_receipt(_spec())
        right = gate.compile_power_receipt(_spec())
        self.assertEqual(gate.canonical_bytes(left), gate.canonical_bytes(right))
        gate.verify_power_receipt(_spec(), left)

    def test_tampered_receipt_fails_verification(self) -> None:
        receipt = copy.deepcopy(self.receipt)
        receipt["required_efficacy_patients"] -= 1
        with self.assertRaisesRegex(ValueError, "power receipt does not match"):
            gate.verify_power_receipt(_spec(), receipt)

    def test_spec_changes_invalidate_the_receipt(self) -> None:
        for mutate in (
            lambda value: value["minimum_effect"].__setitem__("primary", 0.11),
            lambda value: value.__setitem__("target_power", 0.85),
            lambda value: value["contrast_alpha"].__setitem__("primary", 0.02),
            lambda value: value["discordance_grid"].append(0.35),
            lambda value: value["discordance_ceiling_basis"].__setitem__(
                "observed_discordant_pairs", 2
            ),
            lambda value: value.__setitem__("balance_multiple", 32),
        ):
            changed = _spec()
            mutate(changed)
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    gate.spec_sha256(changed), self.receipt["spec_sha256"]
                )

    def test_rejects_post_treatment_or_efficacy_identifiers(self) -> None:
        for field, value in (
            ("efficacy_patient_ids", ["Patient/secret"]),
            ("gold", ["answer"]),
            ("packets", [{"id": "q1"}]),
            ("failure_mode", "missing"),
            ("prior_answers", ["text"]),
        ):
            spec = _spec()
            spec[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "power spec fields"):
                    gate.compile_power_receipt(spec)

    def test_rejects_patient_reuse_and_invalid_alpha_family(self) -> None:
        reused = _spec()
        reused["patient_cluster_policy"]["efficacy_questions_per_patient"] = 2
        with self.assertRaisesRegex(ValueError, "one efficacy question per patient"):
            gate.compile_power_receipt(reused)

        alpha = _spec()
        alpha["contrast_alpha"]["secondary"] = 0.026
        with self.assertRaisesRegex(ValueError, "alpha split"):
            gate.compile_power_receipt(alpha)

    def test_rejects_underpowered_or_unbalanced_search_space(self) -> None:
        too_small = _spec()
        too_small["max_efficacy_patients"] = 383
        with self.assertRaisesRegex(ValueError, "no balanced sample size"):
            gate.compile_power_receipt(too_small)

        invalid_balance = _spec()
        invalid_balance["development_patients"] = 63
        with self.assertRaisesRegex(ValueError, "development patient balance"):
            gate.compile_power_receipt(invalid_balance)

    def test_threshold_uses_unrounded_power(self) -> None:
        exact = gate.exact_conditional_mcnemar_power(
            384, effect=0.1, discordance=0.3, alpha=0.025
        )
        spec = _spec()
        spec["target_power"] = exact + 1e-13
        spec["max_efficacy_patients"] = 384
        with self.assertRaisesRegex(ValueError, "no balanced sample size"):
            gate.compile_power_receipt(spec)

    def test_rejects_invalid_discordance_and_algorithm(self) -> None:
        invalid = _spec()
        invalid["discordance_grid"] = [0.05]
        with self.assertRaisesRegex(ValueError, "discordance cannot be below effect"):
            gate.compile_power_receipt(invalid)

        invalid = _spec()
        invalid["algorithm"] = "simulation"
        with self.assertRaisesRegex(ValueError, "power algorithm"):
            gate.compile_power_receipt(invalid)

        invalid = _spec()
        invalid["discordance_ceiling"] = 0.4
        with self.assertRaisesRegex(ValueError, "discordance ceiling"):
            gate.compile_power_receipt(invalid)


if __name__ == "__main__":
    unittest.main()
