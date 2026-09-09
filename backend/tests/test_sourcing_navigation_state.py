import unittest
from pathlib import Path


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"


class SourcingNavigationStateTests(unittest.TestCase):
    def test_find_out_persists_and_restores_complete_browser_view(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("function persistSourcingViewState()", html)
        self.assertIn("function restoreSourcingViewState(", html)
        self.assertIn("results: latestExternalResults", html)
        self.assertIn("selectedCandidateKeys: [...selectedCandidateKeys()]", html)
        self.assertIn("openCriteriaGroups: criteriaPickerOpenGroups()", html)
        self.assertIn('window.addEventListener("pagehide", persistSourcingViewState)', html)
        self.assertIn("restoreSourcingScroll(savedView)", html)

    def test_saved_search_fallback_rebuilds_filters_without_provider_request(self):
        html = (PAGES / "mine-candidate-external.html").read_text(encoding="utf-8")

        self.assertIn("function applySavedQueryToPage(savedQuery = {}, response = {})", html)
        self.assertIn("applySavedQueryToPage(response.savedQuery || {}, response)", html)
        self.assertIn("activeSourcingSearch:${currentDomain()}", html)
        self.assertIn("The provider was not contacted and 0 search credits were used", html)

    def test_shared_back_button_uses_native_history_and_waits_for_page_content(self):
        component = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertIn("window.history.back()", component)
        self.assertIn("window.history.forward()", component)
        self.assertIn('window.addEventListener("devready-page-content-restored", restoreWorkflowPosition)', component)
        self.assertIn("`externalSourcingView:${keepDomain}`", component)


if __name__ == "__main__":
    unittest.main()
