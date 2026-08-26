import unittest

from azureUtils.routes.azureJobEndpoints import (
    _lawyer_match_score,
    _lawyer_search_criteria,
    _lawyer_search_criteria_errors,
    _pdl_pagination,
    _people_data_row,
)


class LawyerMiningTests(unittest.TestCase):
    def setUp(self):
        self.jd = {
            "jd_id": 85,
            "title": "MC-2026-003 Professional Liability Attorney (3+ years) in Los Angeles, Irvine, Walnut Creek",
            "description": (
                "Los Angeles, CA | Hybrid | Mid-Level Attorney. Must reside in California. "
                "Professional liability and civil litigation defense. Conduct depositions and trial preparation."
            ),
        }

    def test_criteria_are_derived_from_real_law_jd_shape(self):
        criteria = _lawyer_search_criteria(self.jd)

        self.assertEqual(criteria["minYears"], 3)
        self.assertEqual(criteria["region"], "California")
        self.assertEqual(criteria["locations"], ["Los Angeles", "Irvine", "Walnut Creek"])
        self.assertIn("professional liability", criteria["practiceAreas"])
        self.assertIn("civil litigation", criteria["practiceAreas"])
        self.assertIn("professional liability", criteria["requiredPracticeAreas"])
        self.assertIn("professional liability", criteria["requiredSkills"])
        self.assertNotIn("civil litigation", criteria["requiredPracticeAreas"])
        self.assertTrue(criteria["strictLocations"])
        self.assertEqual(criteria["workArrangement"], "hybrid")
        self.assertEqual(criteria["workforceLocation"], "onshore")
        self.assertEqual(_lawyer_search_criteria_errors(criteria), [])

    def test_missing_non_negotiable_values_are_reported_before_provider_search(self):
        criteria = _lawyer_search_criteria(
            {
                "title": "Litigation Attorney",
                "description": "California civil litigation attorney.",
            }
        )

        self.assertEqual(criteria["minYears"], 0)
        self.assertEqual(criteria["workArrangement"], "")
        self.assertEqual(
            _lawyer_search_criteria_errors(criteria),
            ["Minimum years (at least 1)", "Work arrangement"],
        )

    def test_lawyer_score_is_transparent_and_requires_bar_verification(self):
        criteria = _lawyer_search_criteria(self.jd)
        row = {
            "id": "candidate-1",
            "full_name": "Sample Attorney",
            "first_name": "Sample",
            "last_name": "Attorney",
            "job_title": "Associate Attorney",
            "job_company_name": "Example LLP",
            "location_name": "Los Angeles, California, United States",
            "location_locality": "Los Angeles",
            "location_region": "California",
            "inferred_years_experience": 7,
            "summary": "Civil litigation and professional liability defense attorney.",
            "skills": ["Civil Litigation", "Professional Liability", "Depositions"],
            "linkedin_url": "linkedin.com/in/sample-attorney",
            "job_last_verified": "2026-06-01",
        }

        score, matched, details = _lawyer_match_score(row, criteria)
        result = _people_data_row(row, [], [], criteria)

        self.assertGreaterEqual(score, 90)
        self.assertIn("Attorney title", matched)
        self.assertEqual(details["components"]["attorneyTitle"], 30)
        self.assertEqual(details["components"]["state"], 15)
        self.assertEqual(result["verification"]["california_bar_status"], "not_verified")
        self.assertIn("Sample+Attorney", result["verification"]["california_bar_search_url"])
        self.assertEqual(result["email"], "")

    def test_pdl_pagination_exposes_only_the_next_page_token(self):
        pagination = _pdl_pagination({"scroll_token": "next-page-token"}, 5)

        self.assertEqual(
            pagination,
            {
                "pageSize": 5,
                "hasNext": True,
                "nextScrollToken": "next-page-token",
                "costLabel": "up to 5 record credits",
            },
        )
        self.assertEqual(
            _pdl_pagination({}, 5),
            {
                "pageSize": 5,
                "hasNext": False,
                "nextScrollToken": "",
                "costLabel": "up to 5 record credits",
            },
        )

    def test_criteria_detect_non_california_target_jurisdiction(self):
        jd = {
            "jd_id": 99,
            "title": "Commercial Litigation Attorney in New York",
            "description": "New York, NY. Active New York license required.",
        }

        criteria = _lawyer_search_criteria(jd)

        self.assertEqual(criteria["region"], "New York")


if __name__ == "__main__":
    unittest.main()
