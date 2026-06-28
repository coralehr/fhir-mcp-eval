import unittest

import a7_packet_builder as a7


class A7PacketBuilderTests(unittest.TestCase):
    def test_extracts_references_recursively_without_patient_boundary_noise(self):
        resource = {
            "resourceType": "MedicationRequest",
            "id": "mr1",
            "subject": {"reference": "Patient/p1"},
            "medicationReference": {"reference": "Medication/med1"},
            "encounter": {"reference": "Encounter/e1"},
            "performer": [{"actor": {"reference": "Practitioner/pr1"}}],
        }

        refs = a7.extract_references([resource])

        self.assertEqual(refs, ["Encounter/e1", "Medication/med1", "Practitioner/pr1"])

    def test_builds_complete_packet_with_references_codes_and_citations(self):
        row = {
            "question_id": "q1",
            "question": "What medication was prescribed?",
            "patient_fhir_id": "p1",
            "main_table_name": "prescriptions",
            "val_dict": "{'val_placeholder': {'drug_name': 'glucagon'}, 'op_placeholder': {}, 'time_placeholder': {}}",
        }
        medication_request = {
            "resourceType": "MedicationRequest",
            "id": "mr1",
            "medicationReference": {"reference": "Medication/med1"},
            "code": {
                "coding": [{"system": "http://example.test", "code": "MR", "display": "request"}],
                "text": "request",
            },
        }
        medication = {
            "resourceType": "Medication",
            "id": "med1",
            "code": {
                "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "4832", "display": "Glucagon"}],
                "text": "Glucagon",
            },
        }

        record = a7.build_packet_record(
            row,
            plan_only=False,
            resources_by_query={"MedicationRequest?patient=p1&_count=100&_sort=-date&_include=MedicationRequest:medication": [medication_request]},
            referenced_resources_by_id={"Medication/med1": medication},
        )

        packet = record["packet"]
        self.assertEqual(packet["kind"], "a7_bonfire_complete_packet")
        self.assertEqual(packet["source_resource_ids"], ["Medication/med1", "MedicationRequest/mr1"])
        self.assertEqual(packet["reference_resolution"]["resolved"], ["Medication/med1"])
        self.assertTrue(any(c["resource_id"] == "Medication/med1" for c in packet["citations"]))
        self.assertTrue(any(code["display"] == "Glucagon" for code in packet["terminology"]))
        self.assertIsNone(packet["insufficiency"])

    def test_marks_insufficiency_when_live_packet_has_no_resources(self):
        row = {
            "question_id": "q2",
            "question": "Did this patient have a procedure?",
            "patient_fhir_id": "p2",
            "main_table_name": "procedures_icd",
            "true_answer": "do-not-leak",
            "true_fhir_ids": "{'Procedure': ['secret']}",
        }

        record = a7.build_packet_record(row, plan_only=False, resources_by_query={}, referenced_resources_by_id={})

        dumped = str(record)
        self.assertNotIn("do-not-leak", dumped)
        self.assertNotIn("secret", dumped)
        self.assertEqual(record["packet"]["insufficiency"]["reason"], "no_resources_returned")


if __name__ == "__main__":
    unittest.main()
