from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import evidence_ledger


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/results/EXPERIMENT_EVIDENCE_LEDGER.json"
SUCCESSOR = ROOT / "docs/results/a11b-successor-artifacts"


class EvidenceLedgerTests(unittest.TestCase):
    def test_committed_ledger_and_source_receipts_validate(self) -> None:
        ledger = evidence_ledger.validate_ledger(LEDGER, repo_root=ROOT)
        self.assertEqual(len(ledger["experiments"]), 11)
        grid = next(
            row for row in ledger["experiments"] if row["id"] == "generality-grid-99"
        )
        self.assertEqual(grid["status"], "invalid_for_claims")

    def test_successor_receipts_are_local_reproducible_and_fresh(self) -> None:
        for name in (
            "spec.json",
            "receipt.json",
            "public-manifest.json",
            "audit-manifest.json",
        ):
            self.assertEqual(
                (SUCCESSOR / f"source-c-{name}").read_bytes()
                if name in {"spec.json", "receipt.json"}
                else (SUCCESSOR / f"development-c-{name}").read_bytes(),
                (SUCCESSOR / f"source-d-{name}").read_bytes()
                if name in {"spec.json", "receipt.json"}
                else (SUCCESSOR / f"development-d-{name}").read_bytes(),
            )
        receipt = json.loads((SUCCESSOR / "source-c-receipt.json").read_bytes())
        public = json.loads(
            (SUCCESSOR / "development-c-public-manifest.json").read_bytes()
        )
        audit = json.loads(
            (SUCCESSOR / "development-c-audit-manifest.json").read_bytes()
        )
        self.assertNotEqual(
            receipt["raw_output"]["content_sha256"],
            "273e83b72ecd3a5069ea8d10975ec3bffcc16d9b083995fd321e1a7fe2cfc3d2",
        )
        self.assertNotEqual(
            hashlib.sha256(
                (SUCCESSOR / "source-c-receipt.json").read_bytes()
            ).hexdigest(),
            "246d9dc82e27c237629099a01305e9ca65fa4ed49c1beb253803c08c57bc601a",
        )
        self.assertFalse(public["efficacy_materialized"])
        self.assertFalse(audit["efficacy_materialized"])
        self.assertEqual(public["split_counts"], {"development": 64})

    def test_render_preserves_claim_boundaries_and_correct_a11b_population(
        self,
    ) -> None:
        ledger = evidence_ledger.validate_ledger(LEDGER, repo_root=ROOT)
        rendered = evidence_ledger.render_markdown(ledger)
        self.assertIn(
            "64 development Patients + 384 untouched efficacy Patients",
            rendered,
        )
        self.assertIn("No experiment compared storage engines", rendered)
        self.assertIn("wrong denominator", rendered)

    def test_unknown_experiment_reference_fails_closed(self) -> None:
        ledger = json.loads(LEDGER.read_text())
        ledger["claim_register"][0]["evidence"] = ["missing"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps(ledger))
            with self.assertRaisesRegex(
                evidence_ledger.LedgerError,
                "unknown experiment",
            ):
                evidence_ledger.validate_ledger(path, repo_root=ROOT)


if __name__ == "__main__":
    unittest.main()
