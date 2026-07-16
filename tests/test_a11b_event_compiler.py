from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

import a11_answer_harness
from a11_evidence_core import canonical_bytes
from a11b_event_compiler import (
    ARM_E1,
    ARM_T0,
    ARM_T1,
    A11B_QUESTION_PLAN_VERSION,
    compile_arms,
    plan_question,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "a11b_event_compiler_dev.json"
)
GOLD = FIXTURE.with_name("a11b_event_compiler_dev_gold.json")
MAX_PACKET_BYTES = 1_000_000


def _load_cases() -> list[dict]:
    fixture = json.loads(FIXTURE.read_text())
    gold = json.loads(GOLD.read_text())
    if fixture != json.loads(json.dumps(fixture, sort_keys=True)):
        raise AssertionError("fixture is not JSON-round-trip stable")
    if fixture["schema_version"] != "a11b-event-compiler-dev-fixture-v1":
        raise AssertionError("unexpected fixture schema")
    if fixture["synthetic_non_phi"] is not True:
        raise AssertionError("dev fixture must be explicitly synthetic")
    if gold["schema_version"] != "a11b-event-compiler-dev-gold-v1":
        raise AssertionError("unexpected gold schema")
    gold_by_id = {case["case_id"]: case for case in gold["cases"]}
    if set(gold_by_id) != {case["case_id"] for case in fixture["cases"]}:
        raise AssertionError("source and audit gold case ids differ")
    return [{**case, **gold_by_id[case["case_id"]]} for case in fixture["cases"]]


def _step(source: str, pointer: str, target: str | None) -> dict:
    return {
        "source": source,
        "json_pointer": pointer,
        "target": target,
        "target_type": "Observation",
    }


def _source_packet(case: dict) -> dict:
    resources = []
    citations = []
    root_refs = []
    for root_spec in case["roots"]:
        root_ref = f"Observation/{root_spec['id']}"
        intermediate_ref = f"Observation/intermediate-{root_spec['id']}"
        terminal_ref = f"Observation/terminal-{root_spec['id']}"
        root = {
            "resourceType": "Observation",
            "id": root_spec["id"],
            "subject": {"reference": "Patient/synthetic-a11b"},
            "hasMember": [{"reference": intermediate_ref}],
        }
        for field in ("effectiveDateTime", "effectivePeriod", "issued"):
            if field in root_spec:
                root[field] = root_spec[field]
        intermediate = {
            "resourceType": "Observation",
            "id": f"intermediate-{root_spec['id']}",
            "subject": {"reference": "Patient/synthetic-a11b"},
            "hasMember": [{"reference": terminal_ref}],
        }
        if not root_spec["path_complete"]:
            intermediate["hasMember"] = [{"display": "Reference withheld"}]
        terminal = {
            "resourceType": "Observation",
            "id": f"terminal-{root_spec['id']}",
            "subject": {"reference": "Patient/synthetic-a11b"},
            "valueString": f"synthetic-fact-{root_spec['id']}",
        }
        resources.extend((root, intermediate))
        if root_spec["path_complete"]:
            resources.append(terminal)
        root_refs.append(root_ref)
        citations.append(
            {
                "state": "available",
                "target": intermediate_ref,
                "target_type": "Observation",
                "steps": [_step(root_ref, "/hasMember/0", intermediate_ref)],
            }
        )
        citations.append(
            {
                "state": (
                    "available" if root_spec["path_complete"] else "unavailable"
                ),
                "target": terminal_ref if root_spec["path_complete"] else None,
                "target_type": "Observation",
                "steps": [
                    _step(root_ref, "/hasMember/0", intermediate_ref),
                    _step(
                        intermediate_ref,
                        "/hasMember/0",
                        terminal_ref if root_spec["path_complete"] else None,
                    ),
                ],
            }
        )
    return {
        "resources": resources,
        "path_citations": citations,
        "root_refs": root_refs,
        "bounds": {"outcomes": []},
    }


def _question(case: dict) -> str:
    return f"What organism was found in the {case['temporal_policy']} culture?"


