import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from azureUtils.routes import azureJobEndpoints


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"


class ExternalInterestWorkflowTests(unittest.TestCase):
    def test_find_out_page_keeps_candidates_outside_process_until_interest_is_confirmed(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("Determine interest", html)
        self.assertIn("Interested — enter process", html)
        self.assertIn("Not interested", html)
        self.assertIn("options.addToProcess === true", html)
        self.assertIn('stage: "3* - Interest confirmed"', html)
        self.assertIn("Only confirmed interested candidates enter the active process", html)

    def test_temp_profiles_page_records_interest_before_using_candidate(self):
        html = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")

        self.assertIn("Confirm interest & use in process", html)
        self.assertIn("has personally said they are interested", html)
        self.assertIn("/interest`,", html)
        self.assertIn('workflowStatus: "Interested - Profile Build next"', html)

    def test_external_profile_metadata_keeps_confirmed_interest_evidence(self):
        metadata = azureJobEndpoints._external_profile_metadata(
            {
                "name": "Sample Candidate",
                "source_id": "sample-1",
                "interest_workflow": {
                    "status": "interested",
                    "jobId": "85",
                    "startedAt": "2026-09-09T12:00:00Z",
                    "confirmedAt": "2026-09-09T12:30:00Z",
                    "updatedAt": "2026-09-09T12:30:00Z",
                },
            },
            "pdl",
            {"status": "completed"},
        )

        self.assertEqual(metadata["interestWorkflow"]["status"], "interested")
        self.assertEqual(metadata["interestWorkflow"]["jobId"], "85")
        self.assertEqual(
            metadata["interestWorkflow"]["source"],
            "Recruiter-confirmed candidate response",
        )

    @patch.object(azureJobEndpoints.candidates, "updateTemporaryExternalProfileInterest")
    def test_interest_endpoint_saves_domain_scoped_status(self, update_interest):
        update_interest.return_value = {"status": "success", "personid": 42}

        response = azureJobEndpoints.external_candidate_update_temp_interest(
            "42",
            {
                "domain": "law",
                "status": "interested",
                "job_id": "85",
                "started_at": "2026-09-09T12:00:00Z",
                "confirmed_at": "2026-09-09T12:30:00Z",
            },
        )

        self.assertEqual(response["personid"], 42)
        domain = update_interest.call_args.args[1]
        workflow = update_interest.call_args.args[2]
        self.assertEqual(domain, "law")
        self.assertEqual(workflow["status"], "interested")
        self.assertEqual(workflow["jobId"], "85")

    def test_interest_endpoint_rejects_unknown_status(self):
        with self.assertRaises(HTTPException) as raised:
            azureJobEndpoints.external_candidate_update_temp_interest(
                "42",
                {"domain": "dev", "status": "maybe"},
            )

        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
