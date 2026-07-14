from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from a11_substrate_audit import build_receipt


class A11SubstrateAuditTests(unittest.TestCase):
    def test_receipt_is_aggregate_only_and_counts_registered_paths(self) -> None:
        patient_ref = "Patient/private-patient-id"
        resources = [
            {"resourceType": "Patient", "id": "private-patient-id"},
            {
                "resourceType": "Observation",
                "id": "private-root-id",
                "subject": {"reference": patient_ref},
                "hasMember": [{"reference": "Observation/private-middle-id"}],
            },
            {
                "resourceType": "Observation",
                "id": "private-middle-id",
                "subject": {"reference": patient_ref},
                "hasMember": [{"reference": "Observation/private-terminal-id"}],
                "specimen": {"reference": "Specimen/private-specimen-id"},
            },
            {
                "resourceType": "Observation",
                "id": "private-terminal-id",
                "subject": {"reference": patient_ref},
            },
            {
                "resourceType": "Specimen",
                "id": "private-specimen-id",
                "subject": {"reference": patient_ref},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "synthetic.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "synthetic/Observation.000.ndjson",
                    "".join(
                        json.dumps(resource, sort_keys=True) + "\n"
                        for resource in resources
                    ),
                )
            receipt = build_receipt(
                archive_path,
                source_url="https://example.invalid/synthetic.git",
                source_commit="0" * 40,
            )

        registered = {
            item["family"]: item
            for item in receipt["registered_a11_path_families"]
        }
        self.assertEqual(receipt["total_resources"], 5)
        self.assertEqual(
            registered["Observation.hasMember -> Observation.hasMember"],
            {
                "family": "Observation.hasMember -> Observation.hasMember",
                "roots": 1,
                "paths": 1,
                "patient_clusters": 1,
                "rejected_ambiguous_roots": 0,
                "rejected_ambiguous_target_edges": 0,
                "rejected_cross_patient_edges": 0,
            },
        )
        self.assertEqual(
            registered["Observation.hasMember -> Observation.specimen"]["paths"],
            1,
        )
        serialized = json.dumps(receipt, sort_keys=True)
        self.assertNotIn("private-patient-id", serialized)
        self.assertNotIn("private-root-id", serialized)

    def test_cross_patient_chain_is_not_counted_as_eligible(self) -> None:
        resources = [
            {
                "resourceType": "Observation",
                "id": "root",
                "subject": {"reference": "Patient/one"},
                "hasMember": [{"reference": "Observation/middle"}],
            },
            {
                "resourceType": "Observation",
                "id": "middle",
                "subject": {"reference": "Patient/two"},
                "hasMember": [{"reference": "Observation/terminal"}],
            },
            {
                "resourceType": "Observation",
                "id": "terminal",
                "subject": {"reference": "Patient/two"},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "cross-patient.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "synthetic/Observation.000.ndjson",
                    "".join(
                        json.dumps(resource, sort_keys=True) + "\n"
                        for resource in resources
                    ),
                )
            receipt = build_receipt(
                archive_path,
                source_url="https://example.invalid/synthetic.git",
                source_commit="0" * 40,
            )

        family = next(
            item
            for item in receipt["registered_a11_path_families"]
            if item["family"] == "Observation.hasMember -> Observation.hasMember"
        )
        self.assertEqual(family["paths"], 0)
        self.assertEqual(family["rejected_cross_patient_edges"], 1)

    def test_committed_receipt_binds_current_scanner(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        receipt = json.loads(
            (repo / "docs/results/A11_SUBSTRATE_AUDIT_RECEIPT.json").read_text(
                encoding="utf-8"
            )
        )
        scanner = repo / receipt["scanner"]["path"]
        actual = hashlib.sha256(scanner.read_bytes()).hexdigest()
        self.assertEqual(receipt["scanner"]["sha256"], actual)


if __name__ == "__main__":
    unittest.main()
