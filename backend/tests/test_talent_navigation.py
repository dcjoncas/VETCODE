import unittest
from pathlib import Path


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"
SIDEBAR_CACHE_KEY = "20260818-talent-jd-flow"
PROCESS_FLOW_CACHE_KEY = "20260819-workflow-guide"


class TalentNavigationTests(unittest.TestCase):
    def test_sidebar_places_job_descriptions_after_talent(self):
        html = (PAGES / "components" / "sideNav.html").read_text(encoding="utf-8")
        talent = html.index(">Talent</a>")
        jobs = html.index(">Job Descriptions</a>")
        find_in = html.index(">Find Candidates (In)</a>")
        find_out = html.index(">Find Candidates (Out)</a>")
        saved_searches = html.index(">Saved Searches</a>")
        temp_profiles = html.index(">TEMP Profiles</a>")

        self.assertLess(talent, jobs)
        self.assertLess(jobs, find_in)
        self.assertLess(find_in, find_out)
        self.assertLess(find_out, saved_searches)
        self.assertLess(saved_searches, temp_profiles)

    def test_process_flow_routes_talent_through_job_descriptions(self):
        html = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")
        flow_start = html.index('<div class="flow"')
        flow = html[flow_start : html.index('<div class="workflow-status-foot">', flow_start)]

        talent = flow.index('data-flow-step="talent"')
        jobs = flow.index('data-flow-step="job-descriptions"')
        find_in = flow.index('data-flow-step="find-in"')
        find_out = flow.index('data-flow-step="find-out"')

        self.assertLess(talent, jobs)
        self.assertLess(jobs, find_in)
        self.assertLess(find_in, find_out)
        self.assertIn('talent: "job-descriptions"', html)
        self.assertIn('"job-descriptions": "find-in"', html)

    def test_shared_component_cache_keys_are_current(self):
        for page in PAGES.glob("*.html"):
            html = page.read_text(encoding="utf-8")
            if "components/sidebar.html" in html:
                self.assertIn(f"components/sidebar.html?v={SIDEBAR_CACHE_KEY}", html, page.name)
            if "components/processFlow.html" in html:
                self.assertIn(f"components/processFlow.html?v={PROCESS_FLOW_CACHE_KEY}", html, page.name)

    def test_shared_next_step_pill_has_a_visible_halo(self):
        html = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertIn("process-next-halo", html)
        self.assertIn(".step.next:not(.on)", html)
        self.assertIn(".process-next-action", html)
        self.assertIn("prefers-reduced-motion: reduce", html)

    def test_process_flow_uses_a_clear_completion_status_bar(self):
        html = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertIn('id="processProgressTrack"', html)
        self.assertIn('id="processProgressFill"', html)
        self.assertIn('id="processProgressCopy"', html)
        self.assertIn('id="processStatusTitle"', html)
        self.assertIn('id="processNextStepLink"', html)
        self.assertIn("function renderWorkflowStatus()", html)
        self.assertIn("solid = complete, outline = current, gray = remaining", html)
        self.assertIn('content: "✓"', html)
        self.assertIn("animation: none", html)

    def test_process_flow_is_a_permanent_workflow_guide(self):
        html = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertIn('<span class="process-workflow-heading">Workflow guide</span>', html)
        self.assertIn('<div id="processExpanded" class="process-expanded">', html)
        self.assertNotIn('id="processExpandButton"', html)
        self.assertNotIn("function toggleProcessFlow()", html)
        self.assertNotIn("devreadyProcessExpanded", html)

    def test_profile_preview_separates_workflow_and_record_actions(self):
        html = (PAGES / "profile-preview.html").read_text(encoding="utf-8")

        self.assertIn("Profile verification status", html)
        self.assertIn("View Public Profile", html)
        self.assertIn("Next: Candidate Chat", html)
        self.assertIn("More profile actions", html)
        self.assertIn('class="profile-action-panel"', html)

    def test_public_profile_is_client_facing_and_responsive(self):
        html = (PAGES / "profile-public.html").read_text(encoding="utf-8")

        self.assertIn("Candidate profile prepared by DevReady", html)
        self.assertIn("Professional summary", html)
        self.assertIn("Skills and experience", html)
        self.assertIn("Working style", html)
        self.assertIn("Career history", html)
        self.assertIn("function escapeHtml(value)", html)
        self.assertIn("@media (max-width:780px)", html)
        self.assertNotIn('id="skillsChart"', html)
        self.assertNotIn('<table class="table">', html)
        self.assertNotIn("No City Listed", html)

    def test_find_out_actions_are_clean_and_selection_count_is_below_them(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertNotIn("Add JD in Talent", html)
        self.assertIn("process-next-action", html)
        self.assertLess(html.index('id="btnViewTempProfiles"'), html.index('id="enrichmentSelectionStatus"'))
        self.assertLess(html.index('id="enrichmentSelectionStatus"'), html.index('id="results"'))

    def test_find_out_actions_unlock_from_explicit_prerequisites(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn('id="workflowGuidance"', html)
        self.assertIn('id="workflowGuidanceAction"', html)
        self.assertIn('id="btnContinueRoleMatch"', html)
        self.assertIn("function updateWorkflowGuidance()", html)
        self.assertIn("function focusWorkflowPrerequisite(target)", html)
        self.assertIn("function setWorkflowLinkEnabled(link, enabled)", html)
        self.assertIn("Find Candidates is locked", html)
        self.assertIn("Enrichment is locked", html)
        self.assertIn("Role & Match is locked", html)
        self.assertIn("The next action will unlock automatically", html)
        self.assertIn('href="temp-profiles.html" id="btnViewTempProfiles"', html)

    def test_talent_workflow_uses_shared_compact_job_picker(self):
        picker = (PAGES / "JS" / "jobDescriptionPicker.js").read_text(encoding="utf-8")

        self.assertIn("Compact view of the same roles grouped by client", picker)
        self.assertIn("Recent Call Ask JDs", picker)
        self.assertIn("jd-picker-tile", picker)
        self.assertIn('dental: "Dental"', picker)
        self.assertIn('law: "Law"', picker)
        self.assertIn('engineer: "Engineering"', picker)
        self.assertIn('dev: "Technology"', picker)

        for page_name in ("find-candidate.html", "match-role.html", "mine-candidate-external.html"):
            html = (PAGES / page_name).read_text(encoding="utf-8")
            self.assertIn("JS/jobDescriptionPicker.js?v=20260818-jd-mini-picker", html, page_name)
            self.assertIn("DevReadyJobPicker.mount", html, page_name)
            self.assertNotIn("select2", html.lower(), page_name)

    def test_job_library_always_labels_the_active_workspace_domain(self):
        html = (PAGES / "job-descriptions.html").read_text(encoding="utf-8")

        self.assertIn('dental: "Dental"', html)
        self.assertIn('engineer: "Engineering"', html)
        self.assertIn("domainLabel(currentDomain())", html)
        self.assertNotIn("domainLabel(jd.domain || currentDomain())", html)

    def test_removed_web_search_provider_is_not_visible_in_talent_ui(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertNotIn("Brave Search", html)
        self.assertNotIn('data-source="brave"', html)


if __name__ == "__main__":
    unittest.main()
