import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
PAGES = BACKEND / "ui" / "pages"


class TempProfilesWorkflowTests(unittest.TestCase):
    def test_temp_inventory_has_its_own_domain_aware_page(self):
        html = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")

        self.assertIn("/api/azureJobs/external/temp?domain=", html)
        self.assertIn("Use in current process", html)
        self.assertIn("Make permanent", html)
        self.assertIn("Delete", html)
        self.assertIn("LinkedIn-enriched TEMP results", html)
        self.assertIn("linkedin-results/export?domain=", html)
        self.assertIn("data-atlas-client-context", html)
        self.assertIn("JS/atlasClientContext.js", html)

    def test_find_out_does_not_embed_or_auto_load_saved_temp_inventory(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertNotIn('id="tempProfiles"', html)
        self.assertNotIn("function loadTempProfiles", html)
        self.assertIn('href="temp-profiles.html"', html)
        self.assertIn('id="latestTempProfilePanel"', html)

    def test_imported_result_becomes_an_open_devready_profile_action(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("function markCandidateDevReadyProfile", html)
        self.assertIn("devready_profile_complete: true", html)
        self.assertIn("DevReady TEMP profile complete", html)
        self.assertIn("Open DevReady Profile", html)
        self.assertIn("Profile complete", html)
        self.assertIn("enriched_match_complete", html)
        self.assertIn("Calculate JD match", html)
        self.assertIn("match_pending", html)

    def test_external_results_and_temp_profiles_are_ranked_by_current_fit(self):
        find_out = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")
        temp_profiles = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")

        self.assertIn("function rankExternalResults", find_out)
        self.assertIn("latestExternalResults = rankExternalResults", find_out)
        self.assertIn("discovery preview", find_out)
        self.assertIn("saved JD match", find_out)
        self.assertIn("profileMatchesCurrentJob(left)", temp_profiles)
        self.assertIn("safeRightScore - safeLeftScore", temp_profiles)
        self.assertIn("LinkedIn link stored", temp_profiles)

    def test_external_profile_actions_name_the_actual_destination(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("function candidateExternalProfileLabel", html)
        self.assertIn("Open GitHub Profile", html)
        self.assertIn("Open LinkedIn Profile", html)
        self.assertIn("Open CourtListener Record", html)
        self.assertNotIn('>Open profile link<', html)

    def test_legal_verification_uses_jurisdiction_and_provider_admissions(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("function lawVerificationTarget", html)
        self.assertIn("LAWYER_LICENSING_DIRECTORY_URL", html)
        self.assertIn("Provider-reported admission or license", html)
        self.assertIn("function candidateLegalCredentials", html)
        self.assertIn("target jurisdiction", html)
        self.assertIn("License or certification by name", html)
        self.assertIn("Non-negotiable candidate search criteria", html)
        self.assertIn('id="criteriaMustHaveSkills"', html)
        self.assertIn('id="criteriaWorkArrangement"', html)
        self.assertIn('id="criteriaWorkforceLocation"', html)
        self.assertIn('id="searchCriteriaPanel"', html)
        self.assertIn('formData.append("required_skills"', html)
        self.assertIn("Complete the non-negotiable candidate search criteria", html)

    def test_courtlistener_and_professional_sources_share_combined_profile_evidence(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")
        routes = (BACKEND / "azureUtils" / "routes" / "azureJobEndpoints.py").read_text(encoding="utf-8")

        self.assertIn("Create combined TEMP profile", html)
        self.assertIn("Professional data added", html)
        self.assertIn("includedInCandidateProfile", routes)
        self.assertIn('"match_pending"] = True', routes)
        self.assertIn('"usedForCandidateScoring": False', routes)

    def test_stored_scores_are_only_current_for_the_active_job(self):
        html = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")
        candidates = (BACKEND / "azureUtils" / "storage" / "candidates.py").read_text(encoding="utf-8")

        self.assertIn('"matchJobId": match.get("jobId", "")', candidates)
        self.assertIn('"matchCalculated": match_calculated', candidates)
        self.assertIn('"courtEvidenceCount": court_evidence.get("evidenceCount")', candidates)
        self.assertIn("profile.matchJobId", html)
        self.assertIn("JD match not calculated for the current job", html)

    def test_enrichment_and_manual_matching_are_separate_guided_actions(self):
        find_out = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")
        temp_profiles = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")
        routes = (BACKEND / "azureUtils" / "routes" / "azureJobEndpoints.py").read_text(encoding="utf-8")

        self.assertIn('/external/temp/{person_id}/calculate-match', routes)
        self.assertIn('"status": "calculated"', routes)
        self.assertIn('"calculationMode": "explicit_user_action"', routes)
        self.assertIn("function calculateCandidateMatch", find_out)
        self.assertIn('id="candidateMatchDialog"', find_out)
        self.assertIn("function calculateTempMatch", temp_profiles)
        self.assertIn('id="matchStatsDialog"', temp_profiles)
        self.assertIn('body: JSON.stringify({ domain: currentDomain() })', temp_profiles)
        self.assertNotIn("Enrich checked and rematch", temp_profiles)

    def test_start_over_clears_scoped_workflow_state_not_saved_records(self):
        reset_sources = [
            (PAGES / "find-candidate.html").read_text(encoding="utf-8"),
            (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8"),
            (PAGES / "components" / "sidebar.html").read_text(encoding="utf-8"),
        ]

        for source in reset_sources:
            self.assertIn("shortlistDetails:", source)
            self.assertIn("scheduleTracking:", source)
            self.assertIn("latestScheduleTracking:", source)
            self.assertIn("atlasSourcingClient:", source)
            self.assertIn("activeSourcingSearch:", source)
        self.assertIn("Saved TEMP records were not deleted", reset_sources[0])
        for source in reset_sources:
            self.assertNotIn("/api/azureJobs/external/temp/", source)

    def test_temp_page_is_in_talent_navigation_and_access_menu(self):
        nav = (PAGES / "components" / "sideNav.html").read_text(encoding="utf-8")
        main = (BACKEND / "main.py").read_text(encoding="utf-8")

        self.assertLess(nav.index(">Find Candidates (Out)</a>"), nav.index(">Saved Searches</a>"))
        self.assertLess(nav.index(">Saved Searches</a>"), nav.index(">TEMP Profiles</a>"))
        self.assertLess(nav.index(">TEMP Profiles</a>"), nav.index(">Profiles</a>"))
        self.assertIn('"key": "temp_profiles"', main)
        self.assertIn('"key": "saved_searches"', main)


if __name__ == "__main__":
    unittest.main()
