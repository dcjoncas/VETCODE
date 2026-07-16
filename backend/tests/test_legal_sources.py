import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

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

    @patch.dict(os.environ, {"COURTLISTENER_API_TOKEN": "court-test-token"}, clear=False)
    @patch("legalSources.courtListener.requests.get")
    def test_courtlistener_discovers_attorneys_from_jd_dockets(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "count": 921,
            "results": [
                {
                    "docket_id": 101,
                    "caseName": "Design Claim v. Architect",
                    "docketNumber": "2:25-cv-00101",
                    "docket_absolute_url": "/docket/101/design-claim/",
                    "court": "California Central District Court",
                    "court_id": "cacd",
                    "dateFiled": "2025-02-10",
                    "attorney": ["Alex Morgan", "Jamie Lee"],
                    "attorney_id": [11, 12],
                    "recap_documents": [
                        {"snippet": "The complaint alleges professional liability."}
                    ],
                },
                {
                    "docket_id": 202,
                    "caseName": "Broker Claim v. Insurer",
                    "docketNumber": "3:24-cv-00202",
                    "docket_absolute_url": "/docket/202/broker-claim/",
                    "court": "California Northern District Court",
                    "court_id": "cand",
                    "dateFiled": "2024-07-12",
                    "attorney": ["Alex Morgan", "State of California"],
                    "attorney_id": [11, 13],
                    "recap_documents": [
                        {"snippet": "The action concerns accounting malpractice."}
                    ],
                },
            ],
        }
        get.return_value = response

        result = courtListener.search_attorneys_by_criteria(
            {
                "requiredPracticeAreas": ["Professional Liability", "Accounting Malpractice"],
                "locations": ["Los Angeles", "Walnut Creek"],
                "region": "California",
                "minYears": 3,
            },
            size=5,
        )

        self.assertEqual(get.call_count, 1)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["type"], "r")
        self.assertIn("court_id:(cacd OR cand)", params["q"])
        self.assertIn('"Professional Liability"', params["q"])
        self.assertIn("dateFiled:[", params["q"])
        self.assertEqual(result["attorneysDiscovered"], 2)
        self.assertEqual(result["results"][0]["name"], "Alex Morgan")
        self.assertEqual(len(result["results"][0]["evidence"]), 2)
        self.assertEqual(result["requestsUsed"], 1)
        self.assertFalse(result["identityVerified"])

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

    def test_profile_name_alignment_rejects_conflicting_middle_initials(self):
        self.assertTrue(azureJobEndpoints._person_names_align("Alex Morgan", "Alex J Morgan"))
        self.assertTrue(azureJobEndpoints._person_names_align("Alex J Morgan", "Alex James Morgan"))
        self.assertFalse(azureJobEndpoints._person_names_align("Alex J Morgan", "Alex R Morgan"))

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

    @patch.dict(os.environ, {"PDL_API_KEY": "pdl-key"}, clear=True)
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.searchLawyers")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_pdl_credit_failure_returns_actionable_zero_record_audit(self, get_job, search):
        get_job.return_value = (self.jd, ["Professional Liability", "Civil Litigation"])
        search.side_effect = azureJobEndpoints.peopleDataLabs.PeopleDataLabsError(
            "People Data Labs credits are unavailable for this request.",
            402,
        )

        response = azureJobEndpoints.external_candidate_search(
            domain="law",
            jd_id="85",
            source="pdl",
            top_k=5,
            titles="",
            practice_areas="",
            locations="",
            region="",
            min_years=0,
            strict_locations=None,
            scroll_token="",
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(payload["code"], "provider_credits_required")
        self.assertIn("enough Person Search credits", payload["detail"])
        self.assertEqual(payload["results"], [])
        self.assertTrue(payload["sourceAudit"]["queryExecuted"])
        self.assertFalse(payload["sourceAudit"]["queryCompleted"])
        self.assertIsNone(payload["sourceAudit"]["totalMatches"])
        self.assertEqual(payload["sourceAudit"]["recordsReturned"], 0)
        self.assertEqual(payload["sourceAudit"]["recordsReviewed"], 0)
        self.assertEqual(payload["sourceAudit"]["estimatedCreditsUsed"], 0)
        self.assertIn("dashboard.peopledatalabs.com", payload["providerStatus"]["actionUrl"])
        self.assertNotIn("pdl-key", str(payload))

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

    @patch("azureUtils.routes.azureJobEndpoints.courtListener.search_evidence")
    def test_courtlistener_direct_search_returns_research_only_records(self, search):
        search.return_value = {
            "provider": "CourtListener / RECAP",
            "queryExecuted": True,
            "counts": {"recap_docket": 8, "published_opinion": 4},
            "results": [
                {
                    "evidenceType": "recap_docket",
                    "title": "Example v. Example",
                    "docketNumber": "2:26-cv-001",
                    "court": "California Central District Court",
                    "dateFiled": "2026-01-10",
                    "snippet": "Sample Attorney appeared as counsel.",
                    "url": "https://www.courtlistener.com/docket/1/example/",
                }
            ],
            "requestsUsed": 2,
            "identityVerified": False,
            "notice": "Confirm identity and role in every matter.",
        }

        result = azureJobEndpoints.external_candidate_search_direct(
            domain="law",
            query="Sample Attorney",
            source="courtlistener",
            top_k=3,
            scroll_token="",
        )

        search.assert_called_once_with("Sample Attorney", size=3)
        self.assertEqual(result["source"], "courtlistener")
        self.assertEqual(result["results"][0]["result_type"], "court_record_evidence")
        self.assertEqual(result["results"][0]["score"], 0)
        self.assertEqual(result["sourceAudit"]["totalMatches"], 12)
        self.assertEqual(result["sourceAudit"]["recordsReturned"], 1)
        self.assertEqual(result["sourceAudit"]["estimatedCreditsUsed"], 2)
        self.assertFalse(result["sourceAudit"]["identityVerified"])
        self.assertEqual(result["pagination"]["costLabel"], "2 API requests")

    @patch("azureUtils.routes.azureJobEndpoints.courtListener.search_attorneys_by_criteria")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_courtlistener_jd_search_returns_unscored_attorney_leads(self, get_job, search):
        get_job.return_value = (
            self.jd,
            ["Professional Liability", "Accounting Malpractice", "Los Angeles"],
        )
        search.return_value = {
            "provider": "CourtListener / RECAP",
            "queryExecuted": True,
            "matchingDockets": 921,
            "docketsReviewed": 20,
            "attorneysDiscovered": 7,
            "courtIds": ["cacd", "cand"],
            "practiceTerms": ["Professional Liability", "Accounting Malpractice"],
            "results": [
                {
                    "name": "Alex Morgan",
                    "attorneyId": "11",
                    "matchedPracticeAreas": ["Professional Liability"],
                    "courts": ["California Central District Court"],
                    "evidence": [
                        {
                            "docketId": "101",
                            "title": "Design Claim v. Architect",
                            "url": "https://www.courtlistener.com/docket/101/design-claim/",
                        }
                    ],
                }
            ],
            "requestsUsed": 1,
            "countIsEstimate": False,
            "identityVerified": False,
            "notice": "Verify every lead before use.",
        }

        result = azureJobEndpoints.external_candidate_search(
            domain="law",
            jd_id="85",
            source="courtlistener",
            top_k=5,
            titles="",
            practice_areas="",
            locations="",
            region="",
            min_years=0,
            strict_locations=None,
            scroll_token="",
        )

        search.assert_called_once()
        self.assertEqual(result["source"], "courtlistener")
        self.assertTrue(result["searchUsesJobDescription"])
        self.assertEqual(result["results"][0]["result_type"], "court_attorney_lead")
        self.assertEqual(result["results"][0]["name"], "Alex Morgan")
        self.assertEqual(result["results"][0]["score"], 0)
        self.assertTrue(result["results"][0]["profile_data"]["discovered_from_jd"])
        self.assertEqual(result["sourceAudit"]["queryMode"], "jd_court_attorney_discovery")
        self.assertEqual(result["sourceAudit"]["matchingDockets"], 921)
        self.assertEqual(result["sourceAudit"]["docketsReviewed"], 20)
        self.assertFalse(result["sourceAudit"]["identityVerified"])
        self.assertEqual(result["pagination"]["costLabel"], "1 API request")

    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_court_lead_profile_validation_merges_likely_exact_match_without_scoring(
        self, get_job, enrich
    ):
        get_job.return_value = (
            self.jd,
            ["Professional Liability", "Civil Litigation", "Depositions"],
        )
        enrich.return_value = {
            "status": 200,
            "likelihood": 9,
            "matched": {"name": "Sample Attorney", "region": "California"},
            "data": {
                "id": "pdl-123",
                "full_name": "Sample Q Attorney",
                "linkedin_url": "linkedin.com/in/sample-attorney",
                "job_title": "Professional Liability Associate",
                "job_company_name": "Example LLP",
                "job_last_verified": "2026-06-01",
                "location_name": "Los Angeles, California, United States",
                "location_region": "California",
                "location_country": "United States",
                "inferred_years_experience": 7,
                "skills": ["Professional Liability", "Civil Litigation"],
                "summary": "Civil litigation attorney.",
            },
        }
        candidate = {
            "source": "courtlistener",
            "source_label": "CourtListener / RECAP",
            "source_id": "courtlistener-attorney:sample-attorney",
            "result_type": "court_attorney_lead",
            "name": "Sample Attorney",
            "title": "Attorney listed in matching court records",
            "company": "",
            "location": "California Central District Court",
            "profile_url": "https://www.courtlistener.com/docket/101/example/",
            "score": 0,
            "verification": {"identity_status": "not_verified"},
            "profile_data": {"evidence_count": 2, "evidence_records": []},
        }

        result = azureJobEndpoints.external_court_lead_validate_profile(
            {
                "domain": "law",
                "jd_id": "85",
                "criteria": {"region": "California"},
                "candidate": candidate,
            }
        )

        enrich.assert_called_once_with(
            name="Sample Attorney",
            region="California",
            country="United States",
            min_likelihood=8,
            required="linkedin_url",
        )
        updated = result["candidate"]
        self.assertEqual(updated["profile_validation"]["status"], "confirmed_profile_match")
        self.assertEqual(updated["professional_profile_url"], "https://www.linkedin.com/in/sample-attorney")
        self.assertEqual(updated["profile_url"], candidate["profile_url"])
        self.assertEqual(updated["company"], "Example LLP")
        self.assertEqual(updated["years_experience"], 7)
        self.assertEqual(updated["score"], 0)
        self.assertFalse(result["usedForCandidateScoring"])
        self.assertFalse(result["linkedinScraped"])

    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_court_lead_profile_validation_does_not_merge_name_mismatch(self, get_job, enrich):
        get_job.return_value = (self.jd, ["Professional Liability"])
        enrich.return_value = {
            "status": 200,
            "likelihood": 10,
            "data": {
                "id": "pdl-other",
                "full_name": "Different Person",
                "linkedin_url": "linkedin.com/in/different-person",
                "job_title": "Attorney",
                "job_company_name": "Other LLP",
            },
        }
        candidate = {
            "source": "courtlistener",
            "result_type": "court_attorney_lead",
            "name": "Sample Attorney",
            "title": "Attorney listed in matching court records",
            "company": "",
            "profile_url": "https://www.courtlistener.com/docket/101/example/",
            "score": 0,
        }

        result = azureJobEndpoints.external_court_lead_validate_profile(
            {"domain": "law", "jd_id": "85", "candidate": candidate}
        )

        updated = result["candidate"]
        self.assertEqual(updated["profile_validation"]["status"], "needs_review")
        self.assertNotIn("professional_profile_url", updated)
        self.assertEqual(updated["company"], "")
        self.assertEqual(updated["score"], 0)
        self.assertFalse(updated["profile_validation"]["exactNameMatch"])

    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    def test_court_lead_profile_validation_reuses_completed_result_without_new_credit(self, enrich):
        validation = {
            "status": "confirmed_profile_match",
            "provider": "People Data Labs Person Enrichment",
            "requestsUsed": 1,
            "successfulEnrichmentCredits": 1,
        }
        candidate = {
            "source": "courtlistener",
            "result_type": "court_attorney_lead",
            "name": "Sample Attorney",
            "score": 0,
            "profile_validation": validation,
        }

        result = azureJobEndpoints.external_court_lead_validate_profile(
            {"domain": "law", "jd_id": "85", "candidate": candidate}
        )

        enrich.assert_not_called()
        self.assertTrue(result["reused"])
        self.assertEqual(result["candidate"]["profile_validation"], validation)
        self.assertEqual(result["candidate"]["score"], 0)

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
        with self.assertRaisesRegex(Exception, "research-only"):
            azureJobEndpoints.external_candidate_import(
                {
                    "domain": "law",
                    "candidate": {
                        "source": "courtlistener",
                        "result_type": "court_record_evidence",
                    },
                }
            )

    @patch("azureUtils.routes.azureJobEndpoints.candidates.uploadProfile")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.findTemporaryExternalProfile")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    def test_selected_pdl_profile_is_enriched_rematched_and_imported_with_history(
        self,
        get_job,
        enrich,
        find_temp,
        upload,
    ):
        get_job.return_value = (self.jd, ["Professional Liability", "Civil Litigation", "Depositions"])
        find_temp.return_value = None
        enrich.return_value = {
            "status": 200,
            "likelihood": 9,
            "data": {
                "id": "pdl-123",
                "first_name": "Sample",
                "last_name": "Attorney",
                "linkedin_url": "linkedin.com/in/sample-attorney",
                "job_title": "Associate Attorney",
                "job_company_name": "Example LLP",
                "job_last_verified": "2026-06-01",
                "location_name": "Los Angeles, California, United States",
                "location_locality": "Los Angeles",
                "location_region": "California",
                "location_country": "United States",
                "inferred_years_experience": 8,
                "summary": "Professional liability and civil litigation attorney.",
                "skills": ["Professional Liability", "Civil Litigation", "Depositions"],
                "experience": [
                    {
                        "title": "Associate Attorney",
                        "company": {"name": "Example LLP"},
                        "start_date": "2021-01",
                        "end_date": None,
                        "is_primary": True,
                        "summary": "Managed professional liability matters and depositions.",
                    }
                ],
                "education": [
                    {"school": {"name": "Example Law School"}, "degrees": ["JD"]}
                ],
                "certifications": ["California Bar"],
            },
        }
        upload.return_value = {"status": "success", "personid": 501, "name": "Sample Attorney"}

        result = azureJobEndpoints.external_candidate_import(
            {
                "domain": "law",
                "source": "pdl",
                "jd_id": "85",
                "criteria": {
                    "titles": ["Associate Attorney", "Attorney"],
                    "practiceAreas": ["Professional Liability", "Civil Litigation"],
                    "locations": ["Los Angeles"],
                    "region": "California",
                    "minYears": 3,
                    "strictLocations": True,
                },
                "candidate": {
                    "source": "pdl",
                    "source_label": "People Data Labs",
                    "source_id": "pdl-123",
                    "name": "Sample Attorney",
                    "profile_url": "https://www.linkedin.com/in/sample-attorney",
                    "skills": ["Civil Litigation"],
                    "top_matches": ["Civil Litigation"],
                },
            }
        )

        enrich.assert_called_once_with(
            profile="https://www.linkedin.com/in/sample-attorney",
            pdl_id="pdl-123",
        )
        upload_args = upload.call_args.kwargs
        self.assertEqual(upload_args["candidateCity"], "Los Angeles")
        self.assertEqual(upload_args["candidateState"], "California")
        self.assertEqual(len(upload_args["portfolioExperiences"]), 1)
        clean_description, metadata = azureJobEndpoints.candidates.splitExternalProfileDescription(
            upload_args["candidateDescription"]
        )
        self.assertIn("Temporary external profile", clean_description)
        self.assertEqual(metadata["enrichment"]["status"], "completed")
        self.assertEqual(metadata["enrichment"]["likelihood"], 9)
        self.assertGreater(metadata["match"]["score"], 0)
        self.assertEqual(metadata["education"][0]["school"], "Example Law School")
        self.assertEqual(result["personid"], 501)
        self.assertEqual(result["enrichment"]["creditsUsed"], 1)

    @patch("azureUtils.routes.azureJobEndpoints.candidates.uploadProfile")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.findTemporaryExternalProfile")
    def test_duplicate_temp_profile_skips_paid_enrichment(self, find_temp, enrich, upload):
        find_temp.return_value = {
            "personid": 501,
            "name": "Sample Attorney",
            "temporaryProfile": True,
            "duplicate": True,
        }

        result = azureJobEndpoints.external_candidate_import(
            {
                "domain": "law",
                "source": "pdl",
                "candidate": {
                    "source": "pdl",
                    "source_id": "pdl-123",
                    "profile_url": "https://www.linkedin.com/in/sample-attorney",
                },
            }
        )

        self.assertTrue(result["duplicate"])
        self.assertTrue(result["enrichmentSkipped"])
        enrich.assert_not_called()
        upload.assert_not_called()

    @patch("azureUtils.routes.azureJobEndpoints.candidates.uploadProfile")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.findTemporaryExternalProfile")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    def test_pdl_no_match_does_not_create_thin_temp_profile(self, enrich, find_temp, upload):
        find_temp.return_value = None
        enrich.return_value = {"status": 404, "likelihood": 0, "data": None, "matched": {}}

        with self.assertRaises(HTTPException) as error:
            azureJobEndpoints.external_candidate_import(
                {
                    "domain": "law",
                    "source": "pdl",
                    "criteria": {"minYears": "not-a-number"},
                    "candidate": {
                        "source": "pdl",
                        "source_id": "pdl-missing",
                        "profile_url": "https://www.linkedin.com/in/missing-attorney",
                    },
                }
            )

        self.assertEqual(error.exception.status_code, 404)
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
