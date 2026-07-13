import csv
import json
import tempfile
import unittest
from pathlib import Path

import qt4_packet_gate as gate


def _resource(resource_type: str, resource_id: str, **extra):
    return {"resourceType": resource_type, "id": resource_id, **extra}


def _micro_queries(*, patient="p1"):
    return [
        {
            "resource_type": "Observation",
            "path": f"Observation?patient={patient}&code:text={term.replace(' ', '%20')}",
            "reason": "fixed microbiology display vocabulary (micro-v1)",
            "relaxation_policy": "none",
        }
        for term in ("culture", "gram stain", "screen", "smear")
    ]


def _packet(
    resources,
    *,
    features=(),
    traversal=None,
    query="Observation?patient=p1",
    queries=None,
):
    traversed_count = (
        int(traversal["stats"]["added_resource_count"])
        if isinstance(traversal, dict)
        else 0
    )
    root_count = len(resources) - traversed_count
    packet = {
        "kind": "a6a_question_only_packet",
        "planner": "qo-v2",
        "features": sorted(features),
        "pinned_reference_targets": 0,
        "aggregate_summary": None,
        "plan_only": False,
        "resources": resources,
        "resource_count": len(resources),
        "source_resource_ids": sorted(
            f"{resource['resourceType']}/{resource['id']}" for resource in resources
        ),
        "source_queries": queries
        or [{"resource_type": query.split("?", 1)[0], "path": query}],
        "bounds": {"kept_count": root_count},
    }
    if traversal is not None:
        packet["reference_traversal"] = traversal
    packet["sha256"] = "fixture-sha"
    return packet


def _record(question_id: str, packet, *, question: str):
    return {
        "question_id": question_id,
        "question": question,
        "patient_fhir_id": "p1",
        "assumption": "",
        "packet": packet,
    }


def _traversal(appended_resource):
    receipts = [
        {
            "depth": 1,
            "from": "Observation/g1",
            "path": "Observation.hasMember[0].reference",
            "to": "Observation/g2",
            "status": "fetched",
        },
        {
            "depth": 2,
            "from": "Observation/g2",
            "path": "Observation.hasMember[0].reference",
            "to": "Observation/g1",
            "status": "already_present",
        },
    ]
    result = {
        "kind": "bounded_exact_reference_traversal",
        "version": "micro-traversal-v1",
        "limits": {
            "max_depth": 2,
            "max_resources": 24,
            "max_serialized_bytes": 24_000,
            "max_path_receipts": 48,
            "max_path_receipt_bytes": 12_000,
        },
        "stats": {
            "fetch_attempt_count": 1,
            "added_resource_count": 1,
            "added_serialized_bytes": gate._json_bytes(appended_resource),
            "path_receipt_count": 2,
            "path_receipt_serialized_bytes": gate._json_bytes(receipts),
            "path_receipts_omitted": 0,
            "path_status_counts": {
                "fetched": 1,
                "already_present": 1,
                "missing": 0,
                "max_resources": 0,
                "max_serialized_bytes": 0,
            },
        },
        "path_receipts": receipts,
    }
    return result


