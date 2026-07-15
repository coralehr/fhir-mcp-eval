from __future__ import annotations

import copy
import csv
import hashlib
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a6_packet_builder as a6
import compile_evidence
from a11_event_group_benchmark import (
    A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION,
    QUESTION_PLANNER_VERSION,
    plan_question,
)
from a11_packet_adapter import load_promoted_bundle


class A11FourFamilyRecipeTests(unittest.TestCase):
    def test_unknown_recipe_and_planner_versions_fail_closed(self) -> None:
        row = {
            "question_id": "unknown-version",
            "question": "What organism was found in the latest culture?",
            "patient_fhir_id": "synthetic-patient",
            "assumption": "Synthetic non-PHI evidence.",
        }
        with self.assertRaisesRegex(ValueError, "unknown evidence recipe"):
            a6.question_only_planner_version("unregistered-recipe")
        with self.assertRaisesRegex(
            ValueError, "unknown question-only planner version"
        ):
            a6.qo_infer_intent(row, planner_version="qo-unregistered")
        with self.assertRaisesRegex(
            ValueError, "unsupported A11 question planner version"
        ):
            plan_question(row["question"], version="a11-question-plan-unregistered")
        with self.assertRaisesRegex(
            ValueError, "no registered microbiology dispatcher term"
        ):
            plan_question(
                "What finding was in the latest DiagnosticReport?",
                version=A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION,
            )

    def test_historical_recipe_and_question_planner_remain_unchanged(self) -> None:
        row = {
            "question_id": "historical-observation",
            "question": "What organism was found in the latest culture?",
            "patient_fhir_id": "synthetic-patient",
            "assumption": "Synthetic non-PHI evidence.",
        }

        self.assertEqual(
            a6.question_only_planner_version(a6.PROMOTED_EVIDENCE_RECIPE),
            a6.QO_PLANNER_VERSION,
        )
        intent = a6.qo_infer_intent(row)
        plan = a6.build_search_plan(
            row,
            intent,
            count=100,
            features={"micro-vocab"},
        )

        self.assertEqual(intent["planner"], a6.QO_PLANNER_VERSION)
        self.assertEqual({item["resource_type"] for item in plan}, {"Observation"})
        self.assertEqual(
            plan_question(row["question"])["version"],
            QUESTION_PLANNER_VERSION,
        )
        self.assertEqual(
            plan_question(row["question"])["path_signatures"],
            [["Observation.hasMember", "Observation.hasMember"]],
        )

        historical_diagnostic_row = {
            **row,
            "question_id": "historical-diagnostic-wording",
            "question": (
                "What specimen was used for the first culture DiagnosticReport?"
            ),
        }
        historical_diagnostic_intent = a6.qo_infer_intent(
            historical_diagnostic_row
        )
        self.assertNotIn(
            "DiagnosticReport",
            historical_diagnostic_intent["resource_types"],
        )

    def test_a11_recipe_reaches_all_four_registered_path_families(self) -> None:
        planner_version = a6.question_only_planner_version(a6.A11_EVIDENCE_RECIPE)
        self.assertEqual(planner_version, a6.A11_QO_PLANNER_VERSION)
        features = a6.resolve_evidence_recipe(
            a6.A11_EVIDENCE_RECIPE,
            explicit_features=frozenset(),
            planner="question-only",
        )
        self.assertEqual(features, frozenset({"micro-vocab"}))

        cases = (
            (
                "What organism was found in the latest culture Observation?",
                "Observation",
                ["Observation.hasMember", "Observation.hasMember"],
            ),
            (
                "What specimen was used for the first culture Observation?",
                "Observation",
                ["Observation.hasMember", "Observation.specimen"],
            ),
            (
                "What organism was found in the latest culture DiagnosticReport?",
                "DiagnosticReport",
                ["DiagnosticReport.result", "Observation.hasMember"],
            ),
            (
                "What specimen was used for the first culture DiagnosticReport?",
                "DiagnosticReport",
                ["DiagnosticReport.result", "Observation.specimen"],
            ),
        )

        observed_signatures = set()
        for index, (question, expected_root, expected_signature) in enumerate(cases):
            row = {
                "question_id": f"four-family-{index}",
                "question": question,
                "patient_fhir_id": "synthetic-patient",
                "assumption": "Synthetic non-PHI evidence.",
            }
            intent = a6.qo_infer_intent(row, planner_version=planner_version)
            plan = a6.build_search_plan(
                row,
                intent,
                count=100,
                features=features,
            )
            question_plan = plan_question(
                question,
                version=A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION,
            )

            self.assertEqual(intent["planner"], a6.A11_QO_PLANNER_VERSION)
            self.assertEqual(intent["resource_types"], [expected_root])
            self.assertEqual(
                {item["resource_type"] for item in plan},
                {expected_root},
            )
            self.assertEqual(
                question_plan["path_signatures"],
                [expected_signature],
            )
            self.assertTrue(
                all("code%3Atext" not in item["path"] for item in plan),
                "FHIR search parameters must remain unescaped at the key boundary",
            )
            self.assertTrue(
                all("code:text=" in item["path"] for item in plan),
            )
            if expected_root == "DiagnosticReport":
                self.assertTrue(
                    all("_sort=" not in item["path"] for item in plan)
                )
            observed_signatures.add(tuple(expected_signature))

        self.assertEqual(len(observed_signatures), 4)

    def test_a11_diagnostic_report_bounds_use_only_effective_time(self) -> None:
        resources = [
            {
                "resourceType": "DiagnosticReport",
                "id": "effective-earlier-issued-later",
                "effectiveDateTime": "2100-01-01T00:00:00Z",
                "issued": "2100-12-31T00:00:00Z",
            },
            {
                "resourceType": "DiagnosticReport",
                "id": "effective-later-issued-earlier",
                "effectiveDateTime": "2100-01-02T00:00:00Z",
                "issued": "2100-01-01T00:00:00Z",
            },
        ]

        kept, _ = a6.bound_resources(
            resources,
            temporal_policy="recent",
            max_total_resources=1,
            max_packet_chars=10_000,
            clinical_date=a6.a11_canonical_clinical_date,
        )
        self.assertEqual(
            [resource["id"] for resource in kept],
            ["effective-later-issued-earlier"],
        )

        with self.assertRaisesRegex(ValueError, "no canonical effective"):
            a6.bound_resources(
                [
                    {
                        "resourceType": "DiagnosticReport",
                        "id": "issued-only",
                        "issued": "2100-12-31T00:00:00Z",
                    }
                ],
                temporal_policy="recent",
                max_total_resources=1,
                max_packet_chars=10_000,
                clinical_date=a6.a11_canonical_clinical_date,
            )

        row = {
            "question_id": "a11-diagnostic-window",
            "question": (
                "What organism was found in the latest culture DiagnosticReport "
                "during the previous year?"
            ),
            "patient_fhir_id": "synthetic-patient",
            "assumption": "Assume the current time is 2101-12-31 23:59:00.",
        }
        intent = a6.qo_infer_intent(
            row, planner_version=a6.A11_QO_PLANNER_VERSION
        )
        with self.assertRaisesRegex(ValueError, "do not permit date windows"):
            a6.build_search_plan(
                row, intent, count=100, features={"micro-vocab"}
            )

    def test_actual_a11_recipe_output_is_sealed_by_the_strict_adapter(self) -> None:
        class SyntheticClient:
            @staticmethod
            def search_with_pagination(
                query_string: str, *, max_results: int | None = None
            ) -> list[dict]:
                del max_results
                if query_string.startswith("DiagnosticReport?"):
                    return [
                        {
                            "resourceType": "DiagnosticReport",
                            "id": "diagnostic-root",
                            "subject": {"reference": "Patient/synthetic-patient"},
                            "effectiveDateTime": "2100-01-02T00:00:00Z",
                            "code": {"text": "Culture report"},
                            "result": [{"reference": "Observation/culture-result"}],
                        }
                    ]
                if query_string.startswith("Observation?"):
                    return [
                        {
                            "resourceType": "Observation",
                            "id": "observation-root",
                            "subject": {"reference": "Patient/synthetic-patient"},
                            "effectiveDateTime": "2100-01-01T00:00:00Z",
                            "code": {"text": "Culture observation"},
                            "hasMember": [
                                {"reference": "Observation/culture-result"}
                            ],
                        }
                    ]
                raise AssertionError(f"unexpected query: {query_string}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "questions.csv"
            packet_path = root / "packets.jsonl"
            manifest_path = root / "manifest.json"
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
                writer.writerows(
                    [
                        {
                            "question_id": "a11-observation-organism",
                            "split": "valid",
                            "question": "What organism was found in the latest culture Observation?",
                            "assumption": "Synthetic non-PHI evidence.",
                            "patient_fhir_id": "synthetic-patient",
                        },
                        {
                            "question_id": "a11-observation-specimen",
                            "split": "valid",
                            "question": "What specimen was used for the first culture Observation?",
                            "assumption": "Synthetic non-PHI evidence.",
                            "patient_fhir_id": "synthetic-patient",
                        },
                        {
                            "question_id": "a11-diagnostic-report-organism",
                            "split": "valid",
                            "question": "What organism was found in the latest culture DiagnosticReport?",
                            "assumption": "Synthetic non-PHI evidence.",
                            "patient_fhir_id": "synthetic-patient",
                        },
                        {
                            "question_id": "a11-diagnostic-report-specimen",
                            "split": "valid",
                            "question": "What specimen was used for the first culture DiagnosticReport?",
                            "assumption": "Synthetic non-PHI evidence.",
                            "patient_fhir_id": "synthetic-patient",
                        },
                    ]
                )

            argv = [
                "compile_evidence.py",
                "--input",
                str(input_path),
                "--output",
                str(packet_path),
                "--manifest",
                str(manifest_path),
                "--split",
                "valid",
                "--evidence-recipe",
                a6.A11_EVIDENCE_RECIPE,
            ]
            with (
                mock.patch(
                    "fhir_client.get_fhir_client", return_value=SyntheticClient()
                ),
                mock.patch.object(sys, "argv", argv),
                mock.patch("builtins.print"),
            ):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(compile_evidence.__file__, run_name="__main__")
                self.assertEqual(stopped.exception.code, 0)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["config"]["evidence_recipe"],
                {
                    "id": a6.A11_EVIDENCE_RECIPE,
                    "status": "preregistered_pre_answer_a11",
                    "features": ["micro-vocab"],
                    "protocol": "docs/prereg/A11_EVENT_GROUP.md",
                },
            )
            self.assertEqual(
                manifest["config"]["planner_version"],
                a6.A11_QO_PLANNER_VERSION,
            )
            bundle = load_promoted_bundle(
                packet_path,
                manifest_path,
                expected_manifest_sha256=hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
                expected_evidence_recipe=a6.A11_EVIDENCE_RECIPE,
            )
            adapted = {
                question_id: bundle.load(question_id)
                for question_id in bundle.question_ids
            }

            with self.assertRaisesRegex(
                ValueError, "expected evidence recipe"
            ):
                load_promoted_bundle(
                    packet_path,
                    manifest_path,
                    expected_manifest_sha256=hashlib.sha256(
                        manifest_path.read_bytes()
                    ).hexdigest(),
                )

            mutations = (
                (
                    ("config", "evidence_recipe", "protocol"),
                    "docs/prereg/UNREGISTERED.md",
                    "recipe protocol",
                ),
                (
                    ("config", "evidence_recipe", "status"),
                    "promoted_without_evidence",
                    "recipe status",
                ),
                (
                    ("config", "evidence_recipe", "features"),
                    ["micro-vocab", "micro-traversal"],
                    "recipe feature",
                ),
                (
                    ("config", "planner_version"),
                    a6.QO_PLANNER_VERSION,
                    "planner version",
                ),
            )
            for path, replacement, message in mutations:
                mutated = copy.deepcopy(manifest)
                cursor = mutated
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = replacement
                manifest_path.write_text(
                    json.dumps(
                        mutated,
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, message):
                    load_promoted_bundle(
                        packet_path,
                        manifest_path,
                        expected_manifest_sha256=hashlib.sha256(
                            manifest_path.read_bytes()
                        ).hexdigest(),
                        expected_evidence_recipe=a6.A11_EVIDENCE_RECIPE,
                    )

        expectations = {
            "a11-observation-organism": (
                ["Observation/observation-root"],
                ["Observation.hasMember", "Observation.hasMember"],
            ),
            "a11-observation-specimen": (
                ["Observation/observation-root"],
                ["Observation.hasMember", "Observation.specimen"],
            ),
            "a11-diagnostic-report-organism": (
                ["DiagnosticReport/diagnostic-root"],
                ["DiagnosticReport.result", "Observation.hasMember"],
            ),
            "a11-diagnostic-report-specimen": (
                ["DiagnosticReport/diagnostic-root"],
                ["DiagnosticReport.result", "Observation.specimen"],
            ),
        }
        self.assertEqual(set(adapted), set(expectations))
        for question_id, (root_refs, signature) in expectations.items():
            record = adapted[question_id]
            self.assertEqual(record["evidence_recipe"], a6.A11_EVIDENCE_RECIPE)
            self.assertEqual(record["root_refs"], root_refs)
            self.assertEqual(
                record["question_plan"]["version"],
                A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION,
            )
            self.assertEqual(
                record["question_plan"]["path_signatures"], [signature]
            )
            self.assertEqual(
                {
                    query["resource_type"]
                    for query in record["packet"]["source_queries"]
                },
                {root_refs[0].split("/", 1)[0]},
            )


if __name__ == "__main__":
    unittest.main()
