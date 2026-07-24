import copy
import unittest

import c3g_preflight


SHA = "a" * 64


def receipt():
    return {"status": "pass", "path": "receipts/x.json", "sha256": SHA}


def bundle():
    required = {name: receipt() for name in c3g_preflight.REQUIRED_RECEIPTS}
    return {
        "schema_version": c3g_preflight.SCHEMA_VERSION,
        "state": "SEALED",
        "replicates": 3,
        "holdout": {"questions": 405, "patient_clusters": 72},
        "common_answer_contract": {
            "answer_model": "gpt-5.6-sol",
            "answer_model_family": "gpt",
            "reasoning_effort": "high",
            "base_prompt_sha256": SHA,
            "search_craft_sha256": SHA,
            "semantic_empty_retry": 1,
            "model_round_budget": 8,
            "fhir_request_budget": 12,
            "fetcher_sha256": SHA,
            "answer_schema_sha256": SHA,
            "truncation_policy_sha256": SHA,
            "timeout_policy_sha256": SHA,
            "operational_retry_policy_sha256": SHA,
        },
        "arms": {
            "G0": {"graph_packet": None},
            "C1": {"graph_packet": None},
            "C2": {"graph_packet": None},
            "C3": {"graph_packet": None},
            "C3G": {
                "graph_packet": {
                    "compiler_sha256": SHA,
                    "config_sha256": SHA,
                    "packet_cap_tokens": 4096,
                }
            },
            "RAW": {"graph_packet": None},
        },
        "receipts": required,
    }


class C3GPreflightTests(unittest.TestCase):
    def test_complete_bundle_is_launch_ready(self) -> None:
        report = c3g_preflight.audit_bundle(bundle())
        self.assertTrue(report["launch_ready"])
        self.assertEqual(report["failed_gates"], [])

    def test_missing_receipt_fails_closed(self) -> None:
        value = bundle()
        del value["receipts"]["judge_calibration"]
        report = c3g_preflight.audit_bundle(value)
        self.assertFalse(report["launch_ready"])
        self.assertIn("receipt:judge_calibration", report["failed_gates"])
        with self.assertRaisesRegex(ValueError, "not launch-ready"):
            c3g_preflight.assert_launch_ready(value)

    def test_arm_override_cannot_smuggle_a_second_treatment_difference(self) -> None:
        value = bundle()
        value["arms"]["C3G"]["reasoning_effort"] = "xhigh"
        with self.assertRaisesRegex(ValueError, "forbidden arm-specific fields"):
            c3g_preflight.audit_bundle(value)

    def test_c3g_requires_graph_packet_and_c3_forbids_it(self) -> None:
        missing = bundle()
        missing["arms"]["C3G"]["graph_packet"] = None
        with self.assertRaisesRegex(ValueError, "C3G requires"):
            c3g_preflight.audit_bundle(missing)

        leaked = bundle()
        leaked["arms"]["C3"]["graph_packet"] = copy.deepcopy(
            leaked["arms"]["C3G"]["graph_packet"]
        )
        with self.assertRaisesRegex(ValueError, "C3 must not"):
            c3g_preflight.audit_bundle(leaked)

    def test_requires_three_replicates_and_at_least_40_patients(self) -> None:
        value = bundle()
        value["replicates"] = 1
        with self.assertRaisesRegex(ValueError, "exactly three"):
            c3g_preflight.audit_bundle(value)
        value = bundle()
        value["holdout"]["patient_clusters"] = 39
        with self.assertRaisesRegex(ValueError, "at least 40"):
            c3g_preflight.audit_bundle(value)

    def test_nonsealed_or_failed_receipt_is_not_ready(self) -> None:
        value = bundle()
        value["state"] = "DRAFT"
        self.assertFalse(c3g_preflight.audit_bundle(value)["launch_ready"])
        value = bundle()
        value["receipts"]["power"]["status"] = "fail"
        report = c3g_preflight.audit_bundle(value)
        self.assertIn("receipt:power", report["failed_gates"])


if __name__ == "__main__":
    unittest.main()
