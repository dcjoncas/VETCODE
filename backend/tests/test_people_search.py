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


if __name__ == "__main__":
    unittest.main()
