import os
import unittest
from unittest.mock import Mock, patch

from azureUtils.routes import azureJobEndpoints
from legalSources import braveSearch, coreSignal, courtListener


class LegalSourceClientTests(unittest.TestCase):
    @patch.dict(os.environ, {"CORESIGNAL_API_KEY": "core-test-key"}, clear=False)
    @patch("legalSources.coreSignal.requests.post")
    def test_coresignal_preview_search_is_keyed_and_paginated(self, post):
        response = Mock(status_code=200)
        response.headers = {"x-total-results": "24", "x-total-pages": "3"}
        response.json.return_value = [
            {
                "id": 101,
                "full_name": "Sample Attorney",
                "headline": "Professional Liability Attorney",
                "location": "Los Angeles, California",
            }
        ]
        post.return_value = response

        result = coreSignal.search_people(
            titles=["Attorney"],
            practice_areas=["Professional Liability"],
            locations=["Los Angeles"],
            size=5,
            page=2,
        )

        request = post.call_args.kwargs
        self.assertEqual(request["headers"]["apikey"], "core-test-key")
        self.assertEqual(request["params"], {"page": 2, "items_per_page": 5})
        self.assertIn("Professional Liability", request["json"]["keyword"])
        self.assertEqual(result["total"], 24)
        self.assertEqual(result["next_page"], 3)
        self.assertEqual(result["credits_used"], 1)

    @patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "brave-test-key"}, clear=False)
    @patch("legalSources.braveSearch.requests.get")
    def test_brave_search_uses_subscription_header_and_more_results_flag(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "query": {"more_results_available": True},
            "web": {"results": [{"title": "Attorney Bio", "url": "https://example.com/bio"}]},
        }
        get.return_value = response

        result = braveSearch.search_web("professional liability attorney", size=5, page=1)

        request = get.call_args.kwargs
        self.assertEqual(request["headers"]["X-Subscription-Token"], "brave-test-key")
        self.assertEqual(request["params"]["offset"], 1)
        self.assertFalse(request["params"]["text_decorations"] == "true")
        self.assertEqual(result["next_page"], 2)
        self.assertEqual(result["requests_used"], 1)

    @patch.dict(os.environ, {"COURTLISTENER_API_TOKEN": "court-test-token"}, clear=False)
    @patch("legalSources.courtListener.requests.get")
    def test_courtlistener_searches_recap_and_opinions_without_identity_claim(self, get):
        recap = Mock(status_code=200)
        recap.json.return_value = {
            "count": 1,
            "results": [
                {
                    "caseName": "Example v. Example",
                    "docketNumber": "2:26-cv-001",
                    "absolute_url": "/docket/1/example/",
                    "snippet": "<mark>Sample Attorney</mark>",
                }
            ],
        }
        opinions = Mock(status_code=200)
        opinions.json.return_value = {"count": 0, "results": []}
        get.side_effect = [recap, opinions]

        result = courtListener.search_evidence("Sample Attorney", size=3)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            get.call_args_list[0].kwargs["headers"]["Authorization"],
            "Token court-test-token",
        )
        self.assertEqual(get.call_args_list[0].kwargs["params"]["type"], "r")
        self.assertEqual(get.call_args_list[1].kwargs["params"]["type"], "o")
        self.assertFalse(result["identityVerified"])
        self.assertEqual(result["results"][0]["snippet"], "Sample Attorney")
        self.assertEqual(result["results"][0]["url"], "https://www.courtlistener.com/docket/1/example/")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_provider_keys_are_explicit(self):
        with self.assertRaisesRegex(coreSignal.CoreSignalError, "not configured"):
            coreSignal.search_people([], [], [])
        with self.assertRaisesRegex(braveSearch.BraveSearchError, "not configured"):
            braveSearch.search_web("attorney")
        with self.assertRaisesRegex(courtListener.CourtListenerError, "not configured"):
            courtListener.search_evidence("Sample Attorney")


