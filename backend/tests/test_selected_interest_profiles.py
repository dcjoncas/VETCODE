import unittest
from unittest.mock import call, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from azureUtils.routes import azureJobEndpoints as routes


class SelectedInterestProfileTests(unittest.TestCase):
    def setUp(self):
        self.list_patch = patch.object(routes.candidates, "listTemporaryExternalProfiles")
        self.get_patch = patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
        self.list_profiles = self.list_patch.start()
        self.get_profile = self.get_patch.start()
        self.addCleanup(self.list_patch.stop)
        self.addCleanup(self.get_patch.stop)
        self.get_profile.side_effect = lambda person_id, domain: self.profile(int(person_id))

    @staticmethod
    def profile(person_id, match=None):
        return {
            "personid": person_id, "name": f"Candidate {person_id}", "email": "",
            "title": "Software Engineer", "profileUrl": "https://www.linkedin.com/in/example-test",
            "location": {"locality": "Denver", "region": "Colorado", "country": "United States"},
            "externalProfile": {
                "source": "People Data Labs", "sourceId": f"test-{person_id}",
                "contact": {"primaryEmail": "candidate@example.test", "primaryPhone": "+1 303 555 0100"},
                "enrichment": {"provider": "People Data Labs", "status": "completed"},
                "interestWorkflow": {"status": "contacting", "jobId": "85", "confirmedAt": ""},
                "match": match or {"status": "calculated", "score": 80, "jobId": "85",
                    "evidenceType": "structured_professional_profile", "matched": ["Python"],
                    "criteriaSnapshot": {"ignoredCriteria": ["cities"]}},
            },
        }

    def test_unselected_list_keeps_existing_behavior_and_limit(self):
        self.list_profiles.return_value = {"status": "success", "profiles": []}
        result = routes.external_candidate_temp_profiles("technology", 73)
        self.assertIs(result, self.list_profiles.return_value)
        self.list_profiles.assert_called_once_with("dev", 73)
        self.get_profile.assert_not_called()

    def test_selected_batch_loads_older_ids_directly_in_input_order(self):
        result = routes.external_candidate_temp_profiles("law", 1, "902, 12,004,12")
        self.assertEqual([item["personid"] for item in result["profiles"]], [902, 12, 4])
        self.assertEqual(result["requestedPersonIds"], ["902", "12", "4"])
        self.assertEqual(result["missingPersonIds"], [])
        self.get_profile.assert_has_calls([call("902", "law"), call("12", "law"), call("4", "law")])
        self.assertEqual(self.get_profile.call_count, 3)
        self.list_profiles.assert_not_called()

    def test_all_ten_unique_ids_are_supported(self):
        result = routes.external_candidate_temp_profiles(person_ids=",".join(str(value) for value in range(1, 11)))
        self.assertEqual(len(result["profiles"]), 10)

    def test_saved_contacts_enrichment_interest_and_match_evidence_survive(self):
        result = routes.external_candidate_temp_profiles(person_ids="12")["profiles"][0]
        self.assertEqual(result["name"], "Candidate 12")
        self.assertEqual(result["email"], "candidate@example.test")
        self.assertEqual(result["phone"], "+1 303 555 0100")
        self.assertEqual(result["profileUrl"], "https://www.linkedin.com/in/example-test")
        self.assertEqual(result["location"], "Denver, Colorado, United States")
        self.assertTrue(result["linkedInEnriched"])
        self.assertEqual(result["interestStatus"], "contacting")
        self.assertEqual(result["interestJobId"], "85")
        self.assertEqual(result["match"], self.profile(12)["externalProfile"]["match"])
        self.assertEqual(result["matchScore"], 80)
        self.assertEqual(result["matchMatched"], ["Python"])
        self.assertNotIn("externalProfile", result)

    def test_unassessable_match_retains_null_and_current_job_metadata(self):
        match = {"status": "unavailable", "score": None, "jobId": "85",
                 "reason": "No professional evidence", "criteriaSnapshot": {"ignoreAll": True}}
        self.get_profile.side_effect = None
        self.get_profile.return_value = self.profile(7, match)
        result = routes.external_candidate_temp_profiles(person_ids="7")["profiles"][0]
        self.assertEqual(result["match"], match)
        self.assertIsNone(result["matchScore"])
        self.assertFalse(result["matchCalculated"])

    def test_deleted_other_domain_and_promoted_ids_are_not_replaced_by_unselected_people(self):
        def load(person_id, domain):
            if person_id == "2":
                raise HTTPException(404, "Temporary profile not found in this environment.")
            if person_id == "3":
                raise HTTPException(400, "Profile is already permanent.")
            return self.profile(int(person_id))
        self.get_profile.side_effect = load
        result = routes.external_candidate_temp_profiles("dental", person_ids="1,2,3,4")
        self.assertEqual([row["personid"] for row in result["profiles"]], [1, 4])
        self.assertEqual(result["missingPersonIds"], ["2", "3"])
        self.assertEqual(result["requestedPersonIds"], ["1", "2", "3", "4"])
        self.assertTrue(all(args.args[1] == "dental" for args in self.get_profile.call_args_list))
        self.list_profiles.assert_not_called()

    def test_invalid_selection_is_rejected_before_any_storage_access(self):
        for invalid in ("", " ", "1,", ",1", "1,,2", "0", "-1", "1.5", "1e3", "1; DROP TABLE person", "١", "9223372036854775808", "1" * 1025, ",".join(str(value) for value in range(1, 12))):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HTTPException) as raised:
                    routes.external_candidate_temp_profiles(person_ids=invalid)
                self.assertEqual(raised.exception.status_code, 400)
        self.get_profile.assert_not_called()
        self.list_profiles.assert_not_called()

    def test_storage_and_authorization_errors_are_not_hidden_as_empty_batches(self):
        for status in (400, 401, 403, 500, 503):
            with self.subTest(status=status):
                error = HTTPException(status, "Storage or authorization failure")
                self.get_profile.side_effect = error
                with self.assertRaises(HTTPException) as raised:
                    routes.external_candidate_temp_profiles(person_ids="12")
                self.assertIs(raised.exception, error)
        self.list_profiles.assert_not_called()

    def test_http_query_reads_selection_and_normalizes_domain_without_paid_calls(self):
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes.peopleDataLabs, "enrichPerson") as enrich, patch.object(routes.candidates, "saveTemporaryExternalProfileMatch") as save:
            response = TestClient(app).get("/api/azureJobs/external/temp?domain=engineering&person_ids=42,43")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["personid"] for row in response.json()["profiles"]], [42, 43])
        self.get_profile.assert_has_calls([call("42", "engineer"), call("43", "engineer")])
        enrich.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
