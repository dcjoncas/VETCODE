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

    def test_stored_scores_are_only_current_for_the_active_job(self):
        html = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")
        candidates = (BACKEND / "azureUtils" / "storage" / "candidates.py").read_text(encoding="utf-8")

        self.assertIn('"matchJobId": match.get("jobId", "")', candidates)
        self.assertIn("profile.matchJobId", html)
        self.assertIn("Not matched to the current JD", html)

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
        self.assertIn("Saved TEMP records were not deleted", reset_sources[0])
        for source in reset_sources:
            self.assertNotIn("/api/azureJobs/external/temp/", source)

    def test_temp_page_is_in_talent_navigation_and_access_menu(self):
        nav = (PAGES / "components" / "sideNav.html").read_text(encoding="utf-8")
        main = (BACKEND / "main.py").read_text(encoding="utf-8")

        self.assertLess(nav.index(">Find Candidates (Out)</a>"), nav.index(">TEMP Profiles</a>"))
        self.assertLess(nav.index(">TEMP Profiles</a>"), nav.index(">Profiles</a>"))
        self.assertIn('"key": "temp_profiles"', main)


if __name__ == "__main__":
    unittest.main()
