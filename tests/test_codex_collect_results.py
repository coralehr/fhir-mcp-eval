import json
import tempfile
import unittest
from pathlib import Path

import codex_collect_results as collector


class CodexCollectResultsTests(unittest.TestCase):
    def test_extract_usage_keeps_first_complete_record_after_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_log_path = Path(tmp) / "events.jsonl"
            event_log_path.write_text(
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}})
                + "\n"
                + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 999, "output_tokens": 99}})
                + "\n"
                + 'put_tokens": 888, "output_tokens": 88}}\n',
                encoding="utf-8",
            )

            usage = collector.extract_usage(event_log_path)

        self.assertEqual(usage, {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})

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
            event_log_path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10, "output_tokens": 3},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
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

    def test_packet_answer_with_tool_event_is_not_collected_as_valid(self):
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
            event_log_path = qdir / "events.jsonl"
            answer_path.write_text(
                json.dumps(
                    {
                        "answer": "42",
                        "source_resource_ids": ["Observation/o1"],
                        "evidence_summary": "Read a repository answer file.",
                        "insufficiency_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            event_log_path.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "call-1", "type": "command_execution"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            summary = {
                "manifest": {"run_config": {"mode": "packet"}},
                "questions": [
                    {
                        "question_id": "q1",
                        "status": "ok",
                        "returncode": 0,
                        "answer_path": str(answer_path),
                        "event_log_path": str(event_log_path),
                    }
                ],
            }
            (root / "run" / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

            records = collector.collect_results(input_path=input_path, run_dir=root / "run")

        self.assertEqual(records[0]["agent_answer"], "")
        self.assertEqual(records[0]["agent_fhir_resources"], {})
        self.assertIn("contaminated_event_log", records[0]["error"])
        self.assertTrue(records[0]["event_integrity"]["contaminated"])

    def test_packet_contamination_marker_overrides_stray_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,question,true_answer,true_fhir_ids,patient_fhir_id\n"
                'q1,What was measured?,42,"{}",p1\n',
                encoding="utf-8",
            )
            qdir = root / "run" / "questions" / "q1"
            qdir.mkdir(parents=True)
            answer_path = qdir / "answer.json"
            answer_path.write_text(
                json.dumps(
                    {
                        "answer": "42",
                        "source_resource_ids": ["Observation/o1"],
                        "evidence_summary": "Stray answer.",
                        "insufficiency_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            (qdir / "contamination.json").write_text(
                json.dumps({"contaminated": True}), encoding="utf-8"
            )
            (qdir / "events.jsonl").write_text(
                json.dumps({"type": "turn.completed"}) + "\n", encoding="utf-8"
            )
            summary = {
                "manifest": {"run_config": {"mode": "packet"}},
                "questions": [
                    {
                        "question_id": "q1",
                        "status": "ok",
                        "returncode": 0,
                        "answer_path": str(answer_path),
                    }
                ],
            }
            (root / "run" / "summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )

            records = collector.collect_results(
                input_path=input_path, run_dir=root / "run"
            )

        self.assertEqual(records[0]["agent_answer"], "")
        self.assertIn("contamination_marker", records[0]["error"])

    def test_summary_is_merged_with_all_question_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            questions = run_dir / "questions"
            for qid in ("q1", "q2"):
                qdir = questions / qid
                qdir.mkdir(parents=True)
                (qdir / "answer.json").write_text(
                    json.dumps(
                        {
                            "answer": qid,
                            "source_resource_ids": [],
                            "evidence_summary": "evidence",
                            "insufficiency_reason": None,
                        }
                    ),
                    encoding="utf-8",
                )
                (qdir / "events.jsonl").write_text(
                    json.dumps({"type": "turn.completed"}) + "\n",
                    encoding="utf-8",
                )
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "manifest": {"run_config": {"mode": "packet"}},
                        "questions": [
                            {
                                "question_id": "q2",
                                "status": "ok",
                                "returncode": 0,
                                "answer_path": str(questions / "q2" / "answer.json"),
                                "event_log_path": str(questions / "q2" / "events.jsonl"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            items = collector.load_summary(run_dir)

        self.assertEqual({item["question_id"] for item in items}, {"q1", "q2"})


if __name__ == "__main__":
    unittest.main()
