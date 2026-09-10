import copy
import unittest
from unittest.mock import patch

from azureUtils.routes import azureJobEndpoints as routes


class ExternalMatchEnrichmentContextTests(unittest.TestCase):
    def setUp(self):
        self.prior_match = {
            "status": "calculated", "score": 90, "jobId": "job-1", "coveragePercent": 100,
            "evidenceType": "structured_professional_profile", "evidenceFingerprint": "old",
            "criteriaSnapshot": {"ignoredCriteria": ["skills", "cities"], "minYears": 5,
                                 "titles": ["Engineer"], "unexpectedSecret": "must-not-copy"},
        }
        self.profile_url = "https://www.linkedin.com/in/sample-developer"
        self.stored = {
            "personid": 7, "name": "Sample Developer", "title": "Engineer", "profileUrl": self.profile_url,
            "externalProfile": {"source": "People Data Labs", "sourceId": "provider-7",
                                "match": copy.deepcopy(self.prior_match), "enrichment": {"status": "not_requested"},
                                "professionalEvidenceSnapshot": {"title": "Old role", "skills": ["Old technology"]}},
        }

    def assert_pending(self, match):
        self.assertIsNone(match["score"])
        self.assertEqual(match["status"], "not_run")
        self.assertEqual(match["jobId"], "job-1")
        self.assertEqual(match["criteriaSnapshot"]["ignoredCriteria"], ["skills", "cities"])
        for stale_field in ("evidenceType", "evidenceFingerprint", "coveragePercent"):
            self.assertNotIn(stale_field, match)
        self.assertNotIn("unexpectedSecret", match["criteriaSnapshot"])

    def test_pending_keeps_only_job_and_allowlisted_criteria_not_stale_score(self):
        pending = routes._pending_external_match("Recalculate", self.prior_match)
        self.assert_pending(pending)
        pending["criteriaSnapshot"]["ignoredCriteria"].append("titles")
        self.assertEqual(self.prior_match["criteriaSnapshot"]["ignoredCriteria"], ["skills", "cities"])

    def test_pending_without_prior_match_has_no_fabricated_context(self):
        pending = routes._pending_external_match()
        self.assertEqual(pending["jobId"], "")
        self.assertEqual(pending["criteriaSnapshot"], {})

    def import_with_context(self, **payload):
        candidate = {"source": "pdl", "source_id": "provider-7", "name": "Sample Developer",
                     "title": "Engineer", "skills": ["Python"], "profile_url": self.profile_url,
                     "match": copy.deepcopy(self.prior_match)}
        with patch.object(routes.jobs, "getJob", return_value={"jd_id": payload.get("jd_id", "job-1"), "title": "Engineer", "skills": []}), \
             patch.object(routes.externalPeopleSearch, "getPeopleSkills", side_effect=AssertionError("No skill generation")) as generate, \
             patch.object(routes.peopleDataLabs, "enrichPerson", side_effect=AssertionError("No provider call")) as enrich, \
             patch.object(routes.candidates, "findTemporaryExternalProfile", return_value=None), \
             patch.object(routes.candidates, "uploadProfile", return_value={"status": "success", "personid": 7}) as upload:
            result = routes.external_candidate_import({"domain": "dev", "jd_id": "job-1", "candidate": candidate, **payload})
            _, metadata = routes.candidates.splitExternalProfileDescription(upload.call_args.kwargs["candidateDescription"])
        generate.assert_not_called()
        enrich.assert_not_called()
        self.assertEqual(result["providerCreditsUsed"], 0)
        return metadata

    @patch.object(routes.candidates, "saveTemporaryExternalProfileMatch", return_value={"status": "success"})
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes.jobs, "getJob", return_value={"jd_id": "job-1", "title": "Engineer", "skills": []})
    def test_import_empty_jd_skills_is_no_spend_and_ignore_choices_reach_saved_calculation(self, get_job, get_temp, save):
        metadata = self.import_with_context(criteria={"ignoredCriteria": ["skills", "cities"], "titles": ["Engineer"]})
        self.assertIsNone(metadata["match"]["score"])
        self.assertEqual(metadata["match"]["jobId"], "job-1")
        self.assertNotIn("evidenceType", metadata["match"])
        get_temp.return_value = {"personid": 7, "externalProfile": metadata}
        result = routes.external_candidate_calculate_temp_match("7", {"jd_id": "job-1"})
        self.assertEqual(result["match"]["criteriaSnapshot"]["ignoredCriteria"], ["skills", "cities"])
        self.assertEqual(save.call_args.args[2]["criteriaSnapshot"]["ignoredCriteria"], ["skills", "cities"])

    def test_import_falls_back_only_to_same_job_match_criteria_and_explicit_payload_wins(self):
        same = self.import_with_context()
        self.assert_pending(same["match"])
        other = self.import_with_context(jd_id="job-2")
        self.assertEqual(other["match"]["jobId"], "job-2")
        self.assertEqual(other["match"]["criteriaSnapshot"], {})
        explicit = self.import_with_context(criteria={"ignoredCriteria": ["licenses"]})
        self.assertEqual(explicit["match"]["criteriaSnapshot"], {"ignoredCriteria": ["licenses"]})
        cleared = self.import_with_context(criteria={})
        self.assertEqual(cleared["match"]["criteriaSnapshot"], {})

    @patch.object(routes.candidates, "saveTemporaryExternalProfileMatch", return_value={"status": "success"})
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes, "_calculate_external_evidence_match")
    def test_recalculate_reuses_criteria_only_for_same_job_and_when_not_explicit(self, calculate, get_temp, save):
        self.stored["externalProfile"]["match"] = routes._pending_external_match("Recalculate", self.prior_match)
        get_temp.return_value = self.stored
        calculate.return_value = {"status": "unavailable", "score": None}
        routes.external_candidate_calculate_temp_match("7", {"jd_id": "job-1"})
        self.assertEqual(calculate.call_args.args[3]["ignoredCriteria"], ["skills", "cities"])
        routes.external_candidate_calculate_temp_match("7", {"jd_id": "job-2"})
        self.assertIsNone(calculate.call_args.args[3])
        routes.external_candidate_calculate_temp_match("7", {"jd_id": "job-1", "criteria": {}})
        self.assertEqual(calculate.call_args.args[3], {})

    @patch.object(routes.candidates, "applyTemporaryExternalProfileEnrichment", return_value={"status": "success"})
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes.peopleDataLabs, "enrichPerson")
    @patch.object(routes.coreSignal, "configured", return_value=False)
    def test_pdl_enrichment_keeps_criteria_and_updates_original_evidence_snapshot(self, configured, enrich, get_temp, apply):
        get_temp.return_value = self.stored
        enrich.return_value = {"status": 200, "likelihood": 9, "data": {
            "id": "provider-7", "first_name": "Sample", "last_name": "Developer",
            "job_title": "Engineer", "skills": ["Python"], "linkedin_url": self.profile_url,
        }}
        result = routes.external_candidate_enrich_temp_profile("7", {"domain": "dev"})
        self.assert_pending(result["match"])
        metadata = apply.call_args.args[3]
        self.assertEqual(metadata["professionalEvidenceSnapshot"]["skills"], ["Python"])
        self.assertNotEqual(metadata["professionalEvidenceSnapshot"], self.stored["externalProfile"]["professionalEvidenceSnapshot"])

    @patch.object(routes.candidates, "applyTemporaryExternalProfileEnrichment", return_value={"status": "success"})
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes.peopleDataLabs, "enrichPerson", side_effect=AssertionError("No PDL request"))
    @patch.object(routes.coreSignal, "configured", return_value=True)
    @patch.object(routes.coreSignal, "enrichment_dataset", return_value="base")
    @patch.object(routes.coreSignal, "collect_person", return_value={"status": 200, "data": {}, "dataset": "base", "credits_used": 1})
    @patch.object(routes, "_coresignal_collected_row")
    def test_coresignal_enrichment_keeps_criteria_and_updates_evidence(self, mapped, collect, dataset, configured, pdl, get_temp, apply):
        self.stored["externalProfile"]["source"] = "Coresignal"
        get_temp.return_value = self.stored
        mapped.return_value = {"name": "Sample Developer", "title": "Engineer", "skills": ["Python"], "profile_url": self.profile_url}
        result = routes.external_candidate_enrich_temp_profile("7", {"domain": "dev"})
        self.assert_pending(result["match"])
        self.assertEqual(apply.call_args.args[3]["professionalEvidenceSnapshot"]["skills"], ["Python"])
        pdl.assert_not_called()

    @patch.object(routes.candidates, "applyTemporaryExternalProfileEnrichment")
    @patch.object(routes.candidates, "getTemporaryExternalProfileForEnrichment")
    @patch.object(routes.peopleDataLabs, "enrichPerson", side_effect=AssertionError("No provider request"))
    def test_reused_enrichment_preserves_pending_context_without_request_or_write(self, enrich, get_temp, apply):
        self.stored["externalProfile"]["match"] = routes._pending_external_match("Recalculate", self.prior_match)
        self.stored["externalProfile"]["enrichment"] = {"status": "completed", "profileVersion": 2, "provider": "People Data Labs Person Enrichment"}
        get_temp.return_value = self.stored
        result = routes.external_candidate_enrich_temp_profile("7", {"domain": "dev"})
        self.assertTrue(result["reused"])
        self.assert_pending(result["match"])
        enrich.assert_not_called()
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
