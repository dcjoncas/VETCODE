import unittest
from pathlib import Path


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"
CACHE_KEY = "20260818-talent-jd-flow"


class TalentNavigationTests(unittest.TestCase):
    def test_sidebar_places_job_descriptions_after_talent(self):
        html = (PAGES / "components" / "sideNav.html").read_text(encoding="utf-8")
        talent = html.index(">Talent</a>")
        jobs = html.index(">Job Descriptions</a>")
        find_in = html.index(">Find Candidates (In)</a>")
        find_out = html.index(">Find Candidates (Out)</a>")

        self.assertLess(talent, jobs)
        self.assertLess(jobs, find_in)
        self.assertLess(find_in, find_out)

    def test_process_flow_routes_talent_through_job_descriptions(self):
        html = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")
        flow = html[html.index('<div class="flow">') : html.index('</div>\n  </div>\n</div>', html.index('<div class="flow">'))]

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
                self.assertIn(f"components/sidebar.html?v={CACHE_KEY}", html, page.name)
            if "components/processFlow.html" in html:
                self.assertIn(f"components/processFlow.html?v={CACHE_KEY}", html, page.name)

    def test_shared_next_step_pill_has_a_visible_halo(self):
        html = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertIn("process-next-halo", html)
        self.assertIn(".step.next:not(.on)", html)
        self.assertIn(".process-next-action", html)
        self.assertIn("prefers-reduced-motion: reduce", html)

    def test_find_out_actions_are_clean_and_selection_count_is_below_them(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertNotIn("Add JD in Talent", html)
        self.assertIn("process-next-action", html)
        self.assertLess(html.index('id="btnViewTempProfiles"'), html.index('id="enrichmentSelectionStatus"'))
        self.assertLess(html.index('id="enrichmentSelectionStatus"'), html.index('id="results"'))


if __name__ == "__main__":
    unittest.main()
