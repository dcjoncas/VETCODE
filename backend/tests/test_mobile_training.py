from pathlib import Path
import unittest


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"


class MobileTrainingPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mobile = (PAGES / "mobile.html").read_text(encoding="utf-8")
        cls.schedule = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")

    def test_mobile_page_contains_the_complete_legalready_training_path(self):
        self.assertIn("LegalReady mobile training", self.mobile)
        self.assertIn("const legalReadyTrainingSteps = [", self.mobile)
        for key in (
            'key: "job"',
            'key: "candidate"',
            'key: "profile"',
            'key: "match"',
            'key: "client"',
            'key: "candidate-review"',
            'key: "client-interview"',
            'key: "status"',
        ):
            self.assertIn(key, self.mobile)

    def test_mobile_training_uses_only_the_three_approved_aliases(self):
        for email in (
            "mitch.blake@legalready.io",
            "michael.shrader@legalready.io",
            "kacey-jo.hyde@legalready.io",
        ):
            self.assertIn(email, self.mobile)
            self.assertIn(email, self.schedule)

    def test_mobile_layout_has_phone_width_and_footer_guards(self):
        self.assertIn("overflow-x: hidden", self.mobile)
        self.assertIn("word-break: normal", self.mobile)
        self.assertIn("@media (max-width: 340px)", self.mobile)
        self.assertIn("position: static", self.mobile)
        self.assertIn('document.body.classList.toggle("training-active", showTraining)', self.mobile)
        self.assertIn('document.body.classList.toggle("onboarding-active", showOnboarding)', self.mobile)

    def test_training_schedule_blocks_live_sending(self):
        self.assertIn("function legalReadyTrainingContext()", self.schedule)
        self.assertIn("button.disabled = Boolean(training)", self.schedule)
        self.assertIn('provider: training ? "training-no-send" : "Draft"', self.schedule)
        self.assertIn("Live calendar sending is blocked in LegalReady training mode", self.schedule)
        self.assertIn("Live booking and calendar sending are blocked in LegalReady training mode", self.schedule)


if __name__ == "__main__":
    unittest.main()
