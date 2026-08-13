import os
import unittest
from unittest.mock import Mock, patch

from peopleDataLabs import peopleSearch


class PeopleSearchTests(unittest.TestCase):
    def test_lawyer_payload_requires_role_region_experience_practice_and_city(self):
        payload = peopleSearch.build_lawyer_search_payload(
            titles=["Associate Attorney", "Counsel"],
            practice_areas=["Professional Liability", "Civil Litigation"],
            locations=["Los Angeles", "Irvine", "Walnut Creek"],
            region="California",
            min_years=3,
            strict_locations=True,
            size=5,
        )

        query = payload["query"]["bool"]
        self.assertEqual(payload["size"], 5)
        self.assertIn({"term": {"location_region": "california"}}, query["must"])
        self.assertIn({"range": {"inferred_years_experience": {"gte": 3}}}, query["must"])
        self.assertIn(
            {"terms": {"location_locality": ["los angeles", "irvine", "walnut creek"]}},
            query["must"],
        )
        self.assertTrue(
            any(
                {"match_phrase": {"job_title.text": "associate attorney"}}
                in clause.get("bool", {}).get("should", [])
                for clause in query["must"]
            )
        )
        self.assertNotIn("minimum_should_match", str(payload))
        self.assertTrue(
            any(
                {"match_phrase": {"summary": "professional liability"}}
                in clause.get("bool", {}).get("should", [])
                for clause in query["must"]
            )
        )

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.post")
    def test_search_minimizes_fields_and_sets_timeout(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": 200,
            "total": 12,
            "data": [{"id": "candidate-1"}],
            "scroll_token": "next-page-token",
        }
        post.return_value = response

        result = peopleSearch.searchLawyers(
            titles=["Attorney"],
            practice_areas=["Professional Liability"],
            locations=["Los Angeles"],
            region="California",
            min_years=3,
            strict_locations=True,
            size=5,
            scroll_token="current-page-token",
        )

        self.assertEqual(result["total"], 12)
        self.assertEqual(result["scroll_token"], "next-page-token")
        request = post.call_args.kwargs
        self.assertEqual(request["timeout"], peopleSearch.PDL_TIMEOUT)
        self.assertEqual(request["json"]["dataset"], "resume")
        self.assertEqual(request["json"]["scroll_token"], "current-page-token")
        self.assertTrue(request["json"]["titlecase"])
        included = set(request["json"]["data_include"].split(","))
        self.assertIn("linkedin_url", included)
        self.assertIn("inferred_years_experience", included)
        self.assertNotIn("recommended_personal_email", included)
        self.assertNotIn("personal_emails", included)
        self.assertNotIn("mobile_phone", included)
        self.assertNotIn("street_addresses", included)

    def test_oversized_scroll_token_is_rejected_before_provider_request(self):
        with self.assertRaisesRegex(peopleSearch.PeopleDataLabsError, "page token is invalid"):
            peopleSearch.searchLawyers(
                titles=["Attorney"],
                practice_areas=["Professional Liability"],
                locations=["Los Angeles"],
                scroll_token="x" * 4097,
            )

    def test_skill_payload_uses_jd_skills_as_discovery_signals(self):
        payload = peopleSearch.build_skill_search_payload(
            ["Digital Radiography", "Endodontic Chairside", "Patient Comfort"],
            size=5,
        )

        query = payload["query"]["bool"]
        self.assertNotIn("must", query)
        self.assertNotIn("minimum_should_match", query)
        self.assertEqual(len(query["should"]), 3)
        self.assertEqual(payload["size"], 5)

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.post")
    def test_paged_404_marks_pagination_complete(self, post):
        post.return_value = Mock(status_code=404)

        result = peopleSearch.searchSkills(
            ["civil litigation"],
            size=5,
            scroll_token="last-page-token",
        )

        self.assertEqual(result["data"], [])
        self.assertIsNone(result["scroll_token"])

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.post")
    def test_initial_404_is_clean_zero_match_not_provider_failure(self, post):
        post.return_value = Mock(status_code=404)

        result = peopleSearch.searchSkills(["very specific missing skill"], size=5)

        self.assertEqual(result["status"], 404)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["data"], [])
        self.assertIsNone(result["scroll_token"])

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_is_explicit(self):
        with self.assertRaisesRegex(peopleSearch.PeopleDataLabsError, "not configured"):
            peopleSearch.searchSkills(["civil litigation"], 5)

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.post")
    def test_rate_limit_error_does_not_expose_provider_body(self, post):
        response = Mock(status_code=429)
        response.text = "provider-internal-response"
        post.return_value = response

        with self.assertRaisesRegex(peopleSearch.PeopleDataLabsError, "rate limit") as error:
            peopleSearch.searchSkills(["civil litigation"], 5)
        self.assertNotIn("provider-internal-response", str(error.exception))

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.post")
    def test_credit_error_preserves_provider_status(self, post):
        post.return_value = Mock(status_code=402)

        with self.assertRaisesRegex(peopleSearch.PeopleDataLabsError, "credits") as error:
            peopleSearch.searchSkills(["civil litigation"], 5)

        self.assertEqual(error.exception.status_code, 402)
        self.assertEqual(post.call_count, 2)

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.post")
    def test_credit_limited_search_retries_with_one_record_page(self, post):
        credit_error = Mock(status_code=402)
        success = Mock(status_code=200)
        success.json.return_value = {
            "status": 200,
            "total": 277,
            "data": [{"id": "candidate-1"}],
            "scroll_token": "next-page-token",
        }
        post.side_effect = [credit_error, success]

        result = peopleSearch.searchSkills(["civil litigation"], 10)

        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["size"], 10)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["size"], 1)
        self.assertTrue(result["credit_limited"])
        self.assertEqual(result["requested_size"], 10)
        self.assertEqual(result["effective_size"], 1)

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.get")
    def test_selected_person_enrichment_uses_exact_pdl_id_and_minimized_fields(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": 200,
            "likelihood": 10,
            "data": {"id": "pdl-123", "full_name": "Sample Attorney"},
        }
        get.return_value = response

        result = peopleSearch.enrichPerson(
            pdl_id="pdl-123",
            profile="https://www.linkedin.com/in/sample-attorney",
        )

        self.assertEqual(result["likelihood"], 10)
        request = get.call_args
        self.assertEqual(request.args[0], peopleSearch.PDL_ENRICH_URL)
        self.assertEqual(request.kwargs["params"]["pdl_id"], "pdl-123")
        self.assertNotIn("profile", request.kwargs["params"])
        self.assertEqual(request.kwargs["headers"]["X-Api-Key"], "test-key")
        included = set(request.kwargs["params"]["data_include"].split(","))
        self.assertIn("experience.summary", included)
        self.assertIn("education.school.name", included)
        self.assertNotIn("phone_numbers", included)
        self.assertNotIn("street_addresses", included)

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.get")
    def test_selected_person_enrichment_404_is_a_clean_no_match(self, get):
        get.return_value = Mock(status_code=404)

        result = peopleSearch.enrichPerson(profile="linkedin.com/in/missing-profile")

        self.assertEqual(result["status"], 404)
        self.assertIsNone(result["data"])

    @patch.dict(os.environ, {"PDL_API_KEY": "test-key"}, clear=False)
    @patch("peopleDataLabs.peopleSearch.requests.get")
    def test_name_enrichment_requires_linkedin_linked_california_match(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": 200,
            "likelihood": 9,
            "data": {
                "id": "pdl-court-123",
                "full_name": "Sample Attorney",
                "linkedin_url": "linkedin.com/in/sample-attorney",
            },
        }
        get.return_value = response

        result = peopleSearch.enrichPerson(
            name="Sample Attorney",
            region="California",
            country="United States",
            min_likelihood=8,
            required="linkedin_url",
        )

        self.assertEqual(result["likelihood"], 9)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["name"], "Sample Attorney")
        self.assertEqual(params["region"], "California")
        self.assertEqual(params["country"], "United States")
        self.assertEqual(params["min_likelihood"], 8)
        self.assertEqual(params["required"], "linkedin_url")
        self.assertNotIn("profile", params)
        self.assertNotIn("pdl_id", params)


if __name__ == "__main__":
    unittest.main()