class Qt4PacketGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.spec_path = self.root / "questions.csv"
        with self.spec_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["question_id", "main_table_name", "true_fhir_ids"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "question_id": "q-micro",
                    "main_table_name": "microbiologyevents",
                    "true_fhir_ids": repr({"Observation": ["g1", "g2"]}),
                }
            )
            writer.writerow(
                {
                    "question_id": "q-other",
                    "main_table_name": "diagnoses_icd",
                    "true_fhir_ids": repr({"Condition": ["c1"]}),
                }
            )

        common = _record(
            "q-other",
            _packet([_resource("Condition", "c1")], query="Condition?patient=p1"),
            question="What diagnosis was recorded?",
        )
        self.a6_records = [
            _record(
                "q-micro",
                _packet([_resource("Observation", "noise")]),
                question="What organism was found in the culture?",
            ),
            common,
        ]
        appended = _resource(
            "Observation",
            "g2",
            valueString="E. coli",
            hasMember=[{"reference": "Observation/g1"}],
        )
        self.v_records = [
            _record(
                "q-micro",
                _packet(
                    [
                        _resource(
                            "Observation",
                            "g1",
                            hasMember=[{"reference": "Observation/g2"}],
                        )
                    ],
                    features=("micro-vocab",),
                    queries=_micro_queries(),
                ),
                question="What organism was found in the culture?",
            ),
            common,
        ]
        self.t_records = [
            _record(
                "q-micro",
                _packet(
                    [
                        _resource(
                            "Observation",
                            "g1",
                            hasMember=[{"reference": "Observation/g2"}],
                        ),
                        appended,
                    ],
                    features=("micro-vocab", "micro-traversal"),
                    traversal=_traversal(appended),
                    queries=_micro_queries(),
                ),
                question="What organism was found in the culture?",
            ),
            common,
        ]
        self.paths = {}
        for name, records in (
            ("a6a", self.a6_records),
            ("qt4v", self.v_records),
            ("qt4t", self.t_records),
        ):
            path = self.root / f"{name}.jsonl"
            path.write_text(
                "".join(
                    json.dumps(record, sort_keys=True) + "\n" for record in records
                ),
                encoding="utf-8",
            )
            self.paths[name] = path

    def tearDown(self):
        self.temp_dir.cleanup()

    def _report(self):
        return gate.compare_packet_files(
            a6a_path=self.paths["a6a"],
            qt4v_path=self.paths["qt4v"],
            qt4t_path=self.paths["qt4t"],
            question_spec_path=self.spec_path,
            expectations=gate.GateExpectations(
                expected_total=2,
                expected_micro=1,
                expected_non_micro=1,
                min_vocab_gold_gain=1,
                min_traversal_gold_gain=1,
            ),
        )

    def _write_arm(self, arm, records):
        self.paths[arm].write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )

    def _assert_failed(self, gate_name):
        report = self._report()
        self.assertFalse(report["passed"])
        self.assertIn(gate_name, report["failed_gates"])
        return report

    def test_reports_noop_recall_footprint_and_traversal_metrics(self):
        report = self._report()

        self.assertTrue(report["passed"])
        self.assertEqual(report["scheduled_question_count"], 2)
        self.assertEqual(report["scheduled_question_ids"], ["q-micro", "q-other"])
        self.assertEqual(report["dispatch"]["microbiology_questions"], 1)
        self.assertEqual(
            report["equivalence"]["non_micro_packet"], {"matched": 1, "total": 1}
        )
        self.assertEqual(
            report["equivalence"]["non_micro_prompt"], {"matched": 1, "total": 1}
        )

        overall = report["evaluation_only_gold_metrics"]["recall"]["overall"]
        self.assertEqual(overall["a6a"]["id_weighted_recall"], 1 / 3)
        self.assertEqual(overall["qt4v"]["id_weighted_recall"], 2 / 3)
        self.assertEqual(overall["qt4t"]["id_weighted_recall"], 1.0)
        micro = report["evaluation_only_gold_metrics"]["recall"]["microbiology"]
        self.assertEqual(micro["a6a"]["macro_recall"], 0.0)
        self.assertEqual(micro["qt4v"]["macro_recall"], 0.5)
        self.assertEqual(micro["qt4t"]["macro_recall"], 1.0)
        self.assertEqual(micro["a6a"]["any_coverage"], 0.0)
        self.assertEqual(micro["qt4v"]["any_coverage"], 1.0)
        self.assertEqual(micro["qt4v"]["all_coverage"], 0.0)
        self.assertEqual(micro["qt4t"]["all_coverage"], 1.0)
        self.assertEqual(
            report["evaluation_only_gold_metrics"]["traversal_gold_gain"][
                "gold_id_occurrences_gained"
            ],
            1,
        )

        traversal = report["traversal"]
        self.assertEqual(traversal["target_outcomes"]["fetched"], 1)
        self.assertEqual(traversal["target_outcomes"]["already_present"], 1)
        self.assertEqual(traversal["serialized_path_depth_counts"], {"1": 1, "2": 1})
        self.assertEqual(
            traversal["serialized_path_family_counts"]["Observation.hasMember"], 2
        )
        self.assertEqual(
            traversal["serialized_path_family_counts"]["DiagnosticReport.result"], 0
        )
        self.assertEqual(
            traversal["serialized_path_family_counts"]["DiagnosticReport.specimen"], 0
        )
        self.assertGreater(
            report["resource_footprint"]["arms"]["qt4t"]["resource_json_bytes"],
            report["resource_footprint"]["arms"]["qt4t"]["root_resource_json_bytes"],
        )
        self.assertFalse(report["query_fetch_audit"]["supported"])
        self.assertFalse(report["query_fetch_audit"]["hard_gate_applied"])
        self.assertIn(
            "do not preserve per-query fetch receipts",
            report["query_fetch_audit"]["limitation"],
        )

    def test_dispatch_is_recomputed_from_question_text_not_analysis_label(self):
        for arm, records in (
            ("a6a", self.a6_records),
            ("qt4v", self.v_records),
            ("qt4t", self.t_records),
        ):
            changed = json.loads(json.dumps(records))
            changed[1]["question"] = "Was a culture performed?"
            self._write_arm(arm, changed)

        report = self._assert_failed("micro_dispatch_v1_matches_analysis_stratum")
        self.assertEqual(report["dispatch"]["analysis_non_microbiology_dispatched"], 1)

    def test_nested_benchmark_answer_keys_are_rejected_without_copying_values(self):
        for forbidden_key in sorted(gate.FORBIDDEN_PACKET_KEYS):
            with self.subTest(forbidden_key=forbidden_key):
                changed = json.loads(json.dumps(self.a6_records))
                changed[0]["packet"]["resources"][0]["component"] = [
                    {forbidden_key: "must-not-appear"}
                ]
                self._write_arm("a6a", changed)

                report = self._assert_failed("packets_exclude_benchmark_answer_keys")
                rendered = gate.render_json(report)
                self.assertIn(
                    f"packet.resources[0].component[0].{forbidden_key}", rendered
                )
                self.assertNotIn("must-not-appear", rendered)

    def test_micro_query_term_order_is_hard_gated(self):
        changed = json.loads(json.dumps(self.t_records))
        queries = changed[0]["packet"]["source_queries"]
        queries[0], queries[1] = queries[1], queries[0]
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_micro_v1_observation_query_union")

    def test_micro_query_patient_date_sort_params_must_match_a6a(self):
        for arm, records in (("qt4v", self.v_records), ("qt4t", self.t_records)):
            changed = json.loads(json.dumps(records))
            for query in changed[0]["packet"]["source_queries"]:
                query["path"] = query["path"].replace("patient=p1", "patient=p2")
            self._write_arm(arm, changed)

        self._assert_failed("qt4v_micro_v1_observation_query_union")
        self._assert_failed("qt4t_micro_v1_observation_query_union")

    def test_micro_query_relaxation_is_hard_gated(self):
        changed = json.loads(json.dumps(self.v_records))
        changed[0]["packet"]["source_queries"][0]["relaxation_policy"] = "fallback"
        changed[0]["packet"]["source_queries"][1]["relaxation_attempts"] = [
            "Observation?patient=p1"
        ]
        self._write_arm("qt4v", changed)

        self._assert_failed("qt4v_micro_v1_observation_query_union")

    def test_traversal_root_prefix_is_hard_gated(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["resources"][0]["status"] = "tampered"
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_resource_shape")

    def test_traversal_appended_targets_must_be_sorted_and_deduplicated(self):
        changed = json.loads(json.dumps(self.t_records))
        packet = changed[0]["packet"]
        extra = _resource("Observation", "a", valueString="extra")
        packet["resources"].append(extra)
        packet["resource_count"] = len(packet["resources"])
        packet["source_resource_ids"] = sorted(
            f"{resource['resourceType']}/{resource['id']}"
            for resource in packet["resources"]
        )
        stats = packet["reference_traversal"]["stats"]
        stats["fetch_attempt_count"] = 2
        stats["added_resource_count"] = 2
        stats["added_serialized_bytes"] += gate._json_bytes(extra)
        stats["path_receipts_omitted"] = 1
        stats["path_status_counts"]["fetched"] = 2
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_resource_shape")

        changed = json.loads(json.dumps(self.t_records))
        packet = changed[0]["packet"]
        packet["resources"].append(packet["resources"][1])
        self._write_arm("qt4t", changed)
        self._assert_failed("qt4t_traversal_resource_shape")

    def test_traversal_added_byte_stat_is_recomputed_from_resources(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["reference_traversal"]["stats"][
            "added_serialized_bytes"
        ] += 1
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_resource_shape")

    def test_traversal_receipt_counts_bytes_and_statuses_are_recomputed(self):
        changed = json.loads(json.dumps(self.t_records))
        stats = changed[0]["packet"]["reference_traversal"]["stats"]
        stats["path_receipt_count"] += 1
        stats["path_receipt_serialized_bytes"] += 1
        stats["path_status_counts"]["missing"] += 1
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_stats_consistency")

    def test_malformed_traversal_stats_fail_closed_instead_of_crashing_report(self):
        changed = json.loads(json.dumps(self.t_records))
        stats = changed[0]["packet"]["reference_traversal"]["stats"]
        stats["fetch_attempt_count"] = "twenty-four"
        stats["path_status_counts"]["fetched"] = "one"
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_stats_consistency")

    def test_traversal_fetch_attempt_limit_is_hard_gated(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["reference_traversal"]["stats"]["fetch_attempt_count"] = 25
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_actual_limits")

    def test_traversal_added_evidence_byte_limit_is_hard_gated(self):
        changed = json.loads(json.dumps(self.t_records))
        packet = changed[0]["packet"]
        extra = _resource("Observation", "z", valueString="x" * 24_001)
        packet["resources"].append(extra)
        packet["resource_count"] = len(packet["resources"])
        packet["source_resource_ids"] = sorted(
            f"{resource['resourceType']}/{resource['id']}"
            for resource in packet["resources"]
        )
        stats = packet["reference_traversal"]["stats"]
        stats["fetch_attempt_count"] = 2
        stats["added_resource_count"] = 2
        stats["added_serialized_bytes"] += gate._json_bytes(extra)
        stats["path_receipts_omitted"] = 1
        stats["path_status_counts"]["fetched"] = 2
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_actual_limits")

    def test_traversal_receipt_count_limit_is_hard_gated(self):
        changed = json.loads(json.dumps(self.t_records))
        traversal = changed[0]["packet"]["reference_traversal"]
        traversal["path_receipts"].extend(
            [json.loads(json.dumps(traversal["path_receipts"][1])) for _ in range(47)]
        )
        stats = traversal["stats"]
        stats["path_receipt_count"] = len(traversal["path_receipts"])
        stats["path_receipt_serialized_bytes"] = gate._json_bytes(
            traversal["path_receipts"]
        )
        stats["path_status_counts"]["already_present"] = 48
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_actual_limits")

    def test_traversal_receipt_byte_limit_is_recomputed_from_actual_array(self):
        source_id = "s" * 64
        references = [f"Observation/{index:02d}{'x' * 62}" for index in range(48)]
        root = _resource(
            "Observation",
            source_id,
            hasMember=[{"reference": target} for target in references],
        )
        receipts = [
            {
                "depth": 2,
                "from": f"Observation/{source_id}",
                "path": f"Observation.hasMember[{index}].reference",
                "to": target,
                "status": "max_serialized_bytes" if index < 24 else "max_resources",
            }
            for index, target in enumerate(references)
        ]
        self.assertGreater(gate._json_bytes(receipts), 12_000)

        v_changed = json.loads(json.dumps(self.v_records))
        v_changed[0]["packet"]["resources"] = [root]
        v_changed[0]["packet"]["resource_count"] = 1
        v_changed[0]["packet"]["source_resource_ids"] = [f"Observation/{source_id}"]
        self._write_arm("qt4v", v_changed)

        t_changed = json.loads(json.dumps(self.t_records))
        packet = t_changed[0]["packet"]
        packet["resources"] = [root]
        packet["resource_count"] = 1
        packet["source_resource_ids"] = [f"Observation/{source_id}"]
        traversal = packet["reference_traversal"]
        traversal["path_receipts"] = receipts
        traversal["stats"] = {
            "fetch_attempt_count": 24,
            "added_resource_count": 0,
            "added_serialized_bytes": 0,
            "path_receipt_count": 48,
            "path_receipt_serialized_bytes": gate._json_bytes(receipts),
            "path_receipts_omitted": 0,
            "path_status_counts": {
                "fetched": 0,
                "already_present": 0,
                "missing": 0,
                "max_resources": 24,
                "max_serialized_bytes": 24,
            },
        }
        self._write_arm("qt4t", t_changed)

        self._assert_failed("qt4t_traversal_actual_limits")

    def test_traversal_depth_limit_is_hard_gated(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["reference_traversal"]["path_receipts"][1]["depth"] = 3
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_actual_limits")

    def test_traversal_receipts_must_point_to_allowlisted_actual_reference(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["reference_traversal"]["path_receipts"][0]["path"] = (
            "Observation.specimen.reference"
        )
        self._write_arm("qt4t", changed)

        self._assert_failed("qt4t_traversal_receipt_integrity")

    def test_json_and_text_rendering_are_deterministic_and_explicitly_label_gold(self):
        report = self._report()

        self.assertEqual(gate.render_json(report), gate.render_json(report))
        text = gate.render_text(report)
        self.assertIn("PASS", text)
        self.assertIn("EVALUATION-ONLY gold metrics", text)
        self.assertIn("non-micro packet no-op: 1/1", text)
        self.assertIn("DiagnosticReport.result=0", text)

    def test_non_micro_packet_change_fails_required_gate(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[1]["packet"]["resources"][0]["clinicalStatus"] = {"text": "active"}
        self.paths["qt4t"].write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in changed),
            encoding="utf-8",
        )

        report = self._report()

        self.assertFalse(report["passed"])
        self.assertIn("non_micro_packet_equivalence", report["failed_gates"])

    def test_non_frozen_traversal_contract_fails_required_gate(self):
        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["reference_traversal"]["limits"]["max_resources"] = 25
        self.paths["qt4t"].write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in changed),
            encoding="utf-8",
        )

        report = self._report()

        self.assertFalse(report["passed"])
        self.assertIn("qt4t_frozen_traversal_contract", report["failed_gates"])

        changed = json.loads(json.dumps(self.t_records))
        changed[0]["packet"]["reference_traversal"]["kind"] = "generic_graph_walk"
        self._write_arm("qt4t", changed)
        self._assert_failed("qt4t_frozen_traversal_contract")

    def test_cli_writes_both_outputs_and_returns_nonzero_on_gate_failure(self):
        json_out = self.root / "gate.json"
        text_out = self.root / "gate.txt"
        exit_code = gate.main(
            [
                "--a6a",
                str(self.paths["a6a"]),
                "--qt4v",
                str(self.paths["qt4v"]),
                "--qt4t",
                str(self.paths["qt4t"]),
                "--question-spec",
                str(self.spec_path),
                "--json-out",
                str(json_out),
                "--text-out",
                str(text_out),
                "--expected-total",
                "2",
                "--expected-micro",
                "1",
                "--expected-non-micro",
                "1",
                "--quiet",
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(json.loads(json_out.read_text(encoding="utf-8"))["passed"])
        self.assertIn("PASS", text_out.read_text(encoding="utf-8"))

        changed = json.loads(json.dumps(self.t_records))
        changed[1]["packet"]["resources"] = []
        self.paths["qt4t"].write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in changed),
            encoding="utf-8",
        )
        self.assertEqual(
            gate.main(
                [
                    "--a6a",
                    str(self.paths["a6a"]),
                    "--qt4v",
                    str(self.paths["qt4v"]),
                    "--qt4t",
                    str(self.paths["qt4t"]),
                    "--question-spec",
                    str(self.spec_path),
                    "--json-out",
                    str(json_out),
                    "--text-out",
                    str(text_out),
                    "--expected-total",
                    "2",
                    "--expected-micro",
                    "1",
                    "--expected-non-micro",
                    "1",
                    "--quiet",
                ]
            ),
            1,
        )

    def test_duplicate_packet_question_id_is_rejected(self):
        self.paths["a6a"].write_text(
            json.dumps(self.a6_records[0])
            + "\n"
            + json.dumps(self.a6_records[0])
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(gate.GateInputError, "duplicate question_id"):
            self._report()


if __name__ == "__main__":
    unittest.main()
