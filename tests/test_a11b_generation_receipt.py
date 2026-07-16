from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from a11_evidence_core import canonical_bytes, sha256
from a11b_generation_receipt import (
    compile_generation_receipt,
    verify_generation_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
POWER_SPEC = json.loads((ROOT / "fixtures" / "a11b_power_spec.json").read_text())
POWER_RECEIPT = json.loads(
    (ROOT / "docs" / "results" / "a11b-power-receipt.json").read_text()
)


def _write(root: Path, relative: str, payload: bytes) -> dict[str, object]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": relative,
        "sha256": sha256(payload),
        "bytes": len(payload),
    }


def _fixture(root: Path) -> dict[str, object]:
    jar = _write(root, "generator/synthea.jar", b"pinned test jar\n")
    java = _write(root, "runtime/bin/java", b"pinned test runtime\n")
    (root / "runtime" / "bin" / "java").chmod(0o755)
    runtime_module = _write(root, "runtime/lib/modules", b"pinned runtime modules\n")
    probe = _write(
        root,
        "runtime/java-version.txt",
        b'openjdk version "21.0.7"\nEclipse Adoptium\n',
    )
    config = _write(
        root,
        "configuration/synthea.properties",
        b"exporter.fhir.export=true\n",
    )
    module = _write(root, "modules/allergies.json", b'{"name":"Allergies"}\n')
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
            for index in range(448)
        ],
    }
    output = root / "output" / "fhir" / "patients.json"
    output.parent.mkdir(parents=True)
    output.write_bytes(canonical_bytes(bundle))
    return {
        "schema_version": "a11b-synthea-generation-spec-v1",
        "generator": {
            "repository": "synthetichealth/synthea",
            "release_tag": "v-test",
            "commit": "1" * 40,
            "jar": jar,
        },
        "java_runtime": {
            "vendor": "Eclipse Adoptium",
            "version": "21.0.7",
            "executable": java,
            "distribution_files": [runtime_module],
            "version_probe": probe,
            "version_probe_argv": [
                "runtime/bin/java",
                "-XshowSettings:properties",
                "-version",
            ],
        },
        "invocation": {
            "argv": [
                "runtime/bin/java",
                "-jar",
                "generator/synthea.jar",
                "-s",
                "20260715",
                "-p",
                "448",
                "-r",
                "20260715",
            ],
            "environment": {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TZ": "UTC"},
            "seed": 20260715,
            "population": 448,
            "reference_date": "2026-07-15",
            "locale": "en-US",
            "timezone": "UTC",
        },
        "configuration_files": [config],
        "module_files": [module],
        "exporter_settings": {
            "exporter.baseDirectory": "output",
            "exporter.fhir.export": True,
        },
        "output": {
            "root": "output",
            "allowed_suffixes": [".json"],
            "required_patient_count": 448,
            "max_entries": 1000,
            "max_file_bytes": 10_000_000,
            "max_total_bytes": 100_000_000,
        },
        "power_gate": {
            "spec_sha256": sha256(canonical_bytes(POWER_SPEC)),
            "receipt_sha256": sha256(canonical_bytes(POWER_RECEIPT)),
            "required_source_patients": 448,
        },
        "model_calls": 0,
    }


class A11bGenerationReceiptTests(unittest.TestCase):
    def test_compiles_path_stable_zero_model_receipt_for_exact_powered_population(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)

            receipt = compile_generation_receipt(
                spec,
                artifact_root=root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

            self.assertEqual(
                receipt["schema_version"],
                "a11b-synthea-generation-receipt-v1",
            )
            self.assertEqual(receipt["source_population"]["patients"], 448)
            self.assertEqual(receipt["power_gate"]["required_source_patients"], 448)
            self.assertEqual(receipt["model_calls"], 0)
            self.assertFalse(receipt["efficacy_artifacts_opened"])
            self.assertFalse(receipt["gold_artifacts_opened"])
            self.assertNotIn(directory, json.dumps(receipt, sort_keys=True))

    def test_receipt_binds_but_does_not_disclose_output_file_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            original = root / "output" / "fhir" / "patients.json"
            private_name = "synthetic-000-Alice-Smith.json"
            original.rename(original.with_name(private_name))

            receipt = compile_generation_receipt(
                spec,
                artifact_root=root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

            serialized = json.dumps(receipt, sort_keys=True)
            self.assertNotIn(private_name, serialized)
            self.assertEqual(
                set(receipt["raw_output"]["entries"][0]),
                {"path_sha256", "sha256", "bytes"},
            )

    def test_independent_roots_compile_byte_identical_receipts_and_verify(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            first_spec = _fixture(first_root)
            second_spec = _fixture(second_root)

            first = compile_generation_receipt(
                first_spec,
                artifact_root=first_root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )
            second = compile_generation_receipt(
                second_spec,
                artifact_root=second_root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

            self.assertEqual(canonical_bytes(first), canonical_bytes(second))
            verify_generation_receipt(
                first_spec,
                first,
                artifact_root=first_root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

    def test_tampered_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            receipt = compile_generation_receipt(
                spec,
                artifact_root=root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )
            tampered = copy.deepcopy(receipt)
            tampered["source_population"]["patients"] = 447

            with self.assertRaisesRegex(ValueError, "does not match"):
                verify_generation_receipt(
                    spec,
                    tampered,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_tampered_power_binding_and_population_fail_before_artifact_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            drifted = copy.deepcopy(spec)
            drifted["power_gate"]["receipt_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "power gate"):
                compile_generation_receipt(
                    drifted,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

            drifted = copy.deepcopy(spec)
            drifted["invocation"]["population"] = 447
            drifted["invocation"]["argv"][drifted["invocation"]["argv"].index("448")] = "447"
            with self.assertRaisesRegex(ValueError, "power gate"):
                compile_generation_receipt(
                    drifted,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_unregistered_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            (root / "gold.json").write_text('{"answer":"leak"}\n')

            with self.assertRaisesRegex(ValueError, "unexpected=.*gold.json"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_symlinked_registered_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            jar = root / "generator" / "synthea.jar"
            external = root / "external.jar"
            external.write_bytes(jar.read_bytes())
            jar.unlink()
            jar.symlink_to(external)

            with self.assertRaisesRegex(ValueError, "unsafe file"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_hard_linked_registered_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            os.link(
                root / "configuration" / "synthea.properties",
                root / "configuration" / "alias.properties",
            )

            with self.assertRaisesRegex(ValueError, "hard-linked"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_registered_input_byte_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            (root / "modules" / "allergies.json").write_text('{"name":"Changed"}\n')

            with self.assertRaisesRegex(ValueError, "registered input changed"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_generated_patient_count_must_equal_power_gated_population(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            output = root / "output" / "fhir" / "patients.json"
            bundle = json.loads(output.read_text())
            bundle["entry"].pop()
            output.write_bytes(canonical_bytes(bundle))

            with self.assertRaisesRegex(ValueError, "Patient count"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_duplicate_generated_patient_identifier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            output = root / "output" / "fhir" / "patients.json"
            bundle = json.loads(output.read_text())
            bundle["entry"][-1]["resource"]["id"] = bundle["entry"][0]["resource"]["id"]
            output.write_bytes(canonical_bytes(bundle))

            with self.assertRaisesRegex(ValueError, "duplicate generated Patient"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_runtime_probe_must_name_registered_vendor_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            spec["java_runtime"]["vendor"] = "Different Vendor"

            with self.assertRaisesRegex(ValueError, "version probe"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_registered_java_runtime_must_be_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            (root / "runtime" / "bin" / "java").chmod(0o644)

            with self.assertRaisesRegex(ValueError, "not executable"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_invocation_cannot_hide_seed_population_or_reference_date_drift(self) -> None:
        mutations = (("-s", "1"), ("-p", "447"), ("-r", "20260714"))
        for flag, value in mutations:
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec = _fixture(root)
                argv = spec["invocation"]["argv"]
                argv[argv.index(flag) + 1] = value

                with self.assertRaisesRegex(ValueError, f"{re.escape(flag)} value"):
                    compile_generation_receipt(
                        spec,
                        artifact_root=root,
                        power_spec=POWER_SPEC,
                        power_receipt=POWER_RECEIPT,
                    )

    def test_unregistered_output_format_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            (root / "output" / "fhir" / "run.log").write_text("not a FHIR artifact\n")

            with self.assertRaisesRegex(ValueError, "suffix is not allowed"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_generation_contract_allows_only_fhir_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            spec["output"]["allowed_suffixes"] = [".json", ".txt"]

            with self.assertRaisesRegex(ValueError, "exactly .json"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_duplicate_json_keys_in_generated_output_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            (root / "output" / "fhir" / "patients.json").write_bytes(
                b'{"resourceType":"Bundle","resourceType":"Patient","id":"x"}'
            )

            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_every_generated_json_file_must_contain_a_fhir_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            (root / "output" / "fhir" / "metadata.json").write_text(
                '{"not":"FHIR"}\n'
            )

            with self.assertRaisesRegex(ValueError, "contains no FHIR resource"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_compiler_dependency_drift_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            before = [{"path": "compiler.py", "sha256": "1" * 64, "bytes": 1}]
            after = [{"path": "compiler.py", "sha256": "2" * 64, "bytes": 1}]

            with mock.patch(
                "a11b_generation_receipt._dependency_receipts",
                side_effect=[before, after],
            ), self.assertRaisesRegex(ValueError, "dependencies changed"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_path_traversal_and_nonzero_model_calls_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            traversal = copy.deepcopy(spec)
            traversal["generator"]["jar"]["path"] = "../synthea.jar"
            with self.assertRaisesRegex(ValueError, "unsafe"):
                compile_generation_receipt(
                    traversal,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

            paid = copy.deepcopy(spec)
            paid["model_calls"] = 1
            with self.assertRaisesRegex(ValueError, "zero model calls"):
                compile_generation_receipt(
                    paid,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_non_json_or_non_finite_spec_values_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            spec["exporter_settings"]["unsafe"] = math.nan

            with self.assertRaisesRegex(ValueError, "finite JSON"):
                compile_generation_receipt(
                    spec,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_declared_input_and_output_bounds_have_hard_ceilings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = _fixture(root)
            oversized_input = copy.deepcopy(spec)
            oversized_input["java_runtime"]["distribution_files"][0]["bytes"] = 10**12
            with self.assertRaisesRegex(ValueError, "inputs exceed hard safety"):
                compile_generation_receipt(
                    oversized_input,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

            oversized_output = copy.deepcopy(spec)
            oversized_output["output"]["max_file_bytes"] = 10**12
            with self.assertRaisesRegex(ValueError, "output bounds exceed hard safety"):
                compile_generation_receipt(
                    oversized_output,
                    artifact_root=root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )


if __name__ == "__main__":
    unittest.main()
