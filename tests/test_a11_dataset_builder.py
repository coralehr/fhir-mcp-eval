from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import a6_packet_builder as a6
from a11_dataset_builder import (
    AUGMENTATION_SEED,
    DATASET_VERSION,
    FROZEN_PROFILE_SHA256,
    PROVENANCE_VERSION,
    _audit_case,
    build_dataset,
    inspect_source,
    verify_dataset,
)
from a11_event_group_benchmark import (
    A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION,
    A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION,
    A11_NORMALIZED_EVENT_RANK_VERSION,
    plan_question,
    rank_event_roots,
)
from a11_packet_adapter import _recipe_contract


def _write_archive(path: Path, patient_count: int = 115) -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": f"synthetic-{index:03d}",
                }
            }
            for index in range(patient_count)
        ],
    }
    info = zipfile.ZipInfo("fhir/patients.json")
    info.date_time = (2020, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr(
            info,
            json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode(),
        )


def _provenance(source: dict) -> dict:
    return {
        "schema_version": PROVENANCE_VERSION,
        "source_kind": "release_generation",
        "release_tag": "synthetic-test-profile",
        "generator_commit": "2" * 40,
        "jar_sha256": "3" * 64,
        "seed": 42,
        "population": 115,
        "configuration_sha256": "4" * 64,
        "raw_output_sha256": source["content_sha256"],
        "entry_manifest_sha256": source["entry_manifest_sha256"],
        "content_sha256": source["content_sha256"],
        "augmentation_seed": AUGMENTATION_SEED,
        "augmentation_config_sha256": FROZEN_PROFILE_SHA256,
    }


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class A11DatasetBuilderTests(unittest.TestCase):
    def _build(self, root: Path) -> tuple[Path, dict]:
        archive = root / "sample.zip"
        _write_archive(archive)
        source, _ = inspect_source(archive)
        provenance_path = root / "provenance.json"
        provenance_path.write_text(json.dumps(_provenance(source), sort_keys=True))
        output = root / "sealed"
        manifest = build_dataset(archive, provenance_path, output)
        return output, manifest

    def test_public_builder_seals_exact_profile_without_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self._build(Path(directory))
            questions = _read_jsonl(output / "questions.jsonl")
            gold = _read_jsonl(output / "gold.jsonl")
            source = _read_jsonl(output / "source_corpus.jsonl")
            policies = _read_jsonl(output / "policy_contexts.jsonl")
            audit = json.loads((output / "zero_model_audit.json").read_text())

            self.assertEqual(manifest["schema_version"], DATASET_VERSION)
            self.assertEqual(manifest["model_calls"], 0)
            self.assertTrue(manifest["deterministic_rebuild"])
            self.assertEqual(len(questions), 144)
            self.assertEqual(len(source), 144)
            self.assertEqual(len(gold), 144)
            self.assertEqual(len(policies), 144)
            self.assertEqual(audit["development_patients"], 15)
            self.assertEqual(audit["efficacy_patients"], 100)
            self.assertEqual(audit["patient_split_overlap"], 0)
            self.assertEqual(audit["efficacy_temporal"], {"first": 60, "latest": 60})
            self.assertEqual(audit["efficacy_unanswerable"], 24)
            self.assertEqual(
                audit["efficacy_failure_modes"],
                {
                    "bound_exhaustion": 6,
                    "missing": 6,
                    "out_of_scope": 6,
                    "stale_version": 6,
                },
            )
            self.assertEqual(set(audit["family_depth_cells"].values()), {3, 15})
            efficacy_counts = {}
            for row in questions:
                if row["split"] == "efficacy":
                    efficacy_counts[row["patient_fhir_id"]] = efficacy_counts.get(row["patient_fhir_id"], 0) + 1
                self.assertEqual(
                    row["question_plan"]["path_depth"], row["depth"]
                )
                self.assertEqual(
                    row["evidence_recipe"], a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE
                )
            self.assertEqual(list(efficacy_counts.values()).count(1), 80)
            self.assertEqual(list(efficacy_counts.values()).count(2), 20)

            source_text = (output / "source_corpus.jsonl").read_text().lower()
            for forbidden in ("reference_answer", "failure_mode", "terminal_ref", "gold_answer"):
                self.assertNotIn(f'"{forbidden}"', source_text)
            for case in source:
                for entry in case["resources"]:
                    self.assertTrue(entry["resource"]["meta"]["versionId"])
                    self.assertEqual(
                        entry["resource"]["subject"]["reference"],
                        case["patient_ref"],
                    )
                self.assertIn("/_history/", json.dumps(case, sort_keys=True))
            for question, policy in zip(questions, policies, strict=True):
                rendered = json.dumps(
                    policy, sort_keys=True, separators=(",", ":")
                ).encode()
                self.assertEqual(
                    question["policy_context_sha256"],
                    __import__("hashlib").sha256(rendered).hexdigest(),
                )

    def test_rebuilds_are_byte_identical_and_manifest_is_path_stable(self) -> None:
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first, _ = self._build(Path(first_directory))
            second, _ = self._build(Path(second_directory))
            self.assertEqual(
                {path.name: path.read_bytes() for path in first.iterdir()},
                {path.name: path.read_bytes() for path in second.iterdir()},
            )
            manifest = json.loads((first / "manifest.json").read_text())
            serialized = json.dumps(manifest)
            self.assertNotIn(first_directory, serialized)
            self.assertNotIn(second_directory, serialized)
            self.assertNotIn("created_at", manifest)

    def test_manifest_and_artifact_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self._build(Path(directory))
            pinned = (output / "manifest.sha256").read_text().strip()
            verify_dataset(output, expected_manifest_sha256=pinned)
            questions = output / "questions.jsonl"
            questions.write_bytes(questions.read_bytes() + b"{}\n")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                verify_dataset(output, expected_manifest_sha256=pinned)

    def test_insufficient_patients_and_provenance_drift_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "sample.zip"
            _write_archive(archive, patient_count=99)
            source, _ = inspect_source(archive)
            provenance = _provenance(source)
            provenance_path = root / "provenance.json"
            provenance_path.write_text(json.dumps(provenance, sort_keys=True))
            with self.assertRaisesRegex(ValueError, "exactly 115"):
                build_dataset(archive, provenance_path, root / "sealed")

            provenance["unexpected"] = True
            provenance_path.write_text(json.dumps(provenance, sort_keys=True))
            with self.assertRaisesRegex(ValueError, "fields changed"):
                build_dataset(archive, provenance_path, root / "sealed-two")

    def test_shorter_terminal_route_and_v_alias_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self._build(Path(directory))
            sources = _read_jsonl(output / "source_corpus.jsonl")
            questions = _read_jsonl(output / "questions.jsonl")
            gold = _read_jsonl(output / "gold.jsonl")
            index = next(
                position
                for position, (question, answer) in enumerate(zip(questions, gold, strict=True))
                if question["family"] == "observation_finding"
                and question["depth"] == 3
                and answer["answerable"]
            )
            source = copy.deepcopy(sources[index])
            question = questions[index]
            answer = gold[index]
            selected = next(
                entry["resource"]
                for entry in source["resources"]
                if f"{entry['resource']['resourceType']}/{entry['resource']['id']}"
                == answer["selected_root_ref"]
            )
            terminal = next(
                entry["resource"]
                for entry in source["resources"]
                if f"{entry['resource']['resourceType']}/{entry['resource']['id']}"
                == answer["terminal_resource_ref"]
            )
            selected["hasMember"].append(
                {
                    "reference": (
                        f"{answer['terminal_resource_ref']}/_history/"
                        f"{terminal['meta']['versionId']}"
                    )
                }
            )
            with self.assertRaisesRegex(ValueError, "exactly one registered route"):
                _audit_case(source, question, answer)

            aliased = copy.deepcopy(sources[index])
            selected = next(
                entry["resource"]
                for entry in aliased["resources"]
                if f"{entry['resource']['resourceType']}/{entry['resource']['id']}"
                == answer["selected_root_ref"]
            )
            selected["code"]["text"] = answer["reference_answer"]["display"]
            with self.assertRaisesRegex(ValueError, "answer alias"):
                _audit_case(aliased, question, answer)

    def test_depth_aware_planner_and_recipe_are_version_isolated(self) -> None:
        depth_two = "What organism was found in the latest culture Observation?"
        depth_three = (
            "What organism was found in the latest culture Observation "
            "through an intermediate observation?"
        )
        historical = plan_question(
            depth_two, version=A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION
        )
        self.assertNotIn("path_depth", historical)
        self.assertEqual(len(historical["path_signatures"][0]), 2)
        depth_plan = plan_question(
            depth_three, version=A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION
        )
        self.assertEqual(depth_plan["path_depth"], 3)
        self.assertEqual(
            depth_plan["path_signatures"],
            [["Observation.hasMember", "Observation.hasMember", "Observation.hasMember"]],
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            plan_question(
                "What organism was found in the latest culture Observation via an intermediate node?",
                version=A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION,
            )
        self.assertEqual(
            a6.question_only_planner_version(a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE),
            a6.A11_QO_PLANNER_VERSION,
        )
        self.assertEqual(
            a6.evidence_recipe_contract(a6.A11_EVIDENCE_RECIPE).recipe_id,
            "a11-four-family-v1",
        )
        self.assertEqual(
            _recipe_contract(a6.A11_EVIDENCE_RECIPE)["question_planner_version"],
            A11_FOUR_FAMILY_QUESTION_PLANNER_VERSION,
        )
        self.assertEqual(
            _recipe_contract(a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE)[
                "question_planner_version"
            ],
            A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION,
        )

    def test_normalized_utc_rank_uses_resource_type_and_id_tie_break(self) -> None:
        resources = {
            "Observation/b": {
                "resourceType": "Observation",
                "id": "b",
                "effectiveDateTime": "2100-01-01T08:00:00-05:00",
            },
            "Observation/a": {
                "resourceType": "Observation",
                "id": "a",
                "effectiveDateTime": "2100-01-01T13:00:00Z",
            },
        }
        ranked, missing = rank_event_roots(
            resources,
            ["Observation/b", "Observation/a"],
            version=A11_NORMALIZED_EVENT_RANK_VERSION,
        )
        self.assertFalse(missing)
        self.assertEqual([row[3] for row in ranked], ["Observation/a", "Observation/b"])


if __name__ == "__main__":
    unittest.main()
