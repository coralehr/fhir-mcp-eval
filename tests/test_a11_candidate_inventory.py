from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from a11_candidate_inventory import scan_packet_file


class A11CandidateInventoryTests(unittest.TestCase):
    def test_scan_reports_aggregate_topology_without_identifiers(self) -> None:
        patient_id = "patient-secret-id"
        row = {
            "patient_fhir_id": patient_id,
            "packet": {
                "features": ["micro-vocab", "micro-traversal"],
                "resources": [
                    {
                        "resourceType": "Observation",
                        "id": "root-a",
                        "effectiveDateTime": "2100-01-01T00:00:00Z",
                        "hasMember": [{"reference": "Observation/child-a"}],
                    },
                    {
                        "resourceType": "Observation",
                        "id": "root-b",
                        "effectiveDateTime": "2100-02-01T00:00:00Z",
                        "hasMember": [{"reference": "Observation/child-b"}],
                    },
                    {
                        "resourceType": "Observation",
                        "id": "child-a",
                        "specimen": {"reference": "Specimen/spec-a"},
                    },
                    {"resourceType": "Specimen", "id": "spec-a"},
                ],
                "reference_traversal": {
                    "path_receipts": [
                        {
                            "depth": 1,
                            "from": "Observation/root-a",
                            "path": "Observation.hasMember[0].reference",
                            "to": "Observation/child-a",
                            "status": "fetched",
                        },
                        {
                            "depth": 2,
                            "from": "Observation/child-a",
                            "path": "Observation.specimen.reference",
                            "to": "Specimen/spec-a",
                            "status": "fetched",
                        },
                        {
                            "depth": 1,
                            "from": "Observation/root-b",
                            "path": "Observation.hasMember[0].reference",
                            "to": "Observation/child-b",
                            "status": "missing",
                        },
                    ]
                },
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = scan_packet_file(path)

        self.assertEqual(report["dispatched_micro_rows"], 1)
        self.assertEqual(report["unique_dispatched_patients"], 1)
        self.assertEqual(report["rows_with_depth_two_fetched_target"], 1)
        self.assertEqual(report["rows_with_multiple_timed_roots"], 1)
        self.assertEqual(report["rejected_receipts_by_reason"], {})
        self.assertEqual(
            report["available_two_hop_paths_by_family"],
            {"Observation.hasMember -> Observation.specimen": 1},
        )
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn(patient_id, serialized)
        self.assertNotIn("root-a", serialized)
        self.assertNotIn("spec-a", serialized)

    def test_non_dispatched_rows_are_controls(self) -> None:
        row = {
            "patient_fhir_id": "control-patient",
            "packet": {
                "features": [],
                "resources": [],
                "reference_traversal": {"path_receipts": []},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = scan_packet_file(path)

        self.assertEqual(report["sealed_packet_rows"], 1)
        self.assertEqual(report["dispatched_micro_rows"], 0)
        self.assertEqual(report["unique_dispatched_patients"], 0)

    def test_invalid_receipts_cannot_inflate_the_inventory(self) -> None:
        row = {
            "packet": {
                "features": ["micro-vocab"],
                "resources": [
                    {
                        "resourceType": "Observation",
                        "id": "root",
                        "hasMember": [{"reference": "Observation/real-child"}],
                    },
                    {"resourceType": "Observation", "id": "fake-child"},
                ],
                "reference_traversal": {
                    "path_receipts": [
                        {
                            "depth": 1,
                            "from": "Observation/root",
                            "path": "Observation.hasMember[0].reference",
                            "to": "Observation/fake-child",
                            "status": "fetched",
                        }
                    ]
                },
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = scan_packet_file(path)

        self.assertEqual(report["rows_with_any_fetched_target"], 0)
        self.assertEqual(report["available_two_hop_paths_by_family"], {})
        self.assertEqual(
            report["rejected_receipts_by_reason"], {"path_replay_mismatch": 1}
        )

    def test_orphan_depth_two_receipt_is_not_a_two_hop_candidate(self) -> None:
        row = {
            "packet": {
                "features": ["micro-vocab"],
                "resources": [
                    {
                        "resourceType": "Observation",
                        "id": "panel",
                        "hasMember": [{"reference": "Observation/terminal"}],
                    },
                    {"resourceType": "Observation", "id": "terminal"},
                ],
                "reference_traversal": {
                    "path_receipts": [
                        {
                            "depth": 2,
                            "from": "Observation/panel",
                            "path": "Observation.hasMember[0].reference",
                            "to": "Observation/terminal",
                            "status": "fetched",
                        }
                    ]
                },
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packets.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = scan_packet_file(path)

        self.assertEqual(report["rows_with_any_fetched_target"], 1)
        self.assertEqual(report["rows_with_depth_two_fetched_target"], 0)
        self.assertEqual(report["available_two_hop_paths_by_family"], {})


if __name__ == "__main__":
    unittest.main()
