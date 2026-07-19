import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import a0prime_cluster_stats
import a0prime_verdict
import build_a0prime_artifact


REPO = Path(__file__).resolve().parents[1]


class A0PrimeReproducibilityTests(unittest.TestCase):
    def test_verdict_runs_from_minimized_artifact_without_raw_runs(self):
        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "questions": [
                {
                    "question_id": "q1",
                    "patient_fhir_id": "Patient/p1",
                    "stratum": "overflow",
                    "a0_correct": 0,
                    "a5_correct": 1,
                    "a0prime_correct": 1,
                    "a0prime_overflow": False,
                    "a0prime_grade_source": "numeric",
                },
                {
                    "question_id": "q2",
                    "patient_fhir_id": "Patient/p2",
                    "stratum": "matched",
                    "a0_correct": 1,
                    "a5_correct": 0,
                    "a0prime_correct": 1,
                    "a0prime_overflow": False,
                    "a0prime_grade_source": "panel",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "score-artifact.json"
            artifact_path.write_text(
                json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "a0prime_verdict.py"),
                    "--artifact",
                    str(artifact_path),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("A0 raw", completed.stdout)
        self.assertIn("A5 code", completed.stdout)
        self.assertIn("A0' projected", completed.stdout)
        self.assertIn("A0' still overflows on 0/1", completed.stdout)
        self.assertIn("projection-alone recovers 100%", completed.stdout)

    def test_builder_minimizes_raw_answers_and_records_source_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = root / "runs" / "full409"
            a0prime = root / "runs" / "a0prime"
            votes = a0prime / "codex_votes"
            full.mkdir(parents=True)
            votes.mkdir(parents=True)
            resource = [
                {
                    "question_id": "q1",
                    "patient_fhir_id": "Patient/p1",
                    "agent_answer": "1",
                    "true_answer": "[[1]]",
                    "trace": "must not be copied",
                },
                {
                    "question_id": "q2",
                    "patient_fhir_id": "Patient/p2",
                    "agent_answer": "yes",
                    "true_answer": "[['yes']]",
                    "trace": "must not be copied",
                },
            ]
            code = [{**row, "agent_answer": "code"} for row in resource]
            projected = [
                {**resource[0], "agent_answer": "1.0", "true_answer": "[[1.0]]"},
                {**resource[1], "agent_answer": "yes"},
            ]
            for path, value in (
                (full / "multi_turn_resource.json", resource),
                (full / "multi_turn_code_resource.json", code),
                (a0prime / "multi_turn_projected_resource.json", projected),
                (
                    full / "det_labels.json",
                    {
                        "resource|q1": 0,
                        "resource|q2": 1,
                        "code|q1": 1,
                        "code|q2": 0,
                    },
                ),
                (full / "panel_votes.json", []),
                (full / "panel_votes_new.json", []),
                (
                    full / "_strata.json",
                    {"ids": ["q1", "q2"], "overflow": ["q1"], "matched": ["q2"]},
                ),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
            for panel in (1, 2, 3):
                (votes / f"p{panel}_b00.json").write_text(
                    json.dumps({"grades": [{"qid": "q2", "label": 1}]}),
                    encoding="utf-8",
                )

            artifact = build_a0prime_artifact.build_artifact(root)

        self.assertEqual(artifact["schema_version"], "a0prime-score-artifact-v1")
        self.assertEqual(len(artifact["questions"]), 2)
        self.assertEqual(
            artifact["questions"][0]["a0prime_grade_source"], "numeric"
        )
        self.assertEqual(
            artifact["questions"][1]["a0prime_grade_source"], "panel"
        )
        self.assertNotIn("trace", json.dumps(artifact))
        receipt_files = artifact["source_receipt"]["files"]
        self.assertEqual(len(receipt_files), 10)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in receipt_files))

    def test_cluster_stats_are_patient_clustered_and_bind_the_score_artifact(self):
        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "questions": [
                {
                    "question_id": "q1",
                    "patient_fhir_id": "Patient/p1",
                    "stratum": "matched",
                    "a0_correct": 1,
                    "a5_correct": 0,
                    "a0prime_correct": 1,
                    "a0prime_overflow": False,
                    "a0prime_grade_source": "numeric",
                },
                {
                    "question_id": "q2",
                    "patient_fhir_id": "Patient/p1",
                    "stratum": "matched",
                    "a0_correct": 1,
                    "a5_correct": 1,
                    "a0prime_correct": 0,
                    "a0prime_overflow": False,
                    "a0prime_grade_source": "panel",
                },
                {
                    "question_id": "q3",
                    "patient_fhir_id": "Patient/p2",
                    "stratum": "overflow",
                    "a0_correct": 0,
                    "a5_correct": 1,
                    "a0prime_correct": 1,
                    "a0prime_overflow": False,
                    "a0prime_grade_source": "numeric",
                },
            ],
        }
        payload = (
            json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "score-artifact.json"
            path.write_bytes(payload)

            result = a0prime_cluster_stats.compute(path, n_boot=100, seed=7)

        self.assertEqual(
            result["score_artifact_sha256"], hashlib.sha256(payload).hexdigest()
        )
        self.assertEqual(result["matched_question_count"], 2)
        self.assertEqual(result["matched_patient_count"], 1)
        self.assertEqual(set(result["contrasts"]), {"a0prime_minus_a0", "a5_minus_a0"})

    def test_committed_artifact_reproduces_published_table_and_intervals(self):
        artifact_path = REPO / "artifacts" / "a0prime-v1" / "score-artifact.json"
        payload = artifact_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            "39a545d9f5da0d2ec7559f7d699b9dee967c7996fb35bf9f771d71e7b9b35240",
        )
        rendered = a0prime_verdict.render_verdict(
            a0prime_verdict.load_artifact(artifact_path)
        )
        self.assertIn("A0 raw                 0.0%(262)        70.7%(147)        25.4%(409)", rendered)
        self.assertIn("A5 code               65.6%(262)        64.6%(147)        65.3%(409)", rendered)
        self.assertIn("A0' projected         22.1%(262)        70.1%(147)        39.4%(409)", rendered)
        stats = a0prime_cluster_stats.compute(artifact_path)
        a0prime_ci = stats["contrasts"]["a0prime_minus_a0"]["cluster_bootstrap"]
        a5_ci = stats["contrasts"]["a5_minus_a0"]["cluster_bootstrap"]
        self.assertAlmostEqual(a0prime_ci["ci_low"], -0.04929577464788732)
        self.assertAlmostEqual(a0prime_ci["ci_high"], 0.03164556962025317)
        self.assertAlmostEqual(a5_ci["ci_low"], -0.1111111111111111)
        self.assertAlmostEqual(a5_ci["ci_high"], -0.01408450704225352)


if __name__ == "__main__":
    unittest.main()