def _plan(case: dict) -> dict:
    plan = plan_question(_question(case))
    if plan["version"] != A11B_QUESTION_PLAN_VERSION:
        raise AssertionError("unexpected A11b plan version")
    return plan


class A11bEventCompilerTests(unittest.TestCase):
    def test_registered_arms_share_evidence_and_isolate_features(self) -> None:
        case = _load_cases()[0]
        compiled = compile_arms(
            _source_packet(case),
            _question(case),
            _plan(case),
            max_packet_bytes=MAX_PACKET_BYTES,
        )

        self.assertEqual(set(compiled["arms"]), {ARM_T0, ARM_T1, ARM_E1})
        evidence = compiled["arms"][ARM_T0]["evidence"]
        for arm in (ARM_T1, ARM_E1):
            self.assertEqual(compiled["arms"][arm]["evidence"], evidence)
        self.assertNotIn("temporal_aids", compiled["arms"][ARM_T0])
        self.assertNotIn("event_groups", compiled["arms"][ARM_T0])
        self.assertIn("temporal_aids", compiled["arms"][ARM_T1])
        self.assertNotIn("event_groups", compiled["arms"][ARM_T1])
        self.assertEqual(
            compiled["arms"][ARM_T1]["temporal_aids"],
            compiled["arms"][ARM_E1]["temporal_aids"],
        )
        self.assertEqual(len(compiled["arms"][ARM_E1]["event_groups"]), 3)
        self.assertEqual(
            {
                key: value
                for key, value in compiled["arms"][ARM_T1].items()
                if key != "temporal_aids"
            },
            compiled["arms"][ARM_T0],
        )
        self.assertEqual(
            {
                key: value
                for key, value in compiled["arms"][ARM_E1].items()
                if key != "event_groups"
            },
            compiled["arms"][ARM_T1],
        )

        receipt = compiled["equivalence_receipt"]
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(
            receipt["evidence_sha256"],
            hashlib.sha256(canonical_bytes(evidence)).hexdigest(),
        )
        self.assertEqual(
            len(set(receipt["arm_evidence_sha256"].values())),
            1,
        )
        self.assertEqual(
            len(set(receipt["arm_path_citations_sha256"].values())),
            1,
        )

        aids = compiled["arms"][ARM_T1]["temporal_aids"]
        selected = [event for event in aids["events"] if event["selected"]]
        self.assertEqual([event["root_ref"] for event in selected], [case["expected_selected_root"]])
        self.assertEqual(aids["answerability_receipt"]["state"], "sufficient")
        for payload in compiled["arms"].values():
            a11_answer_harness._reject_payload_leakage(payload)

    def test_ambiguous_and_invalid_times_never_select_by_resource_id(self) -> None:
        for case in _load_cases()[1:-1]:
            with self.subTest(case_id=case["case_id"]):
                compiled = compile_arms(
                    _source_packet(case),
                    _question(case),
                    _plan(case),
                    max_packet_bytes=MAX_PACKET_BYTES,
                )
                aids = compiled["arms"][ARM_T1]["temporal_aids"]
                receipt = aids["answerability_receipt"]
                self.assertEqual(receipt["state"], case["expected_state"])
                self.assertEqual(receipt["reason"], case["expected_reason"])
                self.assertFalse(any(event["selected"] for event in aids["events"]))

    def test_incomplete_selected_path_abstains_despite_complete_distractors(self) -> None:
        case = _load_cases()[-1]
        compiled = compile_arms(
            _source_packet(case),
            _question(case),
            _plan(case),
            max_packet_bytes=MAX_PACKET_BYTES,
        )
        aids = compiled["arms"][ARM_T1]["temporal_aids"]
        receipt = aids["answerability_receipt"]

        self.assertEqual(receipt["state"], "insufficient")
        self.assertEqual(receipt["reason"], "selected_path_incomplete")
        self.assertEqual(receipt["selected_event_count"], 1)
        selected = [event for event in aids["events"] if event["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertFalse(all(item["satisfied"] for item in selected[0]["requirements"]))
        self.assertTrue(
            all(
                all(item["satisfied"] for item in event["requirements"])
                for event in aids["events"]
                if not event["selected"]
            )
        )

    def test_compilation_is_order_invariant(self) -> None:
        case = _load_cases()[0]
        source = _source_packet(case)
        reversed_source = copy.deepcopy(source)
        for key in ("resources", "path_citations", "root_refs"):
            reversed_source[key].reverse()
        self.assertEqual(
            canonical_bytes(
                compile_arms(
                    source,
                    _question(case),
                    _plan(case),
                    max_packet_bytes=MAX_PACKET_BYTES,
                )
            ),
            canonical_bytes(
                compile_arms(
                    reversed_source,
                    _question(case),
                    _plan(case),
                    max_packet_bytes=MAX_PACKET_BYTES,
                )
            ),
        )

    def test_clinical_tnm_stage_is_not_mistaken_for_an_arm_label(self) -> None:
        case = _load_cases()[0]
        source = _source_packet(case)
        source["resources"][0]["valueString"] = "T1"

        compiled = compile_arms(
            source,
            _question(case),
            _plan(case),
            max_packet_bytes=MAX_PACKET_BYTES,
        )

        self.assertEqual(set(compiled["arms"]), {ARM_T0, ARM_T1, ARM_E1})

    def test_gold_and_arm_derived_inputs_fail_closed(self) -> None:
        case = _load_cases()[0]
        source = _source_packet(case)
        plan = _plan(case)
        for target, key in (
            (source, "gold_answer"),
            (source["bounds"], "answerable"),
            (plan, "expected_selected_root"),
            (plan, "arm"),
            (plan, "failure_mode"),
        ):
            with self.subTest(key=key):
                hostile_source = copy.deepcopy(source)
                hostile_plan = copy.deepcopy(plan)
                if target is source:
                    hostile_source[key] = "forbidden"
                elif target is source["bounds"]:
                    hostile_source["bounds"][key] = "forbidden"
                else:
                    hostile_plan[key] = "forbidden"
                with self.assertRaisesRegex(ValueError, "forbidden compiler input"):
                    compile_arms(
                        hostile_source,
                        _question(case),
                        hostile_plan,
                        max_packet_bytes=MAX_PACKET_BYTES,
                    )

        nested_arm = copy.deepcopy(source)
        nested_arm["resources"][0]["meta"] = {"opaque": ARM_T0}
        with self.assertRaisesRegex(ValueError, "forbidden compiler input"):
            compile_arms(
                nested_arm,
                _question(case),
                plan,
                max_packet_bytes=MAX_PACKET_BYTES,
            )

        non_json_container = copy.deepcopy(source)
        non_json_container["resources"][0]["meta"] = (
            {"gold_answer": "forbidden"},
        )
        with self.assertRaisesRegex(ValueError, "forbidden compiler input"):
            compile_arms(
                non_json_container,
                _question(case),
                plan,
                max_packet_bytes=MAX_PACKET_BYTES,
            )

        audit_leak = copy.deepcopy(source)
        audit_leak["resources"][0]["auditPathCitations"] = ["LEAK-AUDIT-ONLY"]
        with self.assertRaisesRegex(ValueError, "forbidden compiler input"):
            compile_arms(
                audit_leak,
                _question(case),
                plan,
                max_packet_bytes=MAX_PACKET_BYTES,
            )
        for namespace in ("audit", "checker", "expected", "gold", "governed", "true"):
            namespace_leak = copy.deepcopy(source)
            namespace_leak["resources"][0][namespace] = {"secret": "LEAK"}
            with self.subTest(namespace=namespace), self.assertRaisesRegex(
                ValueError, "forbidden compiler input"
            ):
                compile_arms(
                    namespace_leak,
                    _question(case),
                    plan,
                    max_packet_bytes=MAX_PACKET_BYTES,
                )

    def test_all_arm_packet_bounds_fail_together(self) -> None:
        case = _load_cases()[0]
        with self.assertRaisesRegex(ValueError, "registered packet bound exceeded"):
            compile_arms(
                _source_packet(case),
                _question(case),
                _plan(case),
                max_packet_bytes=64,
            )

    def test_plan_is_rederived_from_the_raw_question(self) -> None:
        case = _load_cases()[0]
        hostile_plan = _plan(case)
        hostile_plan["temporal_policy"] = "first"
        with self.assertRaisesRegex(ValueError, "does not match the raw question"):
            compile_arms(
                _source_packet(case),
                _question(case),
                hostile_plan,
                max_packet_bytes=MAX_PACKET_BYTES,
            )

    def test_nonselected_tie_does_not_force_false_abstention(self) -> None:
        case = copy.deepcopy(_load_cases()[1])
        case["temporal_policy"] = "latest"
        compiled = compile_arms(
            _source_packet(case),
            _question(case),
            _plan(case),
            max_packet_bytes=MAX_PACKET_BYTES,
        )
        aids = compiled["arms"][ARM_T1]["temporal_aids"]
        selected = [event for event in aids["events"] if event["selected"]]
        self.assertEqual(
            [event["root_ref"] for event in selected], ["Observation/root-c"]
        )
        self.assertEqual(
            aids["answerability_receipt"]["reason"],
            "requirements_satisfied",
        )

    def test_available_label_cannot_mask_a_broken_or_unreplayable_path(self) -> None:
        case = _load_cases()[0]
        for mutation in ("broken_chain", "invalid_pointer"):
            source = _source_packet(case)
            selected = case["expected_selected_root"]
            selected_citation = next(
                citation
                for citation in source["path_citations"]
                if len(citation["steps"]) == 2
                and citation["steps"][0]["source"] == selected
            )
            if mutation == "broken_chain":
                selected_citation["steps"][1]["source"] = (
                    "Observation/unrelated"
                )
            else:
                selected_citation["steps"][0]["json_pointer"] = (
                    "/hasMember/999"
                )
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                ValueError, "governed traversal"
            ):
                compile_arms(
                    source,
                    _question(case),
                    _plan(case),
                    max_packet_bytes=MAX_PACKET_BYTES,
                )

    def test_unavailable_reference_is_redacted_in_every_arm(self) -> None:
        case = _load_cases()[-1]
        source = _source_packet(case)
        compiled = compile_arms(
            source,
            _question(case),
            _plan(case),
            max_packet_bytes=MAX_PACKET_BYTES,
        )
        for arm, payload in compiled["arms"].items():
            serialized = canonical_bytes(payload)
            with self.subTest(arm=arm):
                self.assertNotIn(b"terminal-root-c", serialized)
                self.assertIn(b"Reference withheld", serialized)

        unredacted = copy.deepcopy(source)
        intermediate = next(
            resource
            for resource in unredacted["resources"]
            if resource.get("id") == "intermediate-root-c"
        )
        intermediate["hasMember"] = [
            {"reference": "Observation/terminal-root-c"}
        ]
        with self.assertRaisesRegex(ValueError, "is not redacted"):
            compile_arms(
                unredacted,
                _question(case),
                _plan(case),
                max_packet_bytes=MAX_PACKET_BYTES,
            )

    def test_out_of_range_fhir_instants_never_select(self) -> None:
        case = _load_cases()[0]
        for hostile in (
            "2100-01-01T00:00:00+14:99",
            "2100-01-01T00:00:00+23:59",
            "2100-01-01T24:00:00Z",
        ):
            with self.subTest(hostile=hostile):
                source = _source_packet(case)
                source["resources"][0]["effectiveDateTime"] = hostile
                compiled = compile_arms(
                    source,
                    _question(case),
                    _plan(case),
                    max_packet_bytes=MAX_PACKET_BYTES,
                )
                aids = compiled["arms"][ARM_T1]["temporal_aids"]
                self.assertEqual(
                    aids["answerability_receipt"]["reason"],
                    "invalid_clinical_time",
                )
                self.assertFalse(
                    any(event["selected"] for event in aids["events"])
                )


if __name__ == "__main__":
    unittest.main()
