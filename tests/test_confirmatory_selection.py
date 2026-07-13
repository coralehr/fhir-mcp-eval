import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import final_confirmatory_result
import grade_a6a_confirmatory
from question_selection import load_scheduled_question_ids, select_question_rows


class QuestionSelectionTests(unittest.TestCase):
    def test_no_schedule_preserves_full_input(self):
        ids = load_scheduled_question_ids()

        self.assertIsNone(ids)
        self.assertEqual(select_question_rows({"q1": 1, "q2": 2}, ids), {"q1": 1, "q2": 2})

    def test_json_spec_and_repeated_ids_form_one_deduplicated_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "grid.json"
            spec.write_text(json.dumps({"question_ids": ["q2", "q1", "q2"]}), encoding="utf-8")

            ids = load_scheduled_question_ids(spec_path=spec, repeated_ids=["q3", "q1"])

        self.assertEqual(ids, ["q2", "q1", "q3"])

    def test_unknown_scheduled_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing from input"):
            select_question_rows({"q1": {}}, ["q1", "q-missing"])

    def test_grader_rejects_correct_answer_with_packet_tool_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            qdir = run_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "answer.json").write_text(
                json.dumps({"answer": "42", "insufficiency_reason": None}),
                encoding="utf-8",
            )
            (qdir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "call-1", "type": "command_execution"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            verdicts, panel = grade_a6a_confirmatory.grade_arm(
                run_dir,
                {"q1": {"question_id": "q1", "true_answer": "42"}},
            )

        self.assertEqual(verdicts, {"q1": 0})
        self.assertEqual(panel, [])

    def test_contamination_marker_overrides_later_stray_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            qdir = run_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "contamination.json").write_text(
                json.dumps({"contaminated": True}), encoding="utf-8"
            )
            (qdir / "answer.json").write_text(
                json.dumps({"answer": "42", "insufficiency_reason": None}),
                encoding="utf-8",
            )
            (qdir / "events.jsonl").write_text(
                json.dumps({"type": "turn.completed"}) + "\n", encoding="utf-8"
            )

            verdicts, panel = grade_a6a_confirmatory.grade_arm(
                run_dir,
                {"q1": {"question_id": "q1", "true_answer": "42"}},
            )

        self.assertEqual(verdicts, {"q1": 0})
        self.assertEqual(panel, [])

    def test_secondary_metrics_exclude_contaminated_stray_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qdir = root / "run" / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "contamination.json").write_text(
                json.dumps({"contaminated": True}), encoding="utf-8"
            )
            (qdir / "answer.json").write_text(
                json.dumps(
                    {"answer": "42", "insufficiency_reason": "should not count"}
                ),
                encoding="utf-8",
            )
            packets = root / "packets.jsonl"
            packets.write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "packet": {"bounds": {"char_count": 10}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            metrics = final_confirmatory_result.arm_secondary(
                root / "run", packets, ["q1"]
            )

        self.assertEqual(metrics["answered"], 0)
        self.assertEqual(metrics["abstentions"], 0)

    def test_grading_and_final_metrics_use_only_scheduled_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,question,true_answer,patient_fhir_id,main_table_name\n"
                "q1,Question one,1,p1,Observation\n"
                "q2,Question two,1,p2,Observation\n"
                "q3,Question three,1,p3,Condition\n",
                encoding="utf-8",
            )
            spec = root / "grid.json"
            spec.write_text(json.dumps({"question_ids": ["q1", "q2"]}), encoding="utf-8")
            a_dir = root / "a"
            b_dir = root / "b"

            def write_answer(run_dir: Path, question_id: str, answer: str, *, insufficient: bool = False) -> None:
                qdir = run_dir / "questions" / question_id
                qdir.mkdir(parents=True, exist_ok=True)
                (qdir / "answer.json").write_text(
                    json.dumps(
                        {
                            "answer": answer,
                            "insufficiency_reason": "missing" if insufficient else None,
                        }
                    ),
                    encoding="utf-8",
                )

            write_answer(a_dir, "q1", "1")
            write_answer(a_dir, "q2", "0")
            write_answer(a_dir, "q3", "1", insufficient=True)
            write_answer(b_dir, "q1", "0")
            write_answer(b_dir, "q2", "1")
            write_answer(b_dir, "q3", "1", insufficient=True)

            grading_dir = root / "grading"
            with mock.patch(
                "sys.argv",
                [
                    "grade_a6a_confirmatory.py",
                    "--a6a-dir",
                    str(a_dir),
                    "--a0prime-dir",
                    str(b_dir),
                    "--input",
                    str(input_path),
                    "--out",
                    str(grading_dir),
                    "--question-spec",
                    str(spec),
                ],
            ):
                self.assertEqual(grade_a6a_confirmatory.main(), 0)

            det = json.loads((grading_dir / "det_verdicts.json").read_text(encoding="utf-8"))
            self.assertEqual(set(det["a6a"]), {"q1", "q2"})
            self.assertEqual(set(det["a0prime"]), {"q1", "q2"})
            partial = json.loads((grading_dir / "partial_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(partial["scheduled_question_count"], 2)
            self.assertTrue(partial["explicit_question_schedule"])
            (grading_dir / "panel_verdicts.json").write_text("{}\n", encoding="utf-8")

            a_packets = root / "a-packets.jsonl"
            b_packets = root / "b-packets.jsonl"

            def write_packets(path: Path, counts: dict[str, int]) -> None:
                path.write_text(
                    "".join(
                        json.dumps(
                            {
                                "question_id": question_id,
                                "packet": {"bounds": {"char_count": char_count}},
                            }
                        )
                        + "\n"
                        for question_id, char_count in counts.items()
                    ),
                    encoding="utf-8",
                )

            write_packets(a_packets, {"q1": 10, "q2": 20, "q3": 900})
            write_packets(b_packets, {"q1": 30, "q2": 40, "q3": 800})
            with mock.patch(
                "sys.argv",
                [
                    "final_confirmatory_result.py",
                    "--grading-dir",
                    str(grading_dir),
                    "--input",
                    str(input_path),
                    "--a6a-packets",
                    str(a_packets),
                    "--a0prime-packets",
                    str(b_packets),
                    "--a6a-dir",
                    str(a_dir),
                    "--a0prime-dir",
                    str(b_dir),
                    "--question-spec",
                    str(spec),
                ],
            ):
                self.assertEqual(final_confirmatory_result.main(), 0)

            result = json.loads((grading_dir / "final_result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["n"], 2)
            self.assertEqual(result["primary"]["cluster_bootstrap"]["n_pairs"], 2)
            self.assertEqual(result["question_selection"]["question_ids"], ["q1", "q2"])
            self.assertEqual(result["secondary"]["a6a"]["answered"], 2)
            self.assertEqual(result["secondary"]["a6a"]["packet_chars_total"], 30)
            self.assertEqual(result["secondary"]["a0prime"]["packet_chars_total"], 70)


if __name__ == "__main__":
    unittest.main()
