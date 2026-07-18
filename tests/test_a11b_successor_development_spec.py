from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/prereg/a11b_successor_development_spec.json"


class A11bSuccessorDevelopmentSpecTests(unittest.TestCase):
    def test_candidate_is_development_only_unanchored_and_hash_bound(self) -> None:
        spec = json.loads(SPEC.read_bytes())

        self.assertEqual(
            spec["schema_version"],
            "a11b-successor-development-spec-v1",
        )
        self.assertEqual(spec["seal_state"], "candidate_unanchored")
        self.assertFalse(spec["model_calls_authorized"])
        self.assertFalse(spec["corpus"]["efficacy_materialized"])
        self.assertEqual(spec["design"]["planned_answer_calls"], 192)
        self.assertEqual(
            spec["source"]["partition_manifest_sha256"],
            "ac08e626576706d53a5c28cbaca02df1c14b50d820968d61680327701531e3eb",
        )
        self.assertEqual(
            spec["gate"]["result_manifest_version"],
            "a11b-successor-development-result-manifest-v2",
        )
        self.assertEqual(
            set(spec["gate"]["result_manifest_binds"]),
            {
                "assignments_sha256",
                "outcomes_sha256",
                "audit_manifest_sha256",
                "gold_rows_sha256",
                "accepted_token_receipts_sha256",
                "all_attempt_token_receipts_sha256",
                "question_count",
                "arms",
                "accepted_attempts",
                "all_attempts",
                "accepted_token_usage_complete",
                "all_attempt_token_usage_complete",
                "token_economics",
            },
        )
        self.assertEqual(
            spec["grading"]["version"],
            "a11b-successor-development-exact-alias-grading-v1",
        )
        self.assertEqual(spec["grading"]["panel_model_calls"], 0)
        self.assertEqual(
            spec["gate"]["valid_no_headroom_status"],
            "failed_receipt_published",
        )
        self.assertTrue(
            spec["grading"]["explanatory_prose_around_an_alias_is_incorrect"]
        )
        self.assertEqual(
            spec["answer_protocol"]["model_controlled_field_limits"],
            {
                "answer_utf8_bytes": 128,
                "evidence_summary_utf8_bytes": 1024,
                "insufficiency_reason_utf8_bytes": 1024,
                "source_resource_ids": 16,
                "source_resource_id_utf8_bytes": 128,
            },
        )
        self.assertEqual(
            spec["answer_protocol"]["offline_length_unit"],
            "utf8_bytes_stricter_fail_closed",
        )
        self.assertEqual(
            spec["answer_protocol"]["registered_schema_sha256"],
            hashlib.sha256(
                (ROOT / "schemas/a11b_answer_v2.schema.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            spec["answer_protocol"]["transport_schema_sha256"],
            hashlib.sha256(
                (
                    ROOT / "schemas/a11b_answer_v2_transport.schema.json"
                ).read_bytes()
            ).hexdigest(),
        )
        self.assertIn(
            "external commit-pinned exact-head approval anchor",
            spec["remaining_seal_requirements"],
        )


if __name__ == "__main__":
    unittest.main()
