import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from azureUtils.routes import azureJobEndpoints


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"


class ExternalInterestWorkflowTests(unittest.TestCase):
    def test_find_out_prepares_one_contact_ready_interest_batch(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("Determine interest for selected", html)
        self.assertIn("Select no more than ten candidates for the interest check", html)
        self.assertIn("Every selected candidate must have an email, phone number, or LinkedIn profile", html)
        self.assertIn("determineInterestBatch:", html)
        self.assertIn("determine-interest.html?domain=", html)
        self.assertNotIn("Interested — enter process", html)

    def test_determine_interest_page_shows_batch_contacts_and_gates_profile_build(self):
        html = (PAGES / "determine-interest.html").read_text(encoding="utf-8")

        self.assertIn("3A - Interest confirmed", html)
        self.assertIn('contactChannel("Email"', html)
        self.assertIn('contactChannel("Phone"', html)
        self.assertIn('contactChannel("LinkedIn"', html)
        self.assertIn("Mark awaiting reply", html)
        self.assertIn("Mark Interested", html)
        self.assertIn("Mark Not Interested", html)
        self.assertIn("Continue to Profile Build", html)
        self.assertIn('/interest`,', html)
        self.assertIn('profile.interestStatus !== "interested"', html)
        self.assertIn("Active job", html)
        self.assertIn("Qualification brief:", html)
        self.assertIn("interest-match-score", html)
        self.assertIn("window.DevReadyProfessionalMatch.view(profile, currentJobId())", html)
        self.assertIn("window.DevReadyProfessionalMatch.details(fit.match)", html)
        self.assertIn("fit.match.matched", html)
        self.assertIn("fit.match.coveragePercent", html)
        self.assertIn("Review criterion evidence and unknowns", html)
        self.assertNotIn("Number(profile.matchScore)", html)
        self.assertNotIn("needs completed professional enrichment", html)
        self.assertIn("/api/azureJobs/getJob/", html)
        self.assertIn("calculateMissingMatches", html)
        self.assertIn("/calculate-match`,", html)
        self.assertIn("without provider credits", html)

    def test_temp_profiles_page_records_interest_before_using_candidate(self):
        html = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")

        self.assertIn("Determine interest", html)
        self.assertIn("determine-interest.html?domain=", html)
        self.assertIn('profile.interestStatus !== "interested"', html)
        self.assertIn('workflowStatus: "Interested - Profile Build next"', html)

    def test_shared_flow_places_interest_between_find_out_and_profile_build(self):
        flow = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertLess(flow.index('data-flow-step="find-out"'), flow.index('data-flow-step="interest"'))
        self.assertLess(flow.index('data-flow-step="interest"'), flow.index('data-flow-step="profile"'))
        self.assertIn('"find-out": "interest"', flow)
        self.assertIn('interest: "profile"', flow)

    def test_profile_header_exposes_contact_channels_and_time_is_hired_only(self):
        profile = (PAGES / "profile-preview.html").read_text(encoding="utf-8")
        time_admin = (PAGES / "time-admin.html").read_text(encoding="utf-8")

        self.assertIn('id="profileContactStrip"', profile)
        self.assertIn("renderProfileHeaderContacts(profileData)", profile)
        self.assertIn('item("Email"', profile)
        self.assertIn('item("Phone"', profile)
        self.assertIn('item("LinkedIn"', profile)
        self.assertNotIn("Create this candidate's weekly time link", profile)
        self.assertNotIn("createProfileTimeLink", profile)
        self.assertIn("Onboarded people", time_admin)
        self.assertIn("Send or resend their time-entry link from here", time_admin)

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
