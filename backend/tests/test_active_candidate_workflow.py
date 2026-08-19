import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
PAGES = BACKEND / "ui" / "pages"


class ActiveCandidateWorkflowTests(unittest.TestCase):
    def test_shortlist_keeps_order_and_domain_scoped_active_candidate(self):
        script = (PAGES / "JS" / "shortlist.js").read_text(encoding="utf-8")

        self.assertIn("`shortlist:${domain()}`", script)
        self.assertIn("`activeCandidateId:${domain()}`", script)
        self.assertIn("function position()", script)
        self.assertIn("function advance()", script)
        self.assertIn('"devready-active-candidate-changed"', script)
        self.assertIn("One active candidate drives profile review, scheduling, interviews", script)

    def test_shared_process_flow_shows_active_queue_position_and_next_action(self):
        component = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")
        updater = (PAGES / "JS" / "updateProcessFlow.js").read_text(encoding="utf-8")

        self.assertIn('id="candidateSelected"', component)
        self.assertIn('id="processNextCandidate"', component)
        self.assertIn("Active Candidate ${position}", updater)
        self.assertIn("activateNextProcessCandidate", updater)

    def test_schedule_page_uses_active_candidate_for_records(self):
        html = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")

        self.assertIn('id="activeCandidateQueueLabel"', html)
        self.assertIn("Candidate ${state.number} of ${state.total}", html)
        self.assertIn("async function moveToNextCandidate()", html)
        self.assertIn("activeCandidateId: candidateQueue.id", html)
        self.assertIn("shortlistPosition: candidateQueue.number", html)
        self.assertIn("shortlistTotal: candidateQueue.total", html)
        self.assertIn("shortlistOrder: shortlistArray.slice()", html)
        self.assertIn("Work on this candidate", html)

    def test_excel_export_uses_a_native_download_link_with_feedback(self):
        html = (PAGES / "temp-profiles.html").read_text(encoding="utf-8")

        self.assertIn('<a class="btn primary" id="btnExportLinkedInResults"', html)
        self.assertIn("exportLink.href = `${API_BASE}/api/azureJobs/external/temp/linkedin-results/export?domain=", html)
        self.assertIn("Excel download started", html)
        self.assertIn('exportLink.removeAttribute("href")', html)


if __name__ == "__main__":
    unittest.main()
