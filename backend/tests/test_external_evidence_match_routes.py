import unittest
from unittest.mock import patch

from fastapi import HTTPException

from azureUtils.routes import azureJobEndpoints as routes


class ExternalEvidenceMatchRouteTests(unittest.TestCase):
    def setUp(self):
        self.jd = {"jd_id": "job-local", "title": "Software Engineer", "skills": ["Python", "AWS"]}
        self.candidate = {"source": "pdl", "source_label": "People Data Labs", "title": "Software Engineer", "skills": ["Python"]}

    @patch.object(routes.peopleDataLabs, "enrichPerson", side_effect=AssertionError("No enrichment allowed"))
    @patch.object(routes.externalPeopleSearch, "getPeopleSkills", side_effect=AssertionError("No AI call allowed"))
    @patch.object(routes.jobs, "getJob")
    def test_unsaved_discovery_match_is_available_before_contact_enrichment(self, get_job, ai_call, enrich):
        get_job.return_value = self.jd
        response = routes.external_candidate_calculate_discovery_match({
            "domain": "technology", "jd_id": "job-local", "candidate": self.candidate,
            "criteria": {"ignoredCriteria": ["titles"]},
        })
        get_job.assert_called_once_with("job-local", "dev")
        self.assertEqual(response["match"]["score"], 50)
        self.assertEqual(response["providerCreditsUsed"], 0)
        self.assertFalse(response["providerContacted"])
        ai_call.assert_not_called()
        enrich.assert_not_called()

    @patch.object(routes.externalPeopleSearch, "getPeopleSkills", side_effect=AssertionError("No AI call allowed"))
    @patch.object(routes.jobs, "getJob")
    def test_missing_jd_skills_does_not_trigger_paid_fallback(self, get_job, ai_call):
        get_job.return_value = {"jd_id": "job-local", "title": "Engineer", "skills": []}
        routes.external_candidate_calculate_discovery_match({"jd_id": "job-local", "candidate": self.candidate})
        ai_call.assert_not_called()

    def test_missing_job_or_candidate_is_rejected(self):
        with self.assertRaises(HTTPException):
            routes.external_candidate_calculate_discovery_match({"jd_id": "job-local"})
        with self.assertRaises(HTTPException):
            routes.external_candidate_calculate_discovery_match({"candidate": self.candidate})

    @patch.object(routes.candidates, "saveTemporaryExternalProfileMatch")
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes.jobs, "getJob")
    def test_saved_professional_snapshot_does_not_need_enrichment(self, get_job, get_temp, save):
        get_job.return_value = self.jd
        get_temp.return_value = {"personid": 7, "externalProfile": {
            "professionalEvidenceSnapshot": routes._external_match_snapshot(self.candidate),
            "enrichment": {"status": "not_requested"},
        }}
        save.return_value = {"status": "success", "personid": 7}
        response = routes.external_candidate_calculate_temp_match("7", {"jd_id": "job-local"})
        self.assertEqual(response["match"]["status"], "calculated")
        self.assertIn("Python", save.call_args.args[2]["matched"])
        self.assertFalse(response["providerContacted"])

    @patch.object(routes.candidates, "saveTemporaryExternalProfileMatch")
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes.jobs, "getJob")
    def test_contacts_only_persist_unavailable_to_invalidate_old_score_not_fake_zero(self, get_job, get_temp, save):
        get_job.return_value = self.jd
        get_temp.return_value = {"personid": 7, "name": "Python AWS", "email": "p@example.test", "externalProfile": {"providerSkills": ["Python", "AWS"]}}
        save.return_value = {"status": "success", "personid": 7}
        response = routes.external_candidate_calculate_temp_match("7", {"jd_id": "job-local"})
        self.assertIsNone(response["match"]["score"])
        self.assertIsNone(save.call_args.args[2]["score"])
        self.assertEqual(save.call_args.args[2]["status"], "unavailable")

    def test_snapshot_preserves_source_facts_without_contacts_or_derived_matches(self):
        candidate = dict(self.candidate, email="private@example.test", phone="555-0100", top_matches=["AWS"])
        snapshot = routes._external_match_snapshot(candidate)
        self.assertEqual(snapshot["skills"], ["Python"])
        self.assertNotIn("email", snapshot)
        self.assertNotIn("phone", snapshot)
        self.assertNotIn("top_matches", snapshot)
        self.assertEqual(routes._external_candidate_skills(candidate), ["Python"])

    def test_legacy_provider_skills_are_not_reused_but_true_narrative_is(self):
        adapted = routes._stored_external_match_candidate({
            "title": "Software Engineer", "description": "Temporary generated wrapper with AWS",
            "externalProfile": {"providerSkills": ["AWS"], "professionalEvidence": ["Built services using Python"]},
        })
        self.assertEqual(adapted["skills"], [])
        self.assertIn("Python", adapted["summary"])
        self.assertNotIn("AWS", adapted["summary"])

    @patch.object(routes.jobs, "getJob")
    @patch.object(routes.externalSearchHistory, "get_search_group")
    def test_saved_search_refreshes_evidence_match_against_changed_job_without_spend(self, get_group, get_job):
        get_group.return_value = {"pages": [{
            "query": {"jdId": "job-local"},
            "response": {"source": "pdl", "jd": {"jd_id": "job-local"}, "results": [dict(self.candidate, score=100)],
                         "criteria": {"ignoredCriteria": ["titles"]}},
        }]}
        get_job.return_value = self.jd
        first = routes.external_candidate_open_saved_search("saved-1", "dev")
        self.assertEqual(first["results"][0]["score"], 50)
        get_job.return_value = dict(self.jd, skills=["Python"])
        second = routes.external_candidate_open_saved_search("saved-1", "dev")
        self.assertEqual(second["results"][0]["score"], 100)
        self.assertNotEqual(first["results"][0]["match"]["evidenceFingerprint"], second["results"][0]["match"]["evidenceFingerprint"])
        self.assertFalse(second["sourceAudit"]["queryExecuted"])

    @patch.object(routes.jobs, "getJob", return_value=None)
    @patch.object(routes.externalSearchHistory, "get_search_group")
    def test_deleted_job_keeps_search_readable_but_removes_old_percentage(self, get_group, get_job):
        get_group.return_value = {"pages": [{
            "query": {"jdId": "job-local"},
            "response": {"source": "pdl", "results": [dict(self.candidate, score=100)]},
        }]}
        result = routes.external_candidate_open_saved_search("saved-1", "dev")
        self.assertIsNone(result["results"][0]["score"])
        self.assertFalse(result["matchRefreshedFromCurrentJob"])


if __name__ == "__main__":
    unittest.main()
