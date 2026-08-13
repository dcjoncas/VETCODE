import re
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PAGES_ROOT = BACKEND_ROOT / "ui" / "pages"


class LegalReadyPlatformTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide = (PAGES_ROOT / "legalready-training.html").read_text(encoding="utf-8")
        cls.mobile = (PAGES_ROOT / "mobile.html").read_text(encoding="utf-8")
        cls.schedule = (PAGES_ROOT / "schedule-interview.html").read_text(encoding="utf-8")
        cls.nav = (PAGES_ROOT / "components" / "sideNav.html").read_text(encoding="utf-8")
        cls.login = (BACKEND_ROOT / "ui" / "index.html").read_text(encoding="utf-8")

    def test_training_is_separate_from_mobile_app(self):
        self.assertNotIn("LegalReady mobile training", self.mobile)
        self.assertNotIn('key: "training"', self.mobile)
        self.assertNotIn("legalReadyTrainingContext", self.schedule)
        self.assertNotIn("training-no-send", self.schedule)
        self.assertIn("overflow-x: hidden", self.mobile)
        self.assertIn("overflow-wrap: break-word", self.mobile)

    def test_guide_has_complete_ten_step_platform_flow(self):
        keys = re.findall(r'^\s+key: "([^"]+)",$', self.guide, flags=re.MULTILINE)
        self.assertEqual(
            keys,
            [
                "talent",
                "candidate-source",
                "profile",
                "jd",
                "match",
                "shortlist",
                "interest",
                "internal-call",
                "client-call",
                "onboarding",
            ],
        )
        for page in (
            "find-candidate.html",
            "mine-candidate-external.html",
            "profile-preview.html",
            "job-descriptions.html",
            "match-role.html",
            "client-comm.html",
            "schedule-interview.html?interview=ready&purpose=role",
            "schedule-interview.html?interview=client",
            "onboarding-admin.html",
        ):
            self.assertIn(page, self.guide)

    def test_guide_uses_all_three_legalready_aliases_and_safety_rule(self):
        for email in (
            "mitch.blake@legalready.io",
            "michael.shrader@legalready.io",
            "kacey-jo.hyde@legalready.io",
        ):
            self.assertIn(email, self.guide)
        self.assertIn("Do not send a live email or calendar invitation", self.guide)

    def test_sample_result_and_responsive_rules_are_present(self):
        self.assertIn("63% match", self.guide)
        self.assertIn("profile 2368", self.guide)
        self.assertIn("JD 88", self.guide)
        self.assertIn("50% training threshold", self.guide)
        self.assertIn("@media (max-width: 680px)", self.guide)

    def test_login_screen_links_to_platform_training(self):
        self.assertIn("Platform Training", self.login)
        self.assertIn('id="platformTrainingLink"', self.login)
        self.assertIn("pages/legalready-training.html?domain=dev", self.login)
        self.assertNotIn("training-guide-link", self.nav)
        self.assertIn('href="mobile.html"', self.nav)
        self.assertIn(">Mobile Modules</a>", self.nav)


if __name__ == "__main__":
    unittest.main()
