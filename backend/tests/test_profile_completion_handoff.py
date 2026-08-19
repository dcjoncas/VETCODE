import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
PAGES = BACKEND / "ui" / "pages"


class ProfileCompletionHandoffTests(unittest.TestCase):
    def test_shared_incomplete_profile_message_has_candidate_safe_actions(self):
        helper = (PAGES / "JS" / "profileCompletion.js").read_text(encoding="utf-8")

        self.assertIn('label: "AI profile & personality chat"', helper)
        self.assertIn('candidateSafe: true', helper)
        self.assertIn("Open AI profile chat", helper)
        self.assertIn("Copy link", helper)
        self.assertIn("Copy message", helper)
        self.assertIn("Send link to candidate", helper)
        self.assertIn("Please kindly complete your DevReady AI profile and personality chat", helper)
        self.assertIn("DEVREADY INTERNAL LINK", helper)

    def test_candidate_chat_page_exposes_the_same_open_copy_send_flow(self):
        page = (PAGES / "candidate-chat.html").read_text(encoding="utf-8")

        self.assertIn('id="openChatButton"', page)
        self.assertIn('id="copyButton"', page)
        self.assertIn("function candidateChatUrl(token)", page)
        self.assertIn("function candidateChatMessage(link)", page)
        self.assertIn("function renderCandidateChatLink(link)", page)
        self.assertIn("function copyChatLink()", page)
        self.assertIn("Please kindly complete your DevReady AI profile and personality chat", page)
        self.assertNotIn("http://${urlString}", page)

    def test_onboarding_blocker_opens_completion_actions_instead_of_plain_alert(self):
        helper = (PAGES / "JS" / "profileCompletion.js").read_text(encoding="utf-8")
        status = (PAGES / "status-tracker.html").read_text(encoding="utf-8")

        self.assertIn("context = {}", helper)
        self.assertIn("Complete profile before onboarding", status)
        self.assertIn("function onboardingMissingPieces(message)", status)
        self.assertIn("async function showOnboardingProfileCompletionBlock(message, payload)", status)
        self.assertIn("window.profileCompletion.showProfileCompletionLinks", status)
        self.assertIn("await showOnboardingProfileCompletionBlock(message, payload)", status)
        self.assertNotIn('alert(error.message || "Could not start onboarding.")', status)


if __name__ == "__main__":
    unittest.main()
