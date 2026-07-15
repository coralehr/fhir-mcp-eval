from __future__ import annotations

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
import a11_governed_retrieval
import compile_evidence
from a11_evidence_core import canonical_bytes
from a11_governed_retrieval import (
    GovernedRetrievalBundle,
    build_governed_retrieval_bundle,
)
from a11_packet_adapter import load_promoted_bundle


class A11GovernedRetrievalTests(unittest.TestCase):
    patient_id = "synthetic-patient-1"
    patient_ref = f"Patient/{patient_id}"
    practice_id = "synthetic-practice-1"

    @classmethod
    def source_resources(cls) -> list[dict]:
        return [
            {
                "resourceType": "Patient",
                "id": cls.patient_id,
                "meta": {"versionId": "1"},
            },
            {
                "resourceType": "Observation",
                "id": "culture-root-1",
                "meta": {"versionId": "1"},
                "subject": {"reference": cls.patient_ref},
                "effectiveDateTime": "2100-01-02T00:00:00Z",
                "code": {"text": "Culture panel"},
                "hasMember": [{"reference": "Observation/culture-middle-1"}],
            },
            {
                "resourceType": "Observation",
                "id": "culture-middle-1",
                "meta": {"versionId": "1"},
                "subject": {"reference": cls.patient_ref},
                "code": {"text": "Culture component"},
                "hasMember": [{"reference": "Observation/culture-terminal-1"}],
            },
            {
                "resourceType": "Observation",
                "id": "culture-terminal-1",
                "meta": {"versionId": "1"},
                "subject": {"reference": cls.patient_ref},
                "code": {"text": "Escherichia coli"},
                "valueString": "detected",
            },
        ]

    def build_promoted_bundle(
        self, root: Path, resources: list[dict] | None = None
    ):
        resources = resources or self.source_resources()
        root.mkdir(parents=True, exist_ok=True)

        class SyntheticClient:
            @staticmethod
            def search_with_pagination(
                query_string: str, *, max_results: int | None = None
            ) -> list[dict]:
                del max_results
                if query_string.startswith("Observation?"):
                    return [resources[1]]
                raise AssertionError(f"unexpected query: {query_string}")

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
            writer.writerow(
                {
                    "question_id": "governed-retrieval-1",
                    "split": "valid",
                    "question": "What organism was found in the latest culture Observation?",
                    "assumption": "Synthetic non-PHI evidence dated in 2100.",
                    "patient_fhir_id": self.patient_id,
                }
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
            mock.patch("fhir_client.get_fhir_client", return_value=SyntheticClient()),
            mock.patch.object(sys, "argv", argv),
            mock.patch("builtins.print"),
        ):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(compile_evidence.__file__, run_name="__main__")
            self.assertEqual(stopped.exception.code, 0)
        return load_promoted_bundle(
            packet_path,
            manifest_path,
            expected_manifest_sha256=hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            expected_evidence_recipe=a6.A11_EVIDENCE_RECIPE,
        )

    def snapshot_bytes(
        self,
        resources: list[dict] | None = None,
        resource_practices: list[str] | None = None,
    ) -> bytes:
        resources = resources or self.source_resources()
        resource_practices = resource_practices or [self.practice_id] * len(resources)
        self.assertEqual(len(resources), len(resource_practices))
        return canonical_bytes(
            {
                "schema_version": "a11-synthetic-source-snapshot-v1",
                "source_id": "synthetic-source",
                "source_version": "source-v1",
                "practice_id": self.practice_id,
                "patient_ref": self.patient_ref,
                "resources": [
                    {"practice_id": practice_id, "resource": resource}
                    for practice_id, resource in zip(
                        resource_practices, resources, strict=True
                    )
                ],
            }
        )

    def policy(self) -> dict:
        return {
            "principal_id": "synthetic-principal",
            "practice_id": self.practice_id,
            "purpose": "treatment",
            "allowed_purposes": ["treatment"],
            "patient_ref": self.patient_ref,
            "source_id": "synthetic-source",
            "source_version": "source-v1",
            "traversal_bounds": {
                "max_depth": 2,
                "max_targets": 10,
                "max_packet_bytes": 160_000,
                "vocabulary_allowed_resource_types": ["Observation", "Specimen"],
            },
        }

    def policy_artifact(self, policy: dict | None = None) -> dict:
        data = canonical_bytes(policy or self.policy())
        return {
            "policy_context_bytes": data,
            "expected_policy_sha256": hashlib.sha256(data).hexdigest(),
        }

    def test_verified_v_roots_produce_one_shared_versioned_t_e_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            verified_record = promoted.load("governed-retrieval-1")
            snapshot = self.snapshot_bytes()
            bundle = build_governed_retrieval_bundle(
                promoted,
                "governed-retrieval-1",
                source_snapshot_bytes=snapshot,
                expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                **self.policy_artifact(),
            )

        flat = bundle.load_flat_model_packet()
        grouped = bundle.load_event_group_model_packet()
        receipt = bundle.load_receipt()
        self.assertIn(
            "Observation/culture-terminal-1",
            {
                f"{resource['resourceType']}/{resource['id']}"
                for resource in flat["resources"]
            },
        )
        self.assertEqual(grouped["answerability_receipt"]["state"], "sufficient")
        self.assertEqual(
            receipt["shared_retrieval_source_sha256"],
            bundle.retrieval_source_sha256,
        )
        flat_payload = bundle.load_flat_model_payload(
            question_id=verified_record["question_id"],
            question=verified_record["question"],
            question_plan=verified_record["question_plan"],
        )
        grouped_payload = bundle.load_event_group_model_payload(
            question_id=verified_record["question_id"],
            question=verified_record["question"],
            question_plan=verified_record["question_plan"],
        )
        self.assertEqual(flat_payload, canonical_bytes(flat))
        self.assertEqual(grouped_payload, canonical_bytes(grouped))
        self.assertEqual(
            hashlib.sha256(flat_payload).hexdigest(),
            receipt["model_packets"]["t_sha256"],
        )
        with self.assertRaisesRegex(ValueError, "question does not match"):
            bundle.load_flat_model_payload(
                question_id="different-question",
                question=verified_record["question"],
                question_plan=verified_record["question_plan"],
            )
        audit_source = bundle.load_audit_retrieval_source()
        self.assertEqual(
            hashlib.sha256(canonical_bytes(audit_source)).hexdigest(),
            receipt["shared_retrieval_source_sha256"],
        )
        self.assertTrue(audit_source["audit_path_citations"])
        self.assertEqual(
            receipt["traversal"]["resource_versions_sha256"],
            hashlib.sha256(
                canonical_bytes(
                    [
                        {"reference": "Observation/culture-middle-1", "version_id": "1"},
                        {"reference": "Observation/culture-root-1", "version_id": "1"},
                        {"reference": "Observation/culture-terminal-1", "version_id": "1"},
                        {"reference": self.patient_ref, "version_id": "1"},
                    ]
                )
            ).hexdigest(),
        )
        serialized_receipt = json.dumps(receipt, sort_keys=True)
        self.assertNotIn("synthetic-principal", serialized_receipt)
        self.assertNotIn(self.practice_id, serialized_receipt)

    def test_bundle_is_factory_only_and_returns_fresh_copies(self) -> None:
        with self.assertRaisesRegex(TypeError, "build_governed_retrieval_bundle"):
            GovernedRetrievalBundle()

        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            snapshot = self.snapshot_bytes()
            bundle = build_governed_retrieval_bundle(
                promoted,
                "governed-retrieval-1",
                source_snapshot_bytes=snapshot,
                expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                **self.policy_artifact(),
            )
        first = bundle.load_flat_model_packet()
        first["resources"][0]["id"] = "mutated"
        second = bundle.load_flat_model_packet()
        self.assertNotEqual(second["resources"][0]["id"], "mutated")
        with self.assertRaisesRegex(AttributeError, "immutable"):
            bundle._flat_packet_bytes = b"{}"
        object.__setattr__(bundle, "_flat_packet_bytes", b"{}")
        with self.assertRaisesRegex(RuntimeError, "diverged from receipt"):
            bundle.load_flat_model_packet()

    def test_denied_purpose_and_tampered_source_fail_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            snapshot = self.snapshot_bytes()
            denied = self.policy()
            denied["purpose"] = "research"
            with self.assertRaisesRegex(PermissionError, "purpose is denied"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=snapshot,
                    expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                    **self.policy_artifact(denied),
                )

            tampered = snapshot + b"\n"
            with self.assertRaisesRegex(ValueError, "pinned sha256"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=tampered,
                    expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                    **self.policy_artifact(),
                )

            policy_artifact = self.policy_artifact()
            with self.assertRaisesRegex(ValueError, "policy context does not match"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=snapshot,
                    expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                    policy_context_bytes=policy_artifact["policy_context_bytes"] + b"\n",
                    expected_policy_sha256=policy_artifact[
                        "expected_policy_sha256"
                    ],
                )

    def test_benchmark_only_source_fields_fail_before_traversal(self) -> None:
        injections = (
            ("gold_answer", "secret"),
            ("failure_mode", "missing"),
            ("expected_hidden_fact", "secret"),
            ("true_hidden_fact", "secret"),
            ("label", "answerable"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            for key, value in injections:
                with self.subTest(key=key):
                    resources = self.source_resources()
                    resources[2]["nested_test_only"] = [{key: value}]
                    snapshot = self.snapshot_bytes(resources)
                    with (
                        mock.patch.object(
                            a11_governed_retrieval, "project_traversal"
                        ) as traversal,
                        self.assertRaisesRegex(ValueError, "benchmark-only fields"),
                    ):
                        build_governed_retrieval_bundle(
                            promoted,
                            "governed-retrieval-1",
                            source_snapshot_bytes=snapshot,
                            expected_snapshot_sha256=hashlib.sha256(
                                snapshot
                            ).hexdigest(),
                            **self.policy_artifact(),
                        )
                    traversal.assert_not_called()

    def test_non_finite_json_constants_fail_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            snapshot = self.snapshot_bytes()
            marker = b'"valueString":"detected"'
            self.assertIn(marker, snapshot)
            non_finite = snapshot.replace(marker, b'"valueString":NaN', 1)
            with (
                mock.patch.object(
                    a11_governed_retrieval, "project_traversal"
                ) as traversal,
                self.assertRaisesRegex(ValueError, "non-finite JSON number"),
            ):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=non_finite,
                    expected_snapshot_sha256=hashlib.sha256(non_finite).hexdigest(),
                    **self.policy_artifact(),
                )
            traversal.assert_not_called()

    def test_identical_inputs_replay_to_identical_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            snapshot = self.snapshot_bytes()
            kwargs = {
                "source_snapshot_bytes": snapshot,
                "expected_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
                **self.policy_artifact(),
            }
            first = build_governed_retrieval_bundle(
                promoted, "governed-retrieval-1", **kwargs
            )
            second = build_governed_retrieval_bundle(
                promoted, "governed-retrieval-1", **kwargs
            )

        self.assertEqual(first.receipt_sha256, second.receipt_sha256)
        self.assertEqual(first.load_receipt(), second.load_receipt())
        self.assertEqual(
            first.load_audit_retrieval_source(),
            second.load_audit_retrieval_source(),
        )
        self.assertEqual(first.load_flat_model_packet(), second.load_flat_model_packet())
        self.assertEqual(
            first.load_event_group_model_packet(),
            second.load_event_group_model_packet(),
        )

    def test_duplicate_identity_and_missing_resource_version_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))

            duplicate_resources = self.source_resources()
            duplicate_resources.append(dict(duplicate_resources[-1]))
            duplicate_snapshot = self.snapshot_bytes(duplicate_resources)
            with self.assertRaisesRegex(ValueError, "duplicate source resource"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=duplicate_snapshot,
                    expected_snapshot_sha256=hashlib.sha256(
                        duplicate_snapshot
                    ).hexdigest(),
                    **self.policy_artifact(),
                )

            unversioned_resources = self.source_resources()
            unversioned_resources[-1].pop("meta")
            unversioned_snapshot = self.snapshot_bytes(unversioned_resources)
            with self.assertRaisesRegex(ValueError, "no versionId"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=unversioned_snapshot,
                    expected_snapshot_sha256=hashlib.sha256(
                        unversioned_snapshot
                    ).hexdigest(),
                    **self.policy_artifact(),
                )

    def test_source_resource_count_bound_fails_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            snapshot = self.snapshot_bytes()
            with (
                mock.patch.object(a11_governed_retrieval, "MAX_SOURCE_RESOURCES", 3),
                mock.patch.object(
                    a11_governed_retrieval, "project_traversal"
                ) as traversal,
                self.assertRaisesRegex(ValueError, "resources are outside"),
            ):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=snapshot,
                    expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                    **self.policy_artifact(),
                )
            traversal.assert_not_called()

    def test_exact_version_resolves_but_stale_version_stays_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exact_resources = self.source_resources()
            exact_resources[1]["hasMember"][0]["reference"] = (
                "Observation/culture-middle-1/_history/1"
            )
            promoted = self.build_promoted_bundle(
                Path(tmp) / "exact", exact_resources
            )
            exact_resources[2]["hasMember"][0]["reference"] = (
                "Observation/culture-terminal-1/_history/1"
            )
            exact_snapshot = self.snapshot_bytes(exact_resources)
            exact = build_governed_retrieval_bundle(
                promoted,
                "governed-retrieval-1",
                source_snapshot_bytes=exact_snapshot,
                expected_snapshot_sha256=hashlib.sha256(exact_snapshot).hexdigest(),
                **self.policy_artifact(),
            )
            self.assertEqual(
                exact.load_event_group_model_packet()["answerability_receipt"][
                    "state"
                ],
                "sufficient",
            )
            self.assertIn(
                "Observation/culture-terminal-1/_history/1",
                json.dumps(exact.load_flat_model_packet(), sort_keys=True),
            )

            stale_resources = self.source_resources()
            stale_resources[1]["hasMember"][0]["reference"] = (
                "Observation/culture-middle-1/_history/999"
            )
            stale_promoted = self.build_promoted_bundle(
                Path(tmp) / "stale", stale_resources
            )
            stale_snapshot = self.snapshot_bytes(stale_resources)
            stale = build_governed_retrieval_bundle(
                stale_promoted,
                "governed-retrieval-1",
                source_snapshot_bytes=stale_snapshot,
                expected_snapshot_sha256=hashlib.sha256(stale_snapshot).hexdigest(),
                **self.policy_artifact(),
            )
        self.assertEqual(
            stale.load_event_group_model_packet()["answerability_receipt"]["state"],
            "insufficient",
        )
        stale_model = json.dumps(stale.load_flat_model_packet(), sort_keys=True)
        stale_receipt = json.dumps(stale.load_receipt(), sort_keys=True)
        self.assertNotIn("_history/999", stale_model)
        self.assertNotIn("_history/999", stale_receipt)

    def test_cross_practice_and_cross_patient_targets_remain_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp) / "promoted")

            cross_practice_resources = self.source_resources()
            practices = [self.practice_id, self.practice_id, "other-practice", self.practice_id]
            cross_practice_snapshot = self.snapshot_bytes(
                cross_practice_resources, practices
            )
            cross_practice = build_governed_retrieval_bundle(
                promoted,
                "governed-retrieval-1",
                source_snapshot_bytes=cross_practice_snapshot,
                expected_snapshot_sha256=hashlib.sha256(
                    cross_practice_snapshot
                ).hexdigest(),
                **self.policy_artifact(),
            )

            cross_patient_resources = self.source_resources()
            cross_patient_resources[2]["subject"] = {
                "reference": "Patient/synthetic-other-patient"
            }
            cross_patient_snapshot = self.snapshot_bytes(cross_patient_resources)
            cross_patient = build_governed_retrieval_bundle(
                promoted,
                "governed-retrieval-1",
                source_snapshot_bytes=cross_patient_snapshot,
                expected_snapshot_sha256=hashlib.sha256(
                    cross_patient_snapshot
                ).hexdigest(),
                **self.policy_artifact(),
            )

        for bundle in (cross_practice, cross_patient):
            self.assertEqual(
                bundle.load_event_group_model_packet()["answerability_receipt"][
                    "state"
                ],
                "insufficient",
            )
            model = json.dumps(bundle.load_flat_model_packet(), sort_keys=True)
            receipt = json.dumps(bundle.load_receipt(), sort_keys=True)
            self.assertNotIn("culture-middle-1", model)
            self.assertNotIn("culture-middle-1", receipt)
            self.assertNotIn("other-practice", receipt)
            self.assertNotIn("synthetic-other-patient", receipt)

    def test_ambiguous_unbound_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            resources = self.source_resources()
            resources[2].pop("subject")
            snapshot = self.snapshot_bytes(resources)
            with self.assertRaisesRegex(PermissionError, "explicitly patient-bound"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=snapshot,
                    expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                    **self.policy_artifact(),
                )

    def test_shared_model_packet_byte_bound_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            promoted = self.build_promoted_bundle(Path(tmp))
            resources = self.source_resources()
            resources[-1]["valueString"] = "x" * 2_000
            snapshot = self.snapshot_bytes(resources)
            policy = self.policy()
            policy["traversal_bounds"]["max_packet_bytes"] = 256
            with self.assertRaisesRegex(ValueError, "model packet exceeds"):
                build_governed_retrieval_bundle(
                    promoted,
                    "governed-retrieval-1",
                    source_snapshot_bytes=snapshot,
                    expected_snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
                    **self.policy_artifact(policy),
                )


if __name__ == "__main__":
    unittest.main()
