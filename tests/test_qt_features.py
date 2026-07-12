import unittest

import a6_packet_builder as a6


def _obs(i, day, code="Heart Rate", value=None, unit="bpm"):
    r = {
        "resourceType": "Observation",
        "id": f"o{i}",
        "code": {"text": code},
        "effectiveDateTime": f"2100-01-{day:02d}T00:00:00Z",
    }
    if value is not None:
        r["valueQuantity"] = {"value": value, "unit": unit}
    return r


class IncludePinningTests(unittest.TestCase):
    def test_evicted_medication_target_is_pinned(self):
        med = {"resourceType": "Medication", "id": "m1", "code": {"text": "Heparin"}}
        req = {
            "resourceType": "MedicationRequest",
            "id": "r1",
            "medicationReference": {"reference": "Medication/m1"},
            "authoredOn": "2100-02-01",
        }
        kept = [req]
        universe = [req, med]
        out, pinned = a6.pin_reference_targets(kept, universe)
        self.assertEqual(pinned, 1)
        self.assertTrue(any(r.get("id") == "m1" for r in out))

    def test_already_kept_and_unfetched_targets_not_duplicated(self):
        med = {"resourceType": "Medication", "id": "m1"}
        req = {"resourceType": "MedicationRequest", "id": "r1", "medicationReference": {"reference": "Medication/m1"}}
        req2 = {"resourceType": "MedicationRequest", "id": "r2", "medicationReference": {"reference": "Medication/ghost"}}
        out, pinned = a6.pin_reference_targets([req, med, req2], [req, med, req2])
        self.assertEqual(pinned, 0)
        self.assertEqual(len(out), 3)

    def test_non_pinnable_types_ignored(self):
        enc = {"resourceType": "Encounter", "id": "e1"}
        obs = {"resourceType": "Observation", "id": "o1", "encounter": {"reference": "Encounter/e1"}}
        out, pinned = a6.pin_reference_targets([obs], [obs, enc])
        self.assertEqual(pinned, 0)


class EndpointReserveTests(unittest.TestCase):
    def test_extremes_survive_budget_pressure_from_noisy_type(self):
        # one huge Observation each ~big chars + a tiny Encounter pair at extremes
        noisy = [dict(_obs(i, (i % 27) + 1), padding="x" * 800) for i in range(60)]
        enc_first = {"resourceType": "Encounter", "id": "e_first", "period": {"start": "2100-01-01"}}
        enc_last = {"resourceType": "Encounter", "id": "e_last", "period": {"start": "2100-12-31"}}
        resources = noisy + [enc_first, enc_last]
        kept, stats = a6.bound_resources(
            resources,
            temporal_policy="first_last",
            max_total_resources=200,
            max_packet_chars=9_000,
            endpoint_reserve=True,
        )
        ids = {r["id"] for r in kept}
        self.assertIn("e_first", ids)
        self.assertIn("e_last", ids)

    def test_off_by_default_behavior_unchanged(self):
        resources = [_obs(i, (i % 27) + 1) for i in range(30)]
        kept_off, _ = a6.bound_resources(
            resources, temporal_policy="recent", max_total_resources=10, max_packet_chars=1_000_000
        )
        self.assertEqual(len(kept_off), 10)


class AggSummaryTests(unittest.TestCase):
    def test_counts_extremes_and_unit_guarded_sum(self):
        resources = (
            [_obs(i, i + 1, code="Foley", value=100.0, unit="mL") for i in range(20)]
            + [_obs(100 + i, i + 1, code="Heart Rate", value=80 + i, unit="bpm") for i in range(5)]
        )
        s = a6.aggregate_summary(resources)
        foley = next(r for r in s["code_series"] if r["code"] == "foley")
        self.assertEqual(foley["resource_count"], 20)
        self.assertEqual(foley["value_sum"], 2000.0)
        self.assertEqual(foley["first"], "2100-01-01T00:00:00Z")
        self.assertEqual(foley["last"], "2100-01-20T00:00:00Z")

    def test_mixed_units_suppress_sum(self):
        resources = [
            _obs(1, 1, code="Weight", value=80.0, unit="kg"),
            _obs(2, 2, code="Weight", value=176.0, unit="lb"),
        ]
        s = a6.aggregate_summary(resources)
        w = next(r for r in s["code_series"] if r["code"] == "weight")
        self.assertNotIn("value_sum", w)

    def test_medication_distinct_semantics(self):
        med = {"resourceType": "Medication", "id": "m1", "code": {"text": "Heparin"}}
        reqs = [
            {"resourceType": "MedicationRequest", "id": f"r{i}", "medicationReference": {"reference": "Medication/m1"}, "authoredOn": f"2100-01-{i+1:02d}"}
            for i in range(3)
        ] + [
            {"resourceType": "MedicationRequest", "id": "r9", "medicationCodeableConcept": {"text": "Aspirin"}, "authoredOn": "2100-02-01"}
        ]
        s = a6.aggregate_summary(reqs + [med])
        self.assertEqual(s["medication_distinct_count"], 2)
        self.assertEqual(s["medication_displays"]["heparin"], 3)

    def test_char_cap_truncates(self):
        resources = [_obs(i, (i % 27) + 1, code=f"Lab {i}") for i in range(3000)]
        s = a6.aggregate_summary(resources, max_chars=4_000)
        self.assertTrue(s["truncated"])
        self.assertLessEqual(len(a6._json(s)), 4_200)


class FeatureIsolationTests(unittest.TestCase):
    ROW = {
        "question_id": "q1", "split": "test",
        "question": "what was the last heart rate value of patient 1?",
        "assumption": "", "patient_fhir_id": "p1",
    }

    def test_no_features_packet_matches_base_shape(self):
        rec = a6.build_packet_record(self.ROW, plan_only=True, resources_by_query={}, planner="question-only")
        self.assertEqual(rec["packet"]["features"], [])
        self.assertIsNone(rec["packet"]["aggregate_summary"])
        self.assertEqual(rec["packet"]["pinned_reference_targets"], 0)

    def test_features_recorded_in_packet(self):
        rec = a6.build_packet_record(
            self.ROW, plan_only=True, resources_by_query={}, planner="question-only",
            features={"agg-summary", "include-pinning"},
        )
        self.assertEqual(rec["packet"]["features"], ["agg-summary", "include-pinning"])


if __name__ == "__main__":
    unittest.main()