class LegalSourceRouteTests(unittest.TestCase):
    def setUp(self):
        self.jd = {
            "jd_id": 85,
            "company": "Murchison & Cumming",
            "title": "Professional Liability Attorney (3+ years) in Los Angeles",
            "description": "California professional liability and civil litigation attorney.",
        }

    @patch.dict(
        os.environ,
        {
            "PDL_API_KEY": "pdl-key",
            "CORESIGNAL_API_KEY": "core-key",
            "BRAVE_SEARCH_API_KEY": "brave-key",
            "COURTLISTENER_API_TOKEN": "court-key",
        },
        clear=False,
    )
    def test_provider_status_reports_readiness_without_secrets(self):
        result = azureJobEndpoints.external_provider_status()

        self.assertTrue(result["providers"]["coresignal"]["ready"])
        self.assertTrue(result["providers"]["brave"]["ready"])
        self.assertTrue(result["providers"]["courtlistener"]["ready"])
        self.assertNotIn("core-key", str(result))
        self.assertFalse(result["secretsExposed"])

    @patch("azureUtils.routes.azureJobEndpoints.coreSignal.search_people")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_coresignal_route_returns_standard_candidate_and_credit_audit(self, get_job, search):
        get_job.return_value = (self.jd, ["Professional Liability", "Civil Litigation"])
        search.return_value = {
            "status": 200,
            "data": [
                {
                    "id": 101,
                    "full_name": "Sample Attorney",
                    "headline": "Professional Liability Attorney",
                    "title": "Associate Attorney",
                    "company_name": "Example LLP",
                    "location": "Los Angeles, California",
                    "country": "United States",
                }
            ],
            "total": 24,
            "page": 1,
            "page_size": 5,
            "has_more": True,
            "next_page": 2,
            "credits_used": 1,
        }

        result = azureJobEndpoints.external_candidate_search(
            domain="law",
            jd_id="85",
            source="coresignal",
            top_k=5,
            titles="",
            practice_areas="",
            locations="",
            region="",
            min_years=0,
            strict_locations=None,
            scroll_token="",
        )

        self.assertEqual(result["source"], "coresignal")
        self.assertEqual(result["results"][0]["source"], "coresignal")
        self.assertEqual(result["sourceAudit"]["estimatedCreditsUsed"], 1)
        self.assertEqual(result["pagination"]["nextScrollToken"], "2")
        self.assertEqual(result["pagination"]["costLabel"], "1 search credit")

    @patch("azureUtils.routes.azureJobEndpoints.courtListener.search_evidence")
    def test_court_evidence_is_never_used_for_scoring(self, search):
        search.return_value = {
            "provider": "CourtListener / RECAP",
            "results": [],
            "identityVerified": False,
        }

        result = azureJobEndpoints.external_candidate_legal_evidence(
            {"domain": "law", "name": "Sample Attorney", "size": 3}
        )

        self.assertFalse(result["usedForScoring"])
        self.assertFalse(result["identityVerified"])

    def test_brave_query_excludes_linkedin_and_import_is_blocked(self):
        query = azureJobEndpoints._brave_law_query(
            {
                "titles": ["Associate Attorney"],
                "requiredPracticeAreas": ["Professional Liability"],
                "locations": ["Los Angeles"],
                "region": "California",
            }
        )

        self.assertIn("-site:linkedin.com", query)
        direct_query = azureJobEndpoints._brave_direct_query(
            "Jane Attorney site:linkedin.com professional liability"
        )
        self.assertEqual(direct_query.lower().count("site:linkedin.com"), 1)
        self.assertIn("-site:linkedin.com", direct_query.lower())
        with self.assertRaisesRegex(Exception, "research-only"):
            azureJobEndpoints.external_candidate_import(
                {"domain": "law", "source": "brave", "candidate": {"source": "brave"}}
            )
        with self.assertRaisesRegex(Exception, "research-only"):
            azureJobEndpoints.external_candidate_import(
                {
                    "domain": "law",
                    "candidate": {
                        "source": "pdl",
                        "result_type": "public_web_evidence",
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
