import unittest
from unittest.mock import patch

from azureUtils.routes.azureJobEndpoints import (
    _candidate_search_criteria,
    _candidate_search_criteria_errors,
    _lawyer_match_score,
    _lawyer_search_criteria,
    _lawyer_search_criteria_errors,
    _pdl_pagination,
    _people_data_row,
    external_candidate_criteria,
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

        self.assertEqual(criteria["minYears"], 1)
        self.assertEqual(criteria["workArrangement"], "")
        self.assertEqual(
            _lawyer_search_criteria_errors(criteria),
            [
                "Target cities",
                "Remote, onsite, or hybrid",
            ],
        )

    def test_universal_technology_criteria_are_derived_and_validated(self):
        jd = {
            "jd_id": 101,
            "title": "Senior Software Engineer (5+ years) in Denver",
            "description": "Denver, CO | Remote. AWS Certified Solutions Architect required.",
        }

        criteria = _candidate_search_criteria(
            jd,
            job_skills=["Python", "AWS", "FastAPI"],
            domain="technology",
        )

        self.assertEqual(criteria["domain"], "dev")
        self.assertEqual(criteria["titles"], ["Senior Software Engineer"])
        self.assertEqual(criteria["mustHaveSkills"], ["Python", "AWS", "FastAPI"])
        self.assertEqual(criteria["locations"], ["Denver"])
        self.assertEqual(criteria["minYears"], 3)
        self.assertEqual(criteria["experienceRanges"], ["3-5"])
        self.assertEqual(criteria["licenseOrCertification"], "AWS Certified Solutions Architect")
        self.assertEqual(criteria["licensesOrCertifications"], ["AWS Certified Solutions Architect"])
        self.assertEqual(criteria["workArrangement"], "remote")
        self.assertEqual(criteria["workArrangements"], ["remote"])
        self.assertEqual(criteria["workforceLocation"], "onshore")
        self.assertEqual(criteria["workforceLocations"], ["onshore"])
        self.assertEqual(_candidate_search_criteria_errors(criteria), [])

    def test_multi_select_overrides_are_preserved_in_the_shared_contract(self):
        criteria = _candidate_search_criteria(
            {
                "title": "Platform Engineer (5+ years) in Denver",
                "description": "Denver, CO | Hybrid",
            },
            job_skills=["Python", "AWS"],
            domain="technology",
            titles="Platform Engineer,Site Reliability Engineer",
            required_skills="Python,Kubernetes,Terraform",
            locations="Denver,Toronto",
            experience_ranges="3-5,10-14",
            licenses_or_certifications="AWS Certified Solutions Architect,CKA",
            work_arrangements="remote,hybrid",
            workforce_locations="onshore,offshore",
        )

        self.assertEqual(criteria["experienceRanges"], ["3-5", "10-14"])
        self.assertEqual(criteria["minYears"], 3)
        self.assertEqual(
            criteria["licensesOrCertifications"],
            ["AWS Certified Solutions Architect", "CKA"],
        )
        self.assertEqual(criteria["workArrangements"], ["remote", "hybrid"])
        self.assertEqual(criteria["workforceLocations"], ["onshore", "offshore"])
        self.assertEqual(criteria["workforceLocation"], "either")
        self.assertEqual(_candidate_search_criteria_errors(criteria), [])

    def test_explicit_none_required_is_a_valid_credential_decision(self):
        criteria = _candidate_search_criteria(
            {
                "title": "Mechanical Engineer (2+ years) in Austin",
                "description": "Austin, TX | Onsite",
            },
            job_skills=["CAD", "SolidWorks"],
            domain="build",
            license_or_certification="None required",
        )

        self.assertEqual(criteria["domain"], "engineer")
        self.assertEqual(criteria["licenseOrCertification"], "None required")
        self.assertEqual(_candidate_search_criteria_errors(criteria), [])

    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_criteria_endpoint_returns_the_shared_contract_for_build_domain(self, get_job):
        get_job.return_value = (
            {
                "jd_id": 202,
                "title": "Civil Engineer (4+ years) in Phoenix",
                "description": "Phoenix, AZ | Hybrid. Professional Engineer (PE) license required.",
            },
            ["Civil 3D", "Drainage Design"],
        )

        response = external_candidate_criteria("202", "build")

        self.assertEqual(response["domain"], "engineer")
        self.assertEqual(response["criteria"]["policyVersion"], 4)
        self.assertEqual(response["criteria"]["licenseOrCertification"], "Professional Engineer (PE) license")
        self.assertEqual(response["criteriaStatus"]["missing"], [])
        self.assertTrue(response["criteriaStatus"]["complete"])

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
