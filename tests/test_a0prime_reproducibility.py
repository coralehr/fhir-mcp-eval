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
import decompose_a0prime_failures


REPO = Path(__file__).resolve().parents[1]


class A0PrimeReproducibilityTests(unittest.TestCase):
    def test_verdict_runs_from_minimized_artifact_without_raw_runs(self):
        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "question_count": 2,
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

    def test_verdict_rejects_artifact_missing_a_required_stratum(self):
        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "question_count": 1,
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
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "score-artifact.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "both overflow and matched strata"):
                a0prime_verdict.load_artifact(path)

    def test_verdict_rejects_declared_question_count_mismatch(self):
        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "question_count": 2,
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
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "score-artifact.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "question_count does not match"):
                a0prime_verdict.load_artifact(path)

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
                    "question": "What is the latest value?",
                    "agent_answer": "1",
                    "true_answer": "[[1]]",
                    "trace": "must not be copied",
                },
                {
                    "question_id": "q2",
                    "patient_fhir_id": "Patient/p2",
                    "question": "What was the first recorded value?",
                    "agent_answer": "yes",
                    "true_answer": "[['yes']]",
                    "trace": "must not be copied",
                },
            ]
            code = [{**row, "agent_answer": "code"} for row in resource]
            projected = [
                {**resource[0], "agent_answer": "1.0", "true_answer": "[[1.0]]"},
                {
                    **resource[1],
                    "agent_answer": "I cannot determine it because the result was truncated.",
                    "trace": [
                        {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "get_resources_by_patient_fhir_id",
                                        "arguments": json.dumps(
                                            {"resource_type": "Observation"}
                                        ),
                                    }
                                }
                            ]
                        },
                        {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "get_resources_by_patient_fhir_id",
                                        "arguments": json.dumps(
                                            {"resource_type": "Observation"}
                                        ),
                                    }
                                }
                            ]
                        },
                        {"role": "tool", "content": "12345"},
                    ],
                },
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
                    {"ids": ["q1", "q2"], "overflow": ["q2"], "matched": ["q1"]},
                ),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
            for panel in (1, 2, 3):
                (votes / f"p{panel}_b00.json").write_text(
                    json.dumps({"grades": [{"qid": "q2", "label": 0}]}),
                    encoding="utf-8",
                )

            artifact = build_a0prime_artifact.build_artifact(
                root, token_counter=len
            )

        self.assertEqual(artifact["schema_version"], "a0prime-score-artifact-v1")
        self.assertEqual(len(artifact["questions"]), 2)
        self.assertEqual(
            artifact["questions"][0]["a0prime_grade_source"], "numeric"
        )
        self.assertEqual(
            artifact["questions"][1]["a0prime_grade_source"], "panel"
        )
        self.assertTrue(
            artifact["questions"][1]["a0prime_cap_drop_language"]
        )
        self.assertTrue(
            artifact["questions"][1]["a0prime_earliest_or_first"]
        )
        self.assertTrue(
            artifact["questions"][1]["a0prime_repeated_resource_type"]
        )
        self.assertEqual(
            artifact["questions"][1][
                "a0prime_max_tool_content_cl100k_tokens"
            ],
            5,
        )
        self.assertNotIn("trace", json.dumps(artifact))
        receipt_files = artifact["source_receipt"]["files"]
        self.assertEqual(len(receipt_files), 10)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in receipt_files))

    def test_cluster_stats_are_patient_clustered_and_bind_the_score_artifact(self):
        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "question_count": 3,
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
            "b0bc19c605aea20ada713613ee1f8d1e1bfb1d814f6bba38a4e77637b3ddc242",
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
        decomposition = decompose_a0prime_failures.compute(artifact_path)
        self.assertEqual(
            decomposition["counts"],
            {
                "cap_drop_language": 82,
                "correct": 58,
                "earliest_or_first": 40,
                "fit_but_wrong": 107,
                "repeated_resource_overflow": 54,
                "still_overflow": 97,
            },
        )
        self.assertEqual(
            decomposition["code_recovery"],
            {"cap_drop_language": 55, "still_overflow": 64},
        )
        self.assertEqual(
            decomposition["single_tool_block_tokens"],
            {
                "encoding": "cl100k_base",
                "max_tokens": 24815,
                "over_32000": 0,
                "question_count": 97,
            },
        )

    def test_failure_decomposition_emits_qid_categories_and_summary(self):
        def row(
            qid: str,
            *,
            correct: int,
            overflow: bool = False,
            cap_drop: bool = False,
            earliest: bool = False,
            repeated: bool = False,
            max_tool_tokens: int = 0,
            a5_correct: int = 0,
            stratum: str = "overflow",
        ) -> dict:
            return {
                "question_id": qid,
                "patient_fhir_id": f"Patient/{qid}",
                "stratum": stratum,
                "a0_correct": 0,
                "a5_correct": a5_correct,
                "a0prime_correct": correct,
                "a0prime_overflow": overflow,
                "a0prime_grade_source": "failure" if overflow else "numeric",
                "a0prime_cap_drop_language": cap_drop,
                "a0prime_earliest_or_first": earliest,
                "a0prime_repeated_resource_type": repeated,
                "a0prime_max_tool_content_cl100k_tokens": max_tool_tokens,
            }

        artifact = {
            "schema_version": "a0prime-score-artifact-v1",
            "source_receipt": {"files": []},
            "question_count": 5,
            "questions": [
                row("correct", correct=1),
                row(
                    "overflow",
                    correct=0,
                    overflow=True,
                    repeated=True,
                    max_tool_tokens=20,
                    a5_correct=1,
                ),
                row("cap", correct=0, cap_drop=True, earliest=True, a5_correct=1),
                row("wrong", correct=0),
                row("matched", correct=1, stratum="matched"),
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "score-artifact.json"
            path.write_text(
                json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            result = decompose_a0prime_failures.compute(path)
            markdown = decompose_a0prime_failures.render_markdown(result)

        self.assertEqual(
            result["counts"],
            {
                "cap_drop_language": 1,
                "correct": 1,
                "earliest_or_first": 1,
                "fit_but_wrong": 2,
                "repeated_resource_overflow": 1,
                "still_overflow": 1,
            },
        )
        self.assertEqual(result["code_recovery"]["cap_drop_language"], 1)
        self.assertEqual(result["code_recovery"]["still_overflow"], 1)
        self.assertEqual(
            result["single_tool_block_tokens"],
            {
                "encoding": "cl100k_base",
                "max_tokens": 20,
                "over_32000": 0,
                "question_count": 1,
            },
        )
        self.assertEqual(len(result["questions"]), 4)
        self.assertIn("| correct | 1 |", markdown)
        self.assertIn("maximum is 20 tokens", markdown)


if __name__ == "__main__":
    unittest.main()
