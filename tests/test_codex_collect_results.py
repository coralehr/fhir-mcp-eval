import json
import tempfile
import unittest
from pathlib import Path

import codex_collect_results as collector


class CodexCollectResultsTests(unittest.TestCase):
    def test_collects_answer_into_score_taxonomy_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,question,true_answer,true_fhir_ids,patient_fhir_id\n"
                "q1,What was measured?,42,\"{'Observation': ['o1']}\",p1\n",
                encoding="utf-8",
            )
            qdir = root / "run" / "questions" / "q1"
            qdir.mkdir(parents=True)
            answer_path = qdir / "answer.json"
            event_log_path = qdir / "events.jsonl"
            answer_path.write_text(
                json.dumps(
                    {
                        "answer": "42",
                        "source_resource_ids": ["Observation/o1", "Encounter/e1", "Observation/o1"],
                        "evidence_summary": "Used Observation/o1.",
                        "insufficiency_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            event_log_path.write_text(json.dumps({"usage": {"input_tokens": 10, "output_tokens": 3}}) + "\n", encoding="utf-8")
            summary = {
                "questions": [
                    {
                        "question_id": "q1",
                        "status": "ok",
                        "returncode": 0,
                        "answer_path": str(answer_path),
                        "event_log_path": str(event_log_path),
                    }
                ]
            }
            (root / "run" / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

            records = collector.collect_results(input_path=input_path, run_dir=root / "run")

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["agent_answer"], "42")
        self.assertEqual(rec["agent_fhir_resources"], {"Encounter": ["e1"], "Observation": ["o1"]})
        self.assertEqual(rec["usage"], {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})
        self.assertEqual(rec["error"], "")

    def test_missing_answer_is_marked_as_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,question,true_answer,true_fhir_ids,patient_fhir_id\n"
                "q1,What was measured?,42,\"{}\",p1\n",
                encoding="utf-8",
            )
            (root / "run").mkdir()
            (root / "run" / "summary.json").write_text(
                json.dumps({"questions": [{"question_id": "q1", "status": "dry_run", "returncode": None}]}),
                encoding="utf-8",
            )

            records = collector.collect_results(input_path=input_path, run_dir=root / "run")

        self.assertEqual(records[0]["agent_answer"], "")
        self.assertIn("missing_answer", records[0]["error"])

    def test_malformed_answer_is_marked_as_harness_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,question,true_answer,true_fhir_ids,patient_fhir_id\n"
                "q1,What was measured?,42,\"{}\",p1\n",
                encoding="utf-8",
            )
            qdir = root / "run" / "questions" / "q1"
            qdir.mkdir(parents=True)
            answer_path = qdir / "answer.json"
            answer_path.write_text("{}", encoding="utf-8")
            (root / "run" / "summary.json").write_text(
                json.dumps({"questions": [{"question_id": "q1", "status": "ok", "returncode": 0, "answer_path": str(answer_path)}]}),
                encoding="utf-8",
            )

            records = collector.collect_results(input_path=input_path, run_dir=root / "run")

        self.assertEqual(records[0]["agent_answer"], "")
        self.assertIn("answer_schema_error", records[0]["error"])


if __name__ == "__main__":
    unittest.main()
