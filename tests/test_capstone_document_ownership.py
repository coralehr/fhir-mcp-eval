from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class CapstoneDocumentOwnershipTests(unittest.TestCase):
    def test_capstone_has_one_start_page_and_one_final_report(self) -> None:
        findings = (DOCS / "FINDINGS.md").read_text()
        final_report = (DOCS / "FINAL_REPORT.md").read_text()
        report = (DOCS / "REPORT.md").read_text()

        self.assertLessEqual(len(findings.splitlines()), 100)
        for target in (
            "FINAL_REPORT.md",
            "TRUSTWORTHY_REGRADE.md",
            "REPORT.md",
            "CODE_EXPERIMENT.md",
        ):
            self.assertIn(f"]({target})", findings)
        self.assertIn("canonical", findings.casefold())
        self.assertTrue(final_report.startswith("# Final report"))
        self.assertFalse(report.startswith("# FHIR Tool-Ablation Eval — Final"))
        self.assertIn("tool-ablation", report.splitlines()[0].casefold())

    def test_result_and_judge_tables_have_canonical_owners(self) -> None:
        readme = (ROOT / "README.md").read_text()
        findings = (DOCS / "FINDINGS.md").read_text()
        code = (DOCS / "CODE_EXPERIMENT.md").read_text()
        final_report = (DOCS / "FINAL_REPORT.md").read_text()
        trustworthy = (DOCS / "TRUSTWORTHY_REGRADE.md").read_text()

        self.assertNotIn("| Stratum | n | resource | code |", findings)
        self.assertNotIn("| Stratum | n | resource | code |", code)
        self.assertNotIn("| stratum | n | resource | code |", trustworthy)
        self.assertIn("| arm | overflow stratum", final_report)
        self.assertIn("| judge | accuracy vs ground truth", trustworthy)
        self.assertNotIn("| judge | accuracy vs ground truth", findings)
        self.assertNotIn("| judge | accuracy vs ground truth", code)
        self.assertNotIn("| Arm | Overflow stratum", readme)
        self.assertNotIn("61% accurate", readme)
        self.assertNotIn("**Start here: [A11B", readme)
        self.assertNotIn("the code result", readme)
        self.assertIn("Start with [FINDINGS.md]", readme)

    def test_completed_reproducibility_work_is_not_listed_as_pending(self) -> None:
        readme = (ROOT / "README.md").read_text()

        self.assertNotIn(
            "Publish a minimized reproducibility artifact package with checksums.",
            readme,
        )
        self.assertNotIn("Add a tracked failure-decomposition script.", readme)

    def test_reproducibility_boundary_and_grading_audit_are_explicit(self) -> None:
        artifact_readme = (
            ROOT / "artifacts" / "a0prime-v1" / "README.md"
        ).read_text()
        roadmap = (DOCS / "ROADMAP.md").read_text()

        self.assertIn("score-to-table computation", artifact_readme)
        self.assertIn("295 MB gitignored inputs", artifact_readme)
        self.assertIn("per-qid vote counts", roadmap)
        self.assertIn("judge family and model identifiers", roadmap)
        self.assertIn("no model answers or traces", roadmap)


if __name__ == "__main__":
    unittest.main()
