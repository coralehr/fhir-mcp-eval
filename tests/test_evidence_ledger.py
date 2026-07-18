from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import evidence_ledger


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/results/EXPERIMENT_EVIDENCE_LEDGER.json"


class EvidenceLedgerTests(unittest.TestCase):
    def test_committed_ledger_and_source_receipts_validate(self) -> None:
        ledger = evidence_ledger.validate_ledger(LEDGER, repo_root=ROOT)
        self.assertEqual(len(ledger["experiments"]), 10)
        grid = next(
            row for row in ledger["experiments"] if row["id"] == "generality-grid-99"
        )
        self.assertEqual(grid["status"], "invalid_for_claims")

    def test_render_preserves_claim_boundaries_and_correct_a11b_population(self) -> None:
        ledger = evidence_ledger.validate_ledger(LEDGER, repo_root=ROOT)
        rendered = evidence_ledger.render_markdown(ledger)
        self.assertIn("64 development Patients + 384 untouched efficacy Patients", rendered)
        self.assertIn("No experiment compared storage engines", rendered)
        self.assertIn("wrong denominator", rendered)

    def test_unknown_experiment_reference_fails_closed(self) -> None:
        ledger = json.loads(LEDGER.read_text())
        ledger["claim_register"][0]["evidence"] = ["missing"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps(ledger))
            with self.assertRaisesRegex(evidence_ledger.LedgerError, "unknown experiment"):
                evidence_ledger.validate_ledger(path, repo_root=ROOT)


if __name__ == "__main__":
    unittest.main()
