import csv
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import codex_harness
import panel_grade
import qt4_analysis


ARMS = ("a6a", "qt4v", "qt4t")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class SyntheticQt4Run:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.qids = ["q1", "q2"]
        self.spec = root / "spec.json"
        write_json(
            self.spec,
            {"question_ids": self.qids, "expected_question_count": len(self.qids)},
        )
        self.input = root / "input.csv"
        with self.input.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "question_id",
                    "question",
                    "true_answer",
                    "patient_fhir_id",
                    "main_table_name",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "question_id": "q1",
                    "question": "What was the count?",
                    "true_answer": "1",
                    "patient_fhir_id": "p1",
                    "main_table_name": "microbiologyevents",
                }
            )
            writer.writerow(
                {
                    "question_id": "q2",
                    "question": "What was the result?",
                    "true_answer": "positive",
                    "patient_fhir_id": "p2",
                    "main_table_name": "microbiologyevents",
                }
            )

        self.packets: dict[str, Path] = {}
        self.run_dirs: dict[str, Path] = {}
        for arm_index, arm in enumerate(ARMS):
            packet_path = root / f"{arm}.jsonl"
            packet_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "question_id": qid,
                            "packet": {
                                "kind": "bounded_fhir_packet",
                                "features": [] if arm == "a6a" else [arm],
                                "bounds": {"char_count": 999_999},
                                "resources": [
                                    {
                                        "resourceType": "Observation",
                                        "id": f"{arm}-{qid}",
                                        "valueString": "µ-positive",
                                    }
                                ],
                            },
                        },
                        sort_keys=True,
                    )
                    + "\n"
                    for qid in self.qids
                ),
                encoding="utf-8",
            )
            self.packets[arm] = packet_path
            self.run_dirs[arm] = root / f"run-{arm}"

        supporting = root / "supporting"
        supporting.mkdir()
        schema = supporting / "schema.json"
        schema.write_text('{"type":"object"}\n', encoding="utf-8")
        runner = supporting / "runner.py"
        runner.write_text("# synthetic runner\n", encoding="utf-8")
        gate = supporting / "gate.json"
        recall_arm = {
            "questions_with_gold": 2,
            "gold_id_occurrences": 2,
            "retrieved_gold_id_occurrences": 1,
            "id_weighted_recall": 0.5,
            "macro_recall": 0.5,
            "any_coverage_count": 1,
            "any_coverage": 0.5,
            "all_coverage_count": 1,
            "all_coverage": 0.5,
        }
        write_json(
            gate,
            {
                "schema_version": "qt4-zero-model-packet-gate-v1",
                "passed": True,
                "failed_gates": [],
                "inputs": {
                    "question_spec": {
                        "sha256": qt4_analysis.sha256_file(self.input)
                    },
                    **{
                        arm: {"sha256": qt4_analysis.sha256_file(self.packets[arm])}
                        for arm in ARMS
                    },
                },
                "evaluation_only_gold_metrics": {
                    "recall": {
                        "microbiology": {arm: recall_arm for arm in ARMS},
                        "overall": {arm: recall_arm for arm in ARMS},
                    },
                    "vocabulary_gold_change": {
                        "gold_id_occurrences_gained": 2,
                        "gold_id_occurrences_lost": 0,
                    },
                    "traversal_gold_gain": {
                        "gold_id_occurrences_gained": 1,
                        "gold_id_occurrences_lost": 0,
                    },
                },
                "traversal": {
                    "target_outcomes": {
                        "fetched": 1,
                        "already_present": 0,
                        "missing": 2,
                        "resource_capped": 3,
                        "byte_capped": 0,
                    },
                    "fetch_attempt_count": 3,
                    "added_resource_count": 1,
                    "added_serialized_bytes": 100,
                    "path_receipts_omitted": 0,
                    "questions_with_fetched_target": 1,
                    "serialized_path_family_counts": {
                        "Observation.hasMember": 1
                    },
                    "diagnostic_report_path_use": {"total": 0},
                },
                "resource_footprint": {
                    "arms": {
                        arm: {"packet_count": 2, "resource_count": 2}
                        for arm in ARMS
                    },
                    "deltas": {},
                },
                "equivalence": {
                    "non_micro_packet": {"matched": 0, "total": 0},
                    "non_micro_prompt": {"matched": 0, "total": 0},
                },
            },
        )

        snapshots: dict[str, dict[str, str]] = {}
        snapshot_sources = {
            "spec": self.spec,
            "gate_report": gate,
            "input": self.input,
            "schema": schema,
            "harness": Path(codex_harness.__file__).resolve(),
            "runner": runner,
            **{f"packet_{arm}": self.packets[arm] for arm in ARMS},
        }
        for name, source in snapshot_sources.items():
            snapshot = root / "controller" / "artifacts" / f"{name}{source.suffix}"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(source.read_bytes())
            snapshots[name] = {
                "source_path": str(source.resolve()),
                "snapshot_path": str(snapshot.resolve()),
                "sha256": qt4_analysis.sha256_file(source),
            }

        self.controller = root / "controller" / "manifest.json"
        manifest = {
            "created_at": "2026-07-13T00:00:00+00:00",
            "kind": "qt4_micro_interleaved_controller_manifest",
            "schema_version": "qt4-controller-v2",
            "question_ids": self.qids,
            "schedule": [
                {"question_id": qid, "arm": arm}
                for qid in self.qids
                for arm in ARMS
            ],
            "execution": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "codex_bin": "/test/codex",
                "codex_version": "codex-test 1",
            },
            "outputs": {
                arm: str(self.run_dirs[arm].resolve()) for arm in ARMS
            },
            "snapshots": snapshots,
        }
        write_json(self.controller, manifest)
        self.controller_sha = qt4_analysis.sha256_file(self.controller)

        answers = {
            "a6a": {"q1": "0", "q2": "negative"},
            "qt4v": {"q1": "1", "q2": "positive"},
            "qt4t": {"q1": "1", "q2": "negative"},
        }
        usage = {
            "a6a": [(100, 10, 20, 5), (200, None, 30, None)],
            "qt4v": [(150, 15, 25, 6), (250, 25, 35, 7)],
            "qt4t": [(175, 17, 28, 8), (275, 27, 38, 9)],
        }
        packet_records = {
            arm: qt4_analysis.load_packet_records(self.packets[arm], self.qids)
            for arm in ARMS
        }
        input_rows = qt4_analysis.load_gold_rows(self.input, self.qids)
        for arm in ARMS:
            for index, qid in enumerate(self.qids):
                qdir = self.run_dirs[arm] / "questions" / qid
                qdir.mkdir(parents=True, exist_ok=True)
                answer = {
                    "answer": answers[arm][qid],
                    "source_resource_ids": [f"Observation/{arm}-{qid}"],
                    "evidence_summary": "Synthetic evidence.",
                    "insufficiency_reason": None,
                }
                write_json(qdir / "answer.json", answer)
                prompt = codex_harness.build_prompt(
                    {**input_rows[qid], **packet_records[arm][qid]},
                    mode="packet",
                )
                (qdir / "prompt.txt").write_text(prompt, encoding="utf-8")
                inp, cached, out, reasoning = usage[arm][index]
                usage_value = {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "total_tokens": inp + out,
                }
                if cached is not None:
                    usage_value["cached_input_tokens"] = cached
                if reasoning is not None:
                    usage_value["reasoning_output_tokens"] = reasoning
                start = dt.datetime(2026, 7, 13, 12, 0, index, tzinfo=dt.UTC)
                end = start + dt.timedelta(seconds=2 + index)
                events = [
                    {"type": "thread.started", "timestamp": start.isoformat()},
                    {
                        "type": "turn.completed",
                        "timestamp": end.isoformat(),
                        "usage": usage_value,
                    },
                ]
                (qdir / "events.jsonl").write_text(
                    "".join(json.dumps(event) + "\n" for event in events),
                    encoding="utf-8",
                )
                audit = codex_harness.audit_event_log(qdir / "events.jsonl")
                receipt = {
                    "kind": "qt4_attempt_completion",
                    "schema_version": "qt4-attempt-v2",
                    "controller_manifest_sha256": self.controller_sha,
                    "arm": arm,
                    "question_id": qid,
                    "packet_sha256": qt4_analysis.sha256_file(self.packets[arm]),
                    "schema_sha256": qt4_analysis.sha256_file(schema),
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "attempt_number": 1,
                    "harness_exit_code": 0,
                    "returncode": 0,
                    "status": "answered",
                    "event_integrity": audit,
                    "usage": usage_value,
                    "answer_sha256": qt4_analysis.sha256_file(qdir / "answer.json"),
                    "event_log_sha256": qt4_analysis.sha256_file(
                        qdir / "events.jsonl"
                    ),
                    "prompt_sha256": qt4_analysis.sha256_file(qdir / "prompt.txt"),
                }
                write_json(qdir / "completion.json", receipt)

    def artifacts(self) -> dict[str, qt4_analysis.ArmArtifacts]:
        return {
            arm: qt4_analysis.ArmArtifacts(
                name=arm,
                packet_path=self.packets[arm],
                run_dir=self.run_dirs[arm],
            )
            for arm in ARMS
        }


