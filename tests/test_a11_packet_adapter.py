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
import codex_harness
import compile_evidence
from a11_event_group_benchmark import plan_question
from a11_packet_adapter import (
    ADAPTER_VERSION,
    PromotedBundle,
    load_promoted_bundle,
    load_promoted_record,
)


class A11PacketAdapterTests(unittest.TestCase):
    @staticmethod
    def rehash_packet(record: dict) -> None:
        packet_without_hash = {
            key: value for key, value in record["packet"].items() if key != "sha256"
        }
        canonical = json.dumps(
            packet_without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        record["packet"]["sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

    @classmethod
    def reseal_packet(cls, record: dict) -> None:
        packet = record["packet"]
        resources = packet["resources"]
        packet["resource_count"] = len(resources)
        packet["source_resource_ids"] = sorted(
            f"{resource['resourceType']}/{resource['id']}" for resource in resources
        )
        char_count = sum(
            len(
                json.dumps(
                    resource,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            for resource in resources
        )
        packet["bounds"].update(
            {
                "input_count": len(resources),
                "kept_count": len(resources),
                "dropped_count": 0,
                "char_count": char_count,
            }
        )
        packet["root_fetch_receipt"] = {
            "pre_bound_count": len(resources),
            "retained_count": len(resources),
            "dropped_count": 0,
        }
        cls.rehash_packet(record)

    @staticmethod
    def promoted_record() -> dict:
        row = {
            "question_id": "a11-adapter-synthetic-1",
            "split": "valid",
            "question": "What organism was found in the latest culture?",
            "assumption": "Synthetic non-PHI evidence dated in 2100.",
            "patient_fhir_id": "synthetic-patient-1",
        }
        intent = a6.qo_infer_intent(row)
        plan = a6.build_search_plan(
            row,
            intent,
            count=100,
            features={"micro-vocab"},
        )
        for query in plan:
            query["fetch_receipt"] = {
                "status": "ok",
                "initial_result_count": 1,
                "relaxation_attempts": [],
                "pre_bound_count": 1,
                "retained_count": 1,
                "dropped_count": 0,
            }
        resources = [
            {
                "resourceType": "Observation",
                "id": "culture-root-1",
                "subject": {"reference": "Patient/synthetic-patient-1"},
                "effectiveDateTime": "2100-01-02T00:00:00Z",
                "code": {"text": "Culture panel"},
                "hasMember": [{"reference": "Observation/organism-1"}],
            }
        ]
        return a6.build_packet_record(
            row,
            plan_only=False,
            resources_by_query={query["path"]: resources for query in plan},
            planner="question-only",
            plan=plan,
            features={"micro-vocab"},
        )

    @staticmethod
    def promoted_manifest(record: dict, packet_path: Path) -> dict:
        return {
            "created_at": "2100-01-01T00:00:00+00:00",
            "kind": "a6_query_aware_packet_manifest",
            "input": {"path": "synthetic-questions.csv", "sha256": "0" * 64},
            "output": {
                "path": str(packet_path),
                "sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest(),
            },
            "config": {
                "limit": None,
                "count": 100,
                "plan_only": False,
                "split": None,
                "question_spec": None,
                "planner": "question-only",
                "features": ["micro-vocab"],
                "evidence_recipe": {
                    "id": a6.PROMOTED_EVIDENCE_RECIPE,
                    "features": ["micro-vocab"],
                    "status": "promoted_on_qt4_valid374",
                    "promotion_result": "docs/results/QT4_VALID374_RESULT.md",
                },
                "planner_version": a6.QO_PLANNER_VERSION,
                "max_total_resources": a6.A6A_MAX_TOTAL_RESOURCES,
                "max_packet_chars": a6.A6A_MAX_PACKET_CHARS,
                "micro_vocabulary": {
                    "version": a6.MICRO_VOCABULARY_VERSION,
                    "code_text_terms": list(a6.MICRO_CODE_TEXT_TERMS),
                },
                "micro_dispatcher": {
                    "version": a6.MICRO_DISPATCHER_VERSION,
                    "question_terms": list(a6.MICRO_QUESTION_TERMS),
                },
                "reference_traversal": None,
            },
            "questions": 1,
            "packet_hashes": {
                str(record["question_id"]): (
                    record["packet"]["sha256"]
                    if isinstance(record.get("packet"), dict)
                    else "0" * 64
                )
            },
        }

    def write_sealed(
        self, root: Path, records: list[dict]
    ) -> tuple[Path, Path, dict]:
        packet_path = root / "promoted.jsonl"
        packet_path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        manifest = self.promoted_manifest(records[0], packet_path)
        manifest["questions"] = len(records)
        manifest["packet_hashes"] = {
            str(record["question_id"]): (
                record["packet"]["sha256"]
                if isinstance(record.get("packet"), dict)
                else "0" * 64
            )
            for record in records
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return packet_path, manifest_path, manifest

    @staticmethod
    def manifest_sha256(manifest_path: Path) -> str:
        return hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    @staticmethod
    def rewrite_manifest(manifest_path: Path, manifest: dict) -> None:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )

    def load_record(
        self, packet_path: Path, manifest_path: Path, question_id: str
    ) -> dict:
        return load_promoted_record(
            packet_path,
            manifest_path,
            question_id,
            expected_manifest_sha256=self.manifest_sha256(manifest_path),
        )

    @staticmethod
    def set_path(value: dict, path: tuple[str, ...], replacement: object) -> None:
        cursor = value
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = replacement

    def test_v_payload_is_exact_promoted_packet_rendering(self) -> None:
        record = self.promoted_record()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path, manifest_path, _ = self.write_sealed(root, [record])

            adapted = self.load_record(
                packet_path, manifest_path, str(record["question_id"])
            )
            expected = codex_harness.render_model_visible_packet(record["packet"])

            self.assertEqual(adapted["schema_version"], ADAPTER_VERSION)
            self.assertEqual(adapted["packet"], record["packet"])
            self.assertEqual(
                adapted["v_model_payload"],
                codex_harness.model_visible_packet(record["packet"]),
            )
            self.assertEqual(adapted["v_model_payload_json"], expected)
            self.assertNotIn("path_citations", adapted["v_model_payload"])
            self.assertEqual(
                adapted["integrity"]["model_payload_utf8_bytes"],
                len(expected.encode("utf-8")),
            )
            self.assertEqual(
                adapted["integrity"]["model_payload_sha256"],
                hashlib.sha256(expected.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                adapted["integrity"]["packet_sha256"],
                record["packet"]["sha256"],
            )
            self.assertEqual(
                set(adapted["integrity"]["dependency_sha256"]),
                {
                    "a11_evidence_core.py",
                    "a11_event_group_benchmark.py",
                    "a11_packet_adapter.py",
                    "a6_packet_builder.py",
                    "codex_harness.py",
                    "compile_evidence.py",
                },
            )

    def test_actual_product_entrypoint_output_loads_without_network(self) -> None:
        class SyntheticClient:
            @staticmethod
            def search_with_pagination(
                query_string: str, *, max_results: int | None = None
            ) -> list[dict]:
                del query_string, max_results
                return [
                    {
                        "resourceType": "Observation",
                        "id": "product-root-1",
                        "subject": {
                            "reference": "Patient/synthetic-product-patient"
                        },
                        "effectiveDateTime": "2100-02-03T00:00:00Z",
                        "code": {"text": "Culture panel"},
                    }
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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
                        "question_id": "product-entrypoint-1",
                        "split": "valid",
                        "question": "What organism was found in the latest culture?",
                        "assumption": "Synthetic non-PHI evidence dated in 2100.",
                        "patient_fhir_id": "synthetic-product-patient",
                    }
                )
            packet_path = root / "product.jsonl"
            manifest_path = root / "product-manifest.json"
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
            ]
            with (
                mock.patch("fhir_client.get_fhir_client", return_value=SyntheticClient()),
                mock.patch.object(sys, "argv", argv),
                mock.patch("builtins.print"),
            ):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(compile_evidence.__file__, run_name="__main__")
                self.assertEqual(stopped.exception.code, 0)

            adapted = self.load_record(
                packet_path, manifest_path, "product-entrypoint-1"
            )

        self.assertEqual(adapted["packet"]["features"], ["micro-vocab"])
        self.assertEqual(
            adapted["root_refs"], ["Observation/product-root-1"]
        )

    def test_current_product_planner_cannot_supply_diagnostic_report_roots(self) -> None:
        row = {
            "question_id": "producer-feasibility",
            "question": "What organism was found in the latest culture?",
            "patient_fhir_id": "synthetic-patient",
            "assumption": "Synthetic non-PHI evidence.",
        }
        intent = a6.qo_infer_intent(row)
        plan = a6.build_search_plan(
            row,
            intent,
            count=100,
            features={"micro-vocab"},
        )
        resource_types = {item["resource_type"] for item in plan}

        self.assertIn("Observation", resource_types)
        self.assertNotIn("DiagnosticReport", resource_types)

    def test_bundle_hashes_and_parses_the_corpus_once_for_many_questions(self) -> None:
        first = self.promoted_record()
        second = copy.deepcopy(first)
        second["question_id"] = "a11-adapter-synthetic-2"
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(
                Path(tmp), [first, second]
            )
            bundle = load_promoted_bundle(
                packet_path,
                manifest_path,
                expected_manifest_sha256=self.manifest_sha256(manifest_path),
            )
            first_adapted = bundle.load(str(first["question_id"]))
            second_adapted = bundle.load(str(second["question_id"]))

            self.assertEqual(
                bundle.question_ids,
                ("a11-adapter-synthetic-1", "a11-adapter-synthetic-2"),
            )
            self.assertEqual(
                first_adapted["integrity"]["packet_file_sha256"],
                second_adapted["integrity"]["packet_file_sha256"],
            )

    def test_bundle_constructor_cannot_bypass_verification(self) -> None:
        with self.assertRaisesRegex(TypeError, "use load_promoted_bundle"):
            PromotedBundle(
                records={},
                manifest_sha256="0" * 64,
                packet_file_sha256="0" * 64,
                dependency_hashes={},
            )

    def test_loaded_payload_mutation_cannot_change_the_verified_bundle(self) -> None:
        record = self.promoted_record()
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            bundle = load_promoted_bundle(
                packet_path,
                manifest_path,
                expected_manifest_sha256=self.manifest_sha256(manifest_path),
            )
            first = bundle.load(record["question_id"])
            original_json = first["v_model_payload_json"]
            original_integrity = copy.deepcopy(first["integrity"])
            first["v_model_payload"]["resources"][0]["code"]["text"] = "INJECTED"
            first["packet"]["resources"][0]["code"]["text"] = "ALSO INJECTED"

            second = bundle.load(record["question_id"])

        self.assertEqual(second["v_model_payload_json"], original_json)
        self.assertEqual(second["integrity"], original_integrity)
        self.assertNotIn("INJECTED", second["v_model_payload_json"])

    def test_question_only_intent_and_source_plan_are_recomputed(self) -> None:
        mutations = (
            (
                "intent",
                lambda record: record["intent"].update(search_terms=["oracle"]),
                "intent is not question-only reproducible",
            ),
            (
                "patient path",
                lambda record: record["packet"]["source_queries"][0].update(
                    path="Observation?patient=other&_count=100"
                ),
                "source plan is not question-only reproducible",
            ),
            (
                "query reason",
                lambda record: record["packet"]["source_queries"][0].update(
                    reason="oracle-selected"
                ),
                "source plan is not question-only reproducible",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                record = self.promoted_record()
                mutate(record)
                self.rehash_packet(record)
                packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
                with self.assertRaisesRegex(ValueError, message):
                    self.load_record(packet_path, manifest_path, record["question_id"])

    def test_frozen_none_policy_cannot_claim_a_relaxed_fetch(self) -> None:
        record = self.promoted_record()
        query = record["packet"]["source_queries"][0]
        injected_path = "Observation?patient=other&_count=100"
        query["relaxation_attempts"] = [injected_path]
        query["fetch_receipt"]["relaxation_attempts"] = [
            {"path": injected_path, "result_count": 1}
        ]
        self.rehash_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            with self.assertRaisesRegex(ValueError, "policy forbids relaxation"):
                self.load_record(packet_path, manifest_path, record["question_id"])

    def test_manifest_protocol_guards_fail_closed(self) -> None:
        cases = (
            (("kind",), "wrong-kind", "manifest kind"),
            (("config", "planner"), "metadata-oracle", "planner"),
            (("config", "planner_version"), "qo-old", "planner version"),
            (("config", "plan_only"), True, "plan-only"),
            (("config", "features"), [], "feature set"),
            (("config", "max_total_resources"), 201, "resource bound"),
            (("config", "max_packet_chars"), 160001, "character bound"),
            (("config", "count"), 99, "query count"),
            (("config", "reference_traversal"), {}, "traversal"),
            (("config", "evidence_recipe", "id"), "wrong", "evidence recipe"),
            (("config", "evidence_recipe", "features"), [], "recipe feature"),
            (("config", "evidence_recipe", "status"), "draft", "recipe status"),
            (
                ("config", "evidence_recipe", "promotion_result"),
                "other.md",
                "recipe result",
            ),
            (("config", "micro_vocabulary", "version"), "old", "vocabulary version"),
            (("config", "micro_vocabulary", "code_text_terms"), [], "vocabulary terms"),
            (("config", "micro_dispatcher", "version"), "old", "dispatcher version"),
            (("config", "micro_dispatcher", "question_terms"), [], "dispatcher terms"),
        )
        for path, replacement, message in cases:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                record = self.promoted_record()
                packet_path, manifest_path, manifest = self.write_sealed(
                    Path(tmp), [record]
                )
                self.set_path(manifest, path, replacement)
                self.rewrite_manifest(manifest_path, manifest)
                with self.assertRaisesRegex(ValueError, message):
                    self.load_record(packet_path, manifest_path, record["question_id"])

    def test_manifest_config_and_packet_metadata_are_strict_allowlists(self) -> None:
        record = self.promoted_record()
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, manifest = self.write_sealed(Path(tmp), [record])
            manifest["config"]["correct_response"] = "hidden"
            self.rewrite_manifest(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "config fields changed"):
                self.load_record(packet_path, manifest_path, record["question_id"])

        record = self.promoted_record()
        record["packet"]["correct_response"] = "hidden"
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            with self.assertRaisesRegex(ValueError, "metadata fields changed"):
                self.load_record(packet_path, manifest_path, record["question_id"])

    def test_manifest_and_packet_correspondence_guards(self) -> None:
        record = self.promoted_record()
        mutations = (
            (
                "missing hashes",
                lambda manifest: manifest.update(packet_hashes=None),
                "packet_hashes",
            ),
            (
                "question set",
                lambda manifest: manifest.update(packet_hashes={"other": "0" * 64}),
                "question IDs",
            ),
            ("question count", lambda manifest: manifest.update(questions=2), "question count"),
            (
                "manifest packet hash",
                lambda manifest: manifest["packet_hashes"].update(
                    {record["question_id"]: "0" * 64}
                ),
                "does not match manifest",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                packet_path, manifest_path, manifest = self.write_sealed(
                    Path(tmp), [copy.deepcopy(record)]
                )
                mutate(manifest)
                self.rewrite_manifest(manifest_path, manifest)
                with self.assertRaisesRegex(ValueError, message):
                    self.load_record(packet_path, manifest_path, record["question_id"])

        invalid_internal = self.promoted_record()
        invalid_internal["packet"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(
                Path(tmp), [invalid_internal]
            )
            with self.assertRaisesRegex(ValueError, "internal sha256"):
                self.load_record(packet_path, manifest_path, record["question_id"])

        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            bundle = load_promoted_bundle(
                packet_path,
                manifest_path,
                expected_manifest_sha256=self.manifest_sha256(manifest_path),
            )
            with self.assertRaisesRegex(ValueError, "question_id not found"):
                bundle.load("absent")

    def test_pinned_manifest_and_duplicate_json_key_guards(self) -> None:
        record = self.promoted_record()
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, manifest = self.write_sealed(Path(tmp), [record])
            wrong_pin = "0" * 64
            with self.assertRaisesRegex(ValueError, "pinned sha256"):
                load_promoted_bundle(
                    packet_path,
                    manifest_path,
                    expected_manifest_sha256=wrong_pin,
                )

            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            line = line.replace(
                '"question_id": ',
                '"question_id": "duplicate", "question_id": ',
                1,
            )
            packet_path.write_text(line + "\n", encoding="utf-8")
            manifest["output"]["sha256"] = hashlib.sha256(
                packet_path.read_bytes()
            ).hexdigest()
            self.rewrite_manifest(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                self.load_record(packet_path, manifest_path, record["question_id"])

    def test_packet_protocol_guards_fail_closed(self) -> None:
        cases = (
            ("kind", lambda packet: packet.update(kind="wrong"), "packet kind"),
            ("planner", lambda packet: packet.update(planner="old"), "planner version"),
            ("plan-only", lambda packet: packet.update(plan_only=True), "plan-only"),
            (
                "traversal",
                lambda packet: packet.update(reference_traversal={}),
                "metadata fields changed",
            ),
            ("bounds", lambda packet: packet.pop("bounds"), "metadata fields changed"),
            (
                "root receipt",
                lambda packet: packet["root_fetch_receipt"].update(extra=1),
                "root fetch receipt fields",
            ),
            (
                "query receipt",
                lambda packet: packet["source_queries"][0].update(extra=1),
                "source query fields",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                record = self.promoted_record()
                mutate(record["packet"])
                packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
                with self.assertRaisesRegex(ValueError, message):
                    self.load_record(packet_path, manifest_path, record["question_id"])

        record = self.promoted_record()
        record["packet"] = []
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            with self.assertRaisesRegex(ValueError, "packet object"):
                self.load_record(packet_path, manifest_path, record["question_id"])

    def test_patient_resource_boundaries_and_unicode_receipts(self) -> None:
        for patient_id in ("", "Patient/", ["synthetic-patient-1"]):
            with self.subTest(patient_id=patient_id), tempfile.TemporaryDirectory() as tmp:
                record = self.promoted_record()
                record["patient_fhir_id"] = patient_id
                packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
                with self.assertRaisesRegex(ValueError, "patient_fhir_id|patient reference"):
                    self.load_record(packet_path, manifest_path, record["question_id"])

        for field in ("id", "resourceType"):
            with self.subTest(missing=field), tempfile.TemporaryDirectory() as tmp:
                record = self.promoted_record()
                record["packet"]["resources"][0].pop(field)
                packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
                with self.assertRaisesRegex(ValueError, "resource"):
                    self.load_record(packet_path, manifest_path, record["question_id"])

        record = self.promoted_record()
        record["packet"]["resources"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            with self.assertRaisesRegex(ValueError, "resources are not a list"):
                self.load_record(packet_path, manifest_path, record["question_id"])

        record = self.promoted_record()
        record["packet"]["resources"] = []
        self.reseal_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            with self.assertRaisesRegex(ValueError, "no planned"):
                self.load_record(packet_path, manifest_path, record["question_id"])

        record = self.promoted_record()
        first = record["packet"]["resources"][0]
        first["id"] = "z-root"
        first["code"]["text"] = "β-culture 🧫"
        second = copy.deepcopy(first)
        second["id"] = "a-root"
        record["packet"]["resources"].append(second)
        self.reseal_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            adapted = self.load_record(packet_path, manifest_path, record["question_id"])
            expected = codex_harness.render_model_visible_packet(record["packet"])
            self.assertEqual(adapted["v_model_payload_json"], expected)
            self.assertEqual(adapted["root_refs"], ["Observation/a-root", "Observation/z-root"])
            self.assertEqual(adapted["question_plan"], plan_question(record["question"]))
            self.assertEqual(
                adapted["integrity"]["model_payload_utf8_bytes"],
                len(expected.encode("utf-8")),
            )

    def test_gold_or_case_fields_are_rejected_instead_of_stripped(self) -> None:
        record = self.promoted_record()
        contaminated = copy.deepcopy(record)
        contaminated["expected_answer"] = "hidden benchmark answer"

        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(
                Path(tmp), [contaminated]
            )

            with self.assertRaisesRegex(
                ValueError, "compile_evidence.py schema|forbidden benchmark field"
            ):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_gold_prefixes_are_rejected_in_nested_record_and_manifest_fields(self) -> None:
        record = self.promoted_record()
        contaminated_record = copy.deepcopy(record)
        contaminated_record["intent"]["gold_resource_ids"] = ["Observation/hidden"]
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(
                Path(tmp), [contaminated_record]
            )
            with self.assertRaisesRegex(ValueError, "forbidden benchmark field"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path, manifest_path, manifest = self.write_sealed(root, [record])
            manifest["expected_terminal_ids"] = ["Observation/hidden"]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "forbidden benchmark field"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_packet_file_tampering_fails_before_record_adaptation(self) -> None:
        record = self.promoted_record()
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            packet_path.write_text(
                packet_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "manifest output sha256"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_duplicate_question_ids_are_rejected_even_with_resealed_file(self) -> None:
        record = self.promoted_record()
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(
                Path(tmp), [record, copy.deepcopy(record)]
            )

            with self.assertRaisesRegex(ValueError, "duplicate promoted packet"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_resealed_non_dispatched_packet_is_not_a11_eligible(self) -> None:
        record = self.promoted_record()
        record["packet"]["features"] = []
        self.rehash_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])

            with self.assertRaisesRegex(ValueError, "micro-dispatched"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_cross_patient_root_is_rejected_before_authorization(self) -> None:
        record = self.promoted_record()
        record["packet"]["resources"][0]["subject"] = {
            "reference": "Patient/synthetic-patient-other"
        }
        self.reseal_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])

            with self.assertRaisesRegex(ValueError, "patient-consistent"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_cross_patient_non_root_resource_is_also_rejected(self) -> None:
        record = self.promoted_record()
        record["packet"]["resources"].append(
            {
                "resourceType": "Observation",
                "id": "cross-patient-observation",
                "subject": {"reference": "Patient/synthetic-patient-other"},
            }
        )
        self.reseal_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])

            with self.assertRaisesRegex(ValueError, "patient-consistent"):
                self.load_record(
                    packet_path, manifest_path, str(record["question_id"])
                )

    def test_missing_or_nested_cross_patient_scope_is_rejected(self) -> None:
        cases = (
            (
                "missing subject",
                lambda resource: resource.pop("subject"),
                "explicitly patient-consistent",
            ),
            (
                "nested focus",
                lambda resource: resource.update(
                    focus={"reference": "Patient/synthetic-patient-other"}
                ),
                "cross-patient reference",
            ),
            (
                "absolute focus",
                lambda resource: resource.update(
                    focus={
                        "reference": "https://example.invalid/fhir/Patient/synthetic-patient-other"
                    }
                ),
                "cross-patient reference",
            ),
            (
                "versioned focus",
                lambda resource: resource.update(
                    focus={
                        "reference": "Patient/synthetic-patient-other/_history/v1"
                    }
                ),
                "cross-patient reference",
            ),
            (
                "contained patient",
                lambda resource: resource.update(
                    contained=[
                        {"resourceType": "Patient", "id": "synthetic-patient-other"}
                    ]
                ),
                "nested resources",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                record = self.promoted_record()
                mutate(record["packet"]["resources"][0])
                self.reseal_packet(record)
                packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
                with self.assertRaisesRegex(ValueError, message):
                    self.load_record(packet_path, manifest_path, record["question_id"])

        record = self.promoted_record()
        record["packet"]["resources"][0]["subject"] = {
            "reference": (
                "https://example.invalid/fhir/Patient/synthetic-patient-1/_history/v1"
            )
        }
        self.reseal_packet(record)
        with tempfile.TemporaryDirectory() as tmp:
            packet_path, manifest_path, _ = self.write_sealed(Path(tmp), [record])
            adapted = self.load_record(packet_path, manifest_path, record["question_id"])
        self.assertEqual(adapted["patient_ref"], "Patient/synthetic-patient-1")


if __name__ == "__main__":
    unittest.main()
