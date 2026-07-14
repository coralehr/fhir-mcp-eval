from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import a6_packet_builder as a6
import compile_evidence


class PromotedEvidenceRecipeTests(unittest.TestCase):
    def test_promoted_recipe_resolves_to_vocabulary_without_traversal(self) -> None:
        features = a6.resolve_evidence_recipe(
            a6.PROMOTED_EVIDENCE_RECIPE,
            explicit_features=frozenset(),
            planner="question-only",
        )

        self.assertEqual(features, frozenset({"micro-vocab"}))
        self.assertNotIn("micro-traversal", features)

    def test_recipe_and_explicit_features_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            a6.resolve_evidence_recipe(
                a6.PROMOTED_EVIDENCE_RECIPE,
                explicit_features={"micro-vocab"},
                planner="question-only",
            )

    def test_historical_explicit_feature_path_is_unchanged(self) -> None:
        features = a6.resolve_evidence_recipe(
            None,
            explicit_features={"micro-vocab", "micro-traversal"},
            planner="question-only",
        )

        self.assertEqual(features, frozenset({"micro-vocab", "micro-traversal"}))

    def test_product_entrypoint_defaults_to_promoted_recipe(self) -> None:
        self.assertEqual(
            compile_evidence.DEFAULT_EVIDENCE_RECIPE,
            a6.PROMOTED_EVIDENCE_RECIPE,
        )
        repo = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "questions.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "question_id",
                        "split",
                        "question",
                        "assumption",
                        "patient_fhir_id",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "question_id": "synthetic-1",
                        "split": "test",
                        "question": "What organism was identified?",
                        "assumption": "Synthetic non-PHI row",
                        "patient_fhir_id": "synthetic-patient",
                    }
                )

            product_manifest = root / "product-manifest.json"
            product = subprocess.run(
                [
                    sys.executable,
                    str(repo / "compile_evidence.py"),
                    "--input",
                    str(input_path),
                    "--output",
                    str(root / "product.jsonl"),
                    "--manifest",
                    str(product_manifest),
                    "--plan-only",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(product.returncode, 0, product.stderr)
            product_config = json.loads(product_manifest.read_text())["config"]
            self.assertEqual(
                product_config["evidence_recipe"]["id"],
                a6.PROMOTED_EVIDENCE_RECIPE,
            )
            self.assertEqual(product_config["features"], ["micro-vocab"])
            self.assertIsNone(product_config["reference_traversal"])

            legacy_manifest = root / "legacy-manifest.json"
            legacy = subprocess.run(
                [
                    sys.executable,
                    str(repo / "a6_packet_builder.py"),
                    "--input",
                    str(input_path),
                    "--output",
                    str(root / "legacy.jsonl"),
                    "--manifest",
                    str(legacy_manifest),
                    "--plan-only",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            legacy_config = json.loads(legacy_manifest.read_text())["config"]
            self.assertNotIn("evidence_recipe", legacy_config)
            self.assertEqual(legacy_config["features"], [])

            conflict = subprocess.run(
                [
                    sys.executable,
                    str(repo / "compile_evidence.py"),
                    "--input",
                    str(input_path),
                    "--output",
                    str(root / "conflict.jsonl"),
                    "--manifest",
                    str(root / "conflict-manifest.json"),
                    "--plan-only",
                    "--features",
                    "micro-vocab",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("mutually exclusive", conflict.stderr)


if __name__ == "__main__":
    unittest.main()