class Qt4AnalysisTests(unittest.TestCase):
    def prepare(self, synthetic: SyntheticQt4Run, out: Path) -> dict:
        return qt4_analysis.prepare_grading(
            controller_manifest=synthetic.controller,
            question_spec=synthetic.spec,
            input_path=synthetic.input,
            arms=synthetic.artifacts(),
            out_dir=out,
            expected_question_count=2,
        )

    def complete_panel(self, grading_dir: Path) -> None:
        queue_path = grading_dir / "panel_queue.jsonl"
        queue = panel_grade.load_queue(queue_path)
        config = panel_grade.build_judge_config(
            model="gpt-5.6-sol",
            effort="high",
            batch_size=20,
            votes=3,
            timeout=600,
            codex_bin="/test/codex",
            codex_version="codex-test 1",
        )
        blinded = panel_grade.prepare_blinded_items(queue, config)
        manifest = panel_grade.build_cache_manifest(blinded, config)
        cache = panel_grade.new_cache(manifest, blinded)
        correctness = {
            "a6a": False,
            "qt4v": True,
            "qt4t": False,
        }
        for item in cache["items"].values():
            item["votes"] = [correctness[item["host"]["arm"]]] * 3
        cache["usage_receipts"].append(
            {
                "status": "accepted",
                "vote_round": 0,
                "batch_number": 0,
                "opaque_ids": sorted(cache["items"]),
                "event_stream_sha256": "f" * 64,
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 3,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 12,
                    "total_tokens_source": "reported",
                    "complete": True,
                },
            }
        )
        cache_path = grading_dir / "panel_votes.json"
        panel_grade.write_cache(cache_path, cache)
        verdicts = panel_grade.majority_verdicts(cache, required_votes=3)
        write_json(grading_dir / "panel_verdicts.json", verdicts)
        write_json(
            grading_dir / "panel_verdicts.manifest.json",
            {
                "cache_manifest": manifest,
                "cache_sha256": qt4_analysis.sha256_json(cache),
                "verdicts_sha256": qt4_analysis.sha256_json(verdicts),
                "verdict_count": len(verdicts),
                "panel_token_usage": panel_grade.panel_token_summary(cache),
            },
        )

    def assemble(
        self, synthetic: SyntheticQt4Run, grading_dir: Path
    ) -> dict:
        return qt4_analysis.assemble_result(
            controller_manifest=synthetic.controller,
            question_spec=synthetic.spec,
            input_path=synthetic.input,
            arms=synthetic.artifacts(),
            grading_dir=grading_dir,
            expected_question_count=2,
            n_boot=200,
        )

    def test_three_arm_grading_is_single_queue_and_panel_prompt_is_arm_blind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            grading_dir = root / "grading"

            manifest = self.prepare(synthetic, grading_dir)
            queue = panel_grade.load_queue(grading_dir / "panel_queue.jsonl")
            blinded = panel_grade.prepare_blinded_items(
                queue,
                panel_grade.build_judge_config(
                    model="gpt-5.6-sol",
                    effort="high",
                    batch_size=10,
                    votes=3,
                    timeout=600,
                    codex_bin="/test/codex",
                    codex_version="codex-test 1",
                ),
            )
            prompt = panel_grade.batch_prompt(blinded)

        self.assertEqual([item["arm"] for item in queue], list(ARMS))
        self.assertEqual({item["question_id"] for item in queue}, {"q2"})
        self.assertTrue(all(item["opaque_id"].startswith("panel_") for item in blinded))
        for hidden in (*ARMS, "q2"):
            self.assertNotIn(hidden, prompt)
        self.assertEqual(
            manifest["deterministic_grader_invocations"],
            {arm: 1 for arm in ARMS},
        )
        self.assertEqual(manifest["sealed_completion"]["accepted_answers"], 6)

    def test_result_orients_registered_contrasts_and_reports_economics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            grading_dir = root / "grading"
            self.prepare(synthetic, grading_dir)
            self.complete_panel(grading_dir)

            result = self.assemble(synthetic, grading_dir)
            first_bytes = (grading_dir / "final_result.json").read_bytes()
            result_again = self.assemble(synthetic, grading_dir)
            second_bytes = (grading_dir / "final_result.json").read_bytes()
            expected_packet_bytes = sum(
                qt4_analysis.canonical_model_packet_bytes(record["packet"])
                for record in qt4_analysis.load_packet_records(
                    synthetic.packets["a6a"], synthetic.qids
                ).values()
            )

        vocab = result["contrasts"]["qt4v_minus_a6a"]
        traversal = result["contrasts"]["qt4t_minus_qt4v"]
        self.assertEqual(vocab["treatment"], "qt4v")
        self.assertEqual(vocab["reference"], "a6a")
        self.assertEqual(vocab["accuracy_difference"], 1.0)
        self.assertEqual(vocab["discordant_treatment_only"], 2)
        self.assertEqual(traversal["accuracy_difference"], -0.5)
        self.assertEqual(traversal["discordant_reference_only"], 1)
        self.assertTrue(vocab["mcnemar"]["estimable"])

        a6 = result["economics"]["arms"]["a6a"]
        self.assertEqual(a6["tokens"]["input_tokens"]["total"], 300)
        self.assertEqual(a6["tokens"]["output_tokens"]["total"], 50)
        self.assertFalse(a6["tokens"]["cached_input_tokens"]["complete"])
        self.assertEqual(a6["tokens"]["cached_input_tokens"]["total"], 10)
        self.assertEqual(a6["wall_time_seconds"]["total"], 5.0)
        self.assertTrue(a6["attempts"]["retry_history"]["available"])
        self.assertEqual(a6["attempts"]["retry_history"]["count"], 0)
        self.assertEqual(a6["all_attempt_tokens"]["total_tokens"]["total"], 350)
        self.assertEqual(
            result["economics"]["contrasts"]["qt4v_minus_a6a"]["tokens"][
                "input_tokens"
            ]["difference"],
            100,
        )
        self.assertEqual(
            result["economics"]["panel_judging"]["accepted"]["tokens"][
                "total_tokens"
            ],
            12,
        )

        self.assertEqual(a6["model_visible_packet_bytes"]["total"], expected_packet_bytes)
        self.assertNotEqual(expected_packet_bytes, 999_999 * 2)
        self.assertEqual(
            result["mechanism_outcomes"]["traversal"]["target_outcomes"][
                "resource_capped"
            ],
            3,
        )
        self.assertTrue(
            result["promotion_assessment"]["contrasts"]["qt4v_minus_a6a"][
                "confirmatory_run_candidate"
            ]
        )
        self.assertFalse(
            result["promotion_assessment"]["contrasts"]["qt4v_minus_a6a"][
                "promoted"
            ]
        )
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(result, result_again)

    def test_incomplete_or_extra_outputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            (synthetic.run_dirs["qt4t"] / "questions" / "q2" / "completion.json").unlink()
            with self.assertRaisesRegex(ValueError, "exact sealed completion"):
                self.prepare(synthetic, root / "grading-missing")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            extra = synthetic.run_dirs["a6a"] / "questions" / "extra"
            extra.mkdir(parents=True)
            (extra / "answer.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unexpected question directories"):
                self.prepare(synthetic, root / "grading-extra")

    def test_incomplete_panel_votes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            grading_dir = root / "grading"
            self.prepare(synthetic, grading_dir)
            self.complete_panel(grading_dir)
            cache_path = grading_dir / "panel_votes.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            next(iter(cache["items"].values()))["votes"] = [True, True]
            write_json(cache_path, cache)

            with self.assertRaisesRegex(ValueError, "fully voted"):
                self.assemble(synthetic, grading_dir)

    def test_unregistered_self_consistent_panel_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            grading_dir = root / "grading"
            self.prepare(synthetic, grading_dir)
            self.complete_panel(grading_dir)
            cache_path = grading_dir / "panel_votes.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            config = cache["manifest"]["judge_config"]
            config["judge_protocol_version"] = "unregistered-protocol"
            config["judge_preamble_sha256"] = "0" * 64
            cache["manifest"]["judge_config_sha256"] = qt4_analysis.sha256_json(
                config
            )
            panel_grade.write_cache(cache_path, cache)

            with self.assertRaisesRegex(ValueError, "not the registered QT-4 config"):
                self.assemble(synthetic, grading_dir)

    def test_rehashed_prompt_with_different_packet_rendering_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            grading_dir = root / "grading"
            question_dir = synthetic.run_dirs["a6a"] / "questions" / "q1"
            prompt_path = question_dir / "prompt.txt"
            prompt_path.write_text(
                prompt_path.read_text(encoding="utf-8").replace(
                    "µ-positive", "tampered-packet-text"
                ),
                encoding="utf-8",
            )
            receipt_path = question_dir / "completion.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["prompt_sha256"] = qt4_analysis.sha256_file(prompt_path)
            write_json(receipt_path, receipt)

            with self.assertRaisesRegex(ValueError, "prompt does not match sealed"):
                self.prepare(synthetic, grading_dir)

    def test_append_only_retry_usage_is_validated_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = SyntheticQt4Run(root)
            question_dir = synthetic.run_dirs["a6a"] / "questions" / "q1"
            attempt_dir = question_dir / "attempts" / "attempt-0001"
            attempt_dir.mkdir(parents=True)
            failed_events = attempt_dir / "events.jsonl"
            failed_events.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 11,
                            "cached_input_tokens": 3,
                            "output_tokens": 4,
                            "reasoning_output_tokens": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            attempt_path = attempt_dir / "attempt.json"
            receipt = {
                "kind": "qt4_attempt_completion",
                "schema_version": "qt4-attempt-v2",
                "controller_manifest_sha256": synthetic.controller_sha,
                "arm": "a6a",
                "question_id": "q1",
                "packet_sha256": qt4_analysis.sha256_file(
                    synthetic.packets["a6a"]
                ),
                "schema_sha256": json.loads(
                    synthetic.controller.read_text(encoding="utf-8")
                )["snapshots"]["schema"]["sha256"],
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "attempt_number": 1,
                "harness_exit_code": 1,
                "returncode": 1,
                "status": "transient_failure",
                "event_integrity": codex_harness.audit_event_log(failed_events),
                "usage": {
                    "input_tokens": 11,
                    "cached_input_tokens": 3,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 1,
                },
                "archived_files": {
                    "events.jsonl": {
                        "path": str(failed_events),
                        "sha256": qt4_analysis.sha256_file(failed_events),
                    }
                },
                "attempt_receipt_path": str(attempt_path),
            }
            write_json(attempt_path, receipt)
            (question_dir / "attempts.jsonl").write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            completion_path = question_dir / "completion.json"
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            completion["attempt_number"] = 2
            write_json(completion_path, completion)

            grading_dir = root / "grading"
            self.prepare(synthetic, grading_dir)
            self.complete_panel(grading_dir)
            result = self.assemble(synthetic, grading_dir)

            receipt["usage"]["input_tokens"] = 999
            write_json(attempt_path, receipt)
            (question_dir / "attempts.jsonl").write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "archived attempt usage changed"):
                self.prepare(synthetic, root / "grading-tampered-retry")

        a6 = result["economics"]["arms"]["a6a"]
        self.assertEqual(a6["attempts"]["retry_history"]["count"], 1)
        self.assertEqual(a6["failed_attempt_tokens"]["input_tokens"]["total"], 11)
        self.assertEqual(a6["all_attempt_tokens"]["total_tokens"]["total"], 365)

    def test_only_explicit_tool_free_provider_failure_is_valid_retry_history(self):
        for tool_event, should_pass in ((False, True), (True, False)):
            with self.subTest(tool_event=tool_event), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                synthetic = SyntheticQt4Run(root)
                question_dir = synthetic.run_dirs["a6a"] / "questions" / "q1"
                attempt_dir = question_dir / "attempts" / "attempt-0001"
                attempt_dir.mkdir(parents=True)
                failed_events = attempt_dir / "events.jsonl"
                events = [
                    {"type": "thread.started", "thread_id": "thread-test"},
                    {"type": "turn.started"},
                    {"type": "error", "message": "usage limit"},
                ]
                if tool_event:
                    events.append(
                        {
                            "type": "item.completed",
                            "item": {"type": "command_execution"},
                        }
                    )
                events.append(
                    {
                        "type": "turn.failed",
                        "error": {"message": "usage limit"},
                    }
                )
                failed_events.write_text(
                    "\n".join(json.dumps(event) for event in events) + "\n",
                    encoding="utf-8",
                )
                audit = codex_harness.audit_event_log(failed_events)
                marker_path = attempt_dir / "contamination.json"
                write_json(marker_path, {**audit, "quarantine_path": None})
                attempt_path = attempt_dir / "attempt.json"
                controller = json.loads(
                    synthetic.controller.read_text(encoding="utf-8")
                )
                receipt = {
                    "kind": "qt4_attempt_completion",
                    "schema_version": "qt4-attempt-v2",
                    "controller_manifest_sha256": synthetic.controller_sha,
                    "arm": "a6a",
                    "question_id": "q1",
                    "packet_sha256": qt4_analysis.sha256_file(
                        synthetic.packets["a6a"]
                    ),
                    "schema_sha256": controller["snapshots"]["schema"]["sha256"],
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "attempt_number": 1,
                    "harness_exit_code": 1,
                    "returncode": 1,
                    "status": "transient_failure",
                    "event_integrity": audit,
                    "usage": {},
                    "answer_sha256": None,
                    "archived_files": {
                        "events.jsonl": {
                            "path": str(failed_events),
                            "sha256": qt4_analysis.sha256_file(failed_events),
                        },
                        "contamination.json": {
                            "path": str(marker_path),
                            "sha256": qt4_analysis.sha256_file(marker_path),
                        }
                    },
                    "attempt_receipt_path": str(attempt_path),
                }
                write_json(attempt_path, receipt)
                (question_dir / "attempts.jsonl").write_text(
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                completion_path = question_dir / "completion.json"
                completion = json.loads(completion_path.read_text(encoding="utf-8"))
                completion["attempt_number"] = 2
                write_json(completion_path, completion)

                if should_pass:
                    self.prepare(synthetic, root / "grading")
                    missing_marker = json.loads(json.dumps(receipt))
                    missing_marker["archived_files"].pop("contamination.json")
                    write_json(attempt_path, missing_marker)
                    (question_dir / "attempts.jsonl").write_text(
                        json.dumps(
                            missing_marker,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "retryable marker is missing",
                    ):
                        self.prepare(synthetic, root / "grading-missing-marker")

                    changed_marker = {
                        **audit,
                        "quarantine_path": "unexpected-answer-path",
                    }
                    write_json(marker_path, changed_marker)
                    receipt["archived_files"]["contamination.json"][
                        "sha256"
                    ] = qt4_analysis.sha256_file(marker_path)
                    write_json(attempt_path, receipt)
                    (question_dir / "attempts.jsonl").write_text(
                        json.dumps(receipt, ensure_ascii=False, sort_keys=True)
                        + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "retryable marker changed",
                    ):
                        self.prepare(synthetic, root / "grading-changed-marker")
                else:
                    with self.assertRaisesRegex(
                        ValueError,
                        "transient attempt was contaminated",
                    ):
                        self.prepare(synthetic, root / "grading")


if __name__ == "__main__":
    unittest.main()
