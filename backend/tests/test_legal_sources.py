import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from azureUtils.routes import azureJobEndpoints
from legalSources import coreSignal, courtListener


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
        self.assertEqual(result["credits_used"], 10)

    @patch.dict(os.environ, {"CORESIGNAL_API_KEY": "core-test-key"}, clear=False)
    @patch("legalSources.coreSignal.requests.get")
    def test_coresignal_collect_is_keyed_and_accepts_profile_url(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "id": 101,
            "full_name": "Sample Attorney",
            "profile_url": "https://www.linkedin.com/in/sample-attorney",
            "experience": [],
        }
        get.return_value = response

        result = coreSignal.collect_person(
            profile_url="https://www.linkedin.com/in/sample-attorney",
        )

        request = get.call_args
        self.assertEqual(request.kwargs["headers"]["apikey"], "core-test-key")
        self.assertIn("https%3A%2F%2Fwww.linkedin.com%2Fin%2Fsample-attorney", request.args[0])
        self.assertEqual(result["data"]["id"], 101)
        self.assertEqual(result["dataset"], "base")
        self.assertEqual(result["matched_input"], "profile_url")
        self.assertEqual(result["credits_used"], 10)

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
            "CORESIGNAL_EMPLOYEE_DATASET": "multi_source",
            "COURTLISTENER_API_TOKEN": "court-key",
        },
        clear=False,
    )
    def test_provider_status_reports_readiness_without_secrets(self):
        result = azureJobEndpoints.external_provider_status()

        self.assertTrue(result["providers"]["coresignal"]["ready"])
        self.assertEqual(result["providers"]["coresignal"]["employeeDataset"], "multi_source")
        self.assertEqual(result["providers"]["coresignal"]["searchCreditsPerRequest"], 10)
        self.assertEqual(result["providers"]["coresignal"]["collectionCreditsPerRequest"], 20)
        self.assertNotIn("brave", result["providers"])
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
            "credits_used": 10,
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
        self.assertEqual(result["sourceAudit"]["estimatedCreditsUsed"], 10)
        self.assertEqual(result["pagination"]["nextScrollToken"], "2")
        self.assertEqual(result["pagination"]["costLabel"], "10 search credits")

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

    def test_removed_public_web_source_is_rejected_and_public_evidence_import_is_blocked(self):
        for domain in ("dev", "engineer", "law", "dental"):
            self.assertFalse(
                azureJobEndpoints._external_source_allowed_for_domain("brave", domain)
            )
        with self.assertRaisesRegex(Exception, "not available"):
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

    def test_court_attorney_lead_requires_explicit_unverified_identity_acknowledgment(self):
        with self.assertRaises(HTTPException) as error:
            azureJobEndpoints.external_candidate_import(
                {
                    "domain": "law",
                    "source": "courtlistener",
                    "candidate": {
                        "source": "courtlistener",
                        "result_type": "court_attorney_lead",
                        "name": "Jennifer Pafiti",
                    },
                }
            )

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("unverified", error.exception.detail)

    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.uploadProfile")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.findTemporaryExternalProfile")
    def test_court_attorney_lead_creates_unscored_temp_research_profile(
        self,
        find_temp,
        upload,
        get_job,
    ):
        get_job.return_value = (self.jd, ["Professional Liability", "Accounting Malpractice"])
        find_temp.return_value = None
        upload.return_value = {"status": "success", "personid": 777, "name": "Jennifer Pafiti"}
        candidate = {
            "source": "courtlistener",
            "source_label": "CourtListener / RECAP",
            "result_type": "court_attorney_lead",
            "source_id": "courtlistener-attorney:jennifer-pafiti",
            "name": "Jennifer Pafiti",
            "title": "Attorney listed in matching court records",
            "location": "District Court, C.D. California, District Court, N.D. California",
            "profile_url": "https://www.courtlistener.com/docket/123/example/",
            "score": 0,
            "top_matches": ["Court query: accounting malpractice", "Court query: professional liability"],
            "score_details": {
                "band": "Court-data lead",
                "missing": [
                    "Current employer and role",
                    "3+ years experience",
                    "Current location",
                    "California Bar standing",
                ],
            },
            "verification": {
                "identity_status": "not_verified",
                "california_bar_status": "not_verified",
                "current_employment": "not_verified",
            },
            "profile_validation": {
                "status": "no_match",
                "provider": "People Data Labs Person Enrichment",
                "requestsUsed": 1,
                "successfulEnrichmentCredits": 0,
                "notice": "No LinkedIn-linked PDL profile met the exact-name and California lookup threshold.",
            },
            "profile_data": {
                "evidence_count": 4,
                "matched_practice_areas": ["Accounting Malpractice", "Professional Liability"],
                "query_practice_areas": ["Accounting Malpractice", "Professional Liability"],
                "courts": ["District Court, C.D. California", "District Court, N.D. California"],
                "evidence_records": [
                    {
                        "title": "Douglas Bray v. Rocket Lab USA, Inc.",
                        "court": "District Court, C.D. California",
                        "docketNumber": "2:24-cv-00123",
                        "url": "https://www.courtlistener.com/docket/123/example/",
                    }
                ],
            },
        }

        result = azureJobEndpoints.external_candidate_import(
            {
                "domain": "law",
                "source": "courtlistener",
                "jd_id": "85",
                "criteria": {"minYears": 3, "region": "California"},
                "identity_unverified_acknowledged": True,
                "candidate": candidate,
            }
        )

        find_temp.assert_called_once_with("law", "courtlistener-attorney:jennifer-pafiti", "")
        upload_args = upload.call_args.kwargs
        self.assertEqual(upload_args["skills"], [])
        self.assertIsNone(upload_args["linkedInUrl"])
        self.assertIsNone(upload_args["candidateCity"])
        self.assertIsNone(upload_args["candidateState"])
        self.assertIsNone(upload_args["candidateCountry"])
        self.assertEqual(upload_args["candidateTitle"], "Court-record attorney lead - identity unverified")
        self.assertEqual(upload_args["portfolioExperiences"], [])
        clean_description, metadata = azureJobEndpoints.candidates.splitExternalProfileDescription(
            upload_args["candidateDescription"]
        )
        self.assertIn("Court-record research profile", clean_description)
        self.assertEqual(metadata["recordType"], "court_attorney_lead")
        self.assertEqual(metadata["match"]["score"], 0)
        self.assertEqual(metadata["match"]["matched"], [])
        self.assertEqual(metadata["providerSkills"], [])
        self.assertEqual(metadata["verification"]["identityStatus"], "not_verified")
        self.assertEqual(metadata["profileValidation"]["status"], "no_match")
        self.assertEqual(metadata["courtEvidence"]["evidenceCount"], 4)
        self.assertEqual(metadata["courtEvidence"]["records"][0]["title"], "Douglas Bray v. Rocket Lab USA, Inc.")
        self.assertTrue(result["identityUnverified"])
        self.assertEqual(result["courtEvidence"]["evidenceCount"], 4)

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

    @patch.dict(os.environ, {"CORESIGNAL_API_KEY": ""}, clear=False)
    @patch("azureUtils.routes.azureJobEndpoints.candidates.applyTemporaryExternalProfileEnrichment")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.getTemporaryExternalProfileForEnrichment")
    def test_existing_temp_profile_is_enriched_from_stored_linkedin_url(
        self,
        get_temp,
        enrich,
        apply_enrichment,
    ):
        get_temp.return_value = {
            "personid": 2389,
            "name": "Anne Podraza",
            "profileUrl": "https://www.linkedin.com/in/anne-podraza",
            "location": {"locality": "Trenton", "region": "New Jersey", "country": "United States"},
            "externalProfile": {
                "source": "Coresignal Preview",
                "enrichment": {"status": "not_requested", "provider": "Coresignal Preview"},
            },
        }
        enrich.return_value = {
            "status": 200,
            "likelihood": 9,
            "data": {
                "id": "pdl-anne",
                "first_name": "Anne",
                "last_name": "Podraza",
                "linkedin_url": "linkedin.com/in/anne-podraza",
                "job_title": "Healthcare Consultant",
                "location_locality": "Trenton",
                "location_region": "New Jersey",
                "location_country": "United States",
                "summary": "Healthcare and leadership consultant.",
                "work_email": "anne@example.org",
                "recommended_personal_email": "anne.personal@example.net",
                "mobile_phone": "+1 609 555 0100",
                "phone_numbers": ["+1 609 555 0101"],
                "skills": ["Healthcare", "Leadership Coaching"],
                "experience": [
                    {
                        "title": {"name": "Consultant", "raw": ["consultant"]},
                        "company": {"name": "Example Health"},
                        "start_date": "2022-01",
                        "is_primary": True,
                    }
                ],
            },
        }
        apply_enrichment.return_value = {
            "status": "success",
            "personid": 2389,
            "name": "Anne Podraza",
            "profileUrl": "https://www.linkedin.com/in/anne-podraza",
        }

        result = azureJobEndpoints.external_candidate_enrich_temp_profile(
            "2389",
            {"domain": "dental"},
        )

        enrich.assert_called_once_with(
            profile="https://www.linkedin.com/in/anne-podraza",
            name="",
            locality="",
            region="",
            country="",
            min_likelihood=8,
            required="linkedin_url",
        )
        saved_candidate = apply_enrichment.call_args.args[2]
        saved_metadata = apply_enrichment.call_args.args[3]
        self.assertEqual(saved_candidate["title"], "Healthcare Consultant")
        self.assertEqual(saved_candidate["email"], "anne@example.org")
        self.assertEqual(len(saved_candidate["portfolio"]), 1)
        self.assertEqual(saved_candidate["portfolio"][0]["mainRole"], "Consultant")
        self.assertEqual(saved_metadata["source"], "Coresignal Preview")
        self.assertEqual(saved_metadata["enrichment"]["status"], "completed")
        self.assertEqual(saved_metadata["enrichment"]["profileVersion"], 2)
        self.assertEqual(saved_metadata["contact"]["primaryEmail"], "anne@example.org")
        self.assertEqual(saved_metadata["contact"]["mobilePhone"], "+1 609 555 0100")
        self.assertIn("+1 609 555 0101", saved_metadata["contact"]["phoneNumbers"])
        self.assertFalse(result["reused"])
        self.assertEqual(result["creditsUsed"], 1)
        self.assertFalse(result["linkedinScraped"])

    @patch.dict(
        os.environ,
        {
            "CORESIGNAL_API_KEY": "core-key",
            "CORESIGNAL_EMPLOYEE_DATASET": "multi_source",
        },
        clear=False,
    )
    @patch("azureUtils.routes.azureJobEndpoints.candidates.applyTemporaryExternalProfileEnrichment")
    @patch("azureUtils.routes.azureJobEndpoints.coreSignal.collect_person")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.getTemporaryExternalProfileForEnrichment")
    def test_coresignal_temp_profile_collects_full_professional_record(
        self,
        get_temp,
        pdl_enrich,
        collect,
        apply_enrichment,
    ):
        get_temp.return_value = {
            "personid": 2390,
            "name": "Anne Podraza",
            "profileUrl": "https://www.linkedin.com/in/anne-podraza",
            "location": {"locality": "Trenton", "region": "New Jersey", "country": "United States"},
            "externalProfile": {
                "source": "Coresignal profile preview",
                "sourceId": "core-anne",
                "enrichment": {"status": "not_requested", "provider": "Coresignal Preview"},
            },
        }
        collect.return_value = {
            "status": 200,
            "credits_used": 20,
            "dataset": "multi_source",
            "matched_input": "profile_url",
            "data": {
                "id": "core-anne",
                "first_name": "Anne",
                "last_name": "Podraza",
                "full_name": "Anne Podraza",
                "headline": "Healthcare and leadership consultant",
                "professional_network_url": "https://www.linkedin.com/in/anne-podraza",
                "picture_url": "https://cdn.example.org/anne.jpg",
                "summary": "Healthcare consulting and leadership coaching.",
                "location_full": "Trenton, New Jersey, United States",
                "location_city": "Trenton",
                "location_state": "New Jersey",
                "location_country": "United States",
                "checked_at": "2026-08-17T10:00:00Z",
                "connections_count": 350,
                "primary_professional_email": "anne@examplehealth.org",
                "primary_professional_email_status": "verified",
                "professional_emails_collection": [
                    {
                        "professional_email": "anne@examplehealth.org",
                        "professional_email_status": "verified",
                        "order_of_priority": 1,
                    }
                ],
                "active_experience_department": "Consulting",
                "active_experience_management_level": "Senior",
                "is_decision_maker": 1,
                "total_experience_duration_months": 96,
                "experience": [
                    {
                        "position_title": "Healthcare Consultant",
                        "company_name": "Example Health",
                        "company_industry": "Healthcare",
                        "company_size_range": "51-200 employees",
                        "company_website": "https://example.org",
                        "location": "Trenton, New Jersey",
                        "date_from": "2022-01-01",
                        "active_experience": 1,
                        "description": "Advises healthcare teams and coaches leaders.",
                    }
                ],
                "education": [
                    {
                        "institution": "Example University",
                        "program": "Master of Health Administration",
                        "date_from": "2012-01-01",
                        "date_to": "2014-06-01",
                    }
                ],
                "certifications": [
                    {"title": "Leadership Coach", "issuer": "Example Institute", "certificate_url": "https://example.org/certificate"},
                    {"title": "Old Deleted Credential", "deleted": 1},
                ],
                "languages": [{"name": "English", "proficiency": "Native"}],
                "services": ["Leadership Coaching", "Healthcare Consulting"],
                "projects": [{"title": "Clinical Team Program", "description": "Improved onboarding."}],
                "awards": [{"title": "Healthcare Leadership Award", "issuer": "Example Association"}],
                "websites": [{"name": "Portfolio", "url": "https://anne.example.org"}],
            },
        }
        apply_enrichment.return_value = {
            "status": "success",
            "personid": 2390,
            "name": "Anne Podraza",
            "profileUrl": "https://www.linkedin.com/in/anne-podraza",
        }

        result = azureJobEndpoints.external_candidate_enrich_temp_profile(
            "2390",
            {"domain": "dental"},
        )

        collect.assert_called_once_with(
            employee_id="",
            profile_url="https://www.linkedin.com/in/anne-podraza",
            dataset="multi_source",
        )
        pdl_enrich.assert_not_called()
        saved_candidate = apply_enrichment.call_args.args[2]
        saved_metadata = apply_enrichment.call_args.args[3]
        self.assertEqual(saved_candidate["title"], "Healthcare Consultant")
        self.assertEqual(saved_candidate["email"], "anne@examplehealth.org")
        self.assertEqual(saved_candidate["portfolio"][0]["companyName"], "Example Health")
        self.assertEqual(saved_metadata["source"], "Coresignal Multi-source Employee")
        self.assertEqual(saved_metadata["enrichment"]["provider"], "Coresignal Multi-source Employee Collect")
        self.assertTrue(saved_metadata["enrichment"]["contactFieldsRequested"])
        self.assertEqual(saved_metadata["contact"]["primaryEmail"], "anne@examplehealth.org")
        self.assertEqual(saved_metadata["contact"]["primaryProfessionalEmailStatus"], "verified")
        self.assertEqual(saved_metadata["education"][0]["school"], "Example University")
        self.assertEqual(saved_metadata["education"][0]["startYear"], 2012)
        self.assertIn("Leadership Coach", saved_metadata["certifications"])
        self.assertNotIn("Old Deleted Credential", saved_metadata["certifications"])
        self.assertEqual(saved_metadata["professionalDetails"]["languages"][0]["name"], "English")
        self.assertEqual(saved_metadata["professionalDetails"]["projects"][0]["title"], "Clinical Team Program")
        self.assertEqual(saved_metadata["professionalDetails"]["connectionsCount"], 350)
        self.assertEqual(saved_metadata["professionalDetails"]["currentDepartment"], "Consulting")
        self.assertEqual(saved_metadata["professionalDetails"]["currentManagementLevel"], "Senior")
        self.assertTrue(saved_metadata["professionalDetails"]["decisionMaker"])
        self.assertTrue(result["contactDataIncluded"])
        self.assertEqual(result["creditsUsed"], 20)
        self.assertFalse(result["linkedinScraped"])

    @patch("azureUtils.routes.azureJobEndpoints.candidates.applyTemporaryExternalProfileEnrichment")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.getTemporaryExternalProfileForEnrichment")
    def test_completed_pdl_temp_enrichment_is_reused_without_credit(
        self,
        get_temp,
        enrich,
        apply_enrichment,
    ):
        get_temp.return_value = {
            "personid": 2381,
            "name": "Demetria Keith",
            "profileUrl": "https://www.linkedin.com/in/demetria-keith",
            "location": {},
            "externalProfile": {
                "enrichment": {
                    "status": "completed",
                    "provider": "People Data Labs Person Enrichment",
                    "creditsUsed": 1,
                    "profileVersion": 2,
                }
            },
        }

        result = azureJobEndpoints.external_candidate_enrich_temp_profile(
            "2381",
            {"domain": "dental"},
        )

        enrich.assert_not_called()
        apply_enrichment.assert_not_called()
        self.assertTrue(result["reused"])
        self.assertEqual(result["creditsUsed"], 0)

    @patch("azureUtils.routes.azureJobEndpoints.candidates.applyTemporaryExternalProfileEnrichment")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.getTemporaryExternalProfileForEnrichment")
    def test_rich_pdl_temp_profile_is_rematched_to_selected_jd_without_credit(
        self,
        get_temp,
        get_job_skills,
        enrich,
        apply_enrichment,
    ):
        get_temp.return_value = {
            "personid": 2381,
            "name": "Demetria Keith",
            "title": "Dental Assistant",
            "description": "Temporary external profile with patient-care experience.",
            "profileUrl": "https://www.linkedin.com/in/demetria-keith",
            "location": {"locality": "Denver", "region": "Colorado", "country": "United States"},
            "externalProfile": {
                "providerSkills": ["Dental Assisting", "Patient Care"],
                "professionalEvidence": ["Digital radiography and endodontic chairside support"],
                "certifications": ["Certified Dental Assistant"],
                "enrichment": {
                    "status": "completed",
                    "provider": "People Data Labs Person Enrichment",
                    "creditsUsed": 1,
                    "profileVersion": 2,
                },
            },
        }
        get_job_skills.return_value = (
            {"job_title": "Dental Assistant"},
            ["Digital Radiography", "Endodontic Chairside", "Patient Care"],
        )
        apply_enrichment.return_value = {
            "status": "success",
            "personid": 2381,
            "name": "Demetria Keith",
            "profileUrl": "https://www.linkedin.com/in/demetria-keith",
        }

        result = azureJobEndpoints.external_candidate_enrich_temp_profile(
            "2381",
            {"domain": "dental", "jd_id": "jd-dental-1"},
        )

        enrich.assert_not_called()
        apply_enrichment.assert_called_once()
        saved_metadata = apply_enrichment.call_args.args[3]
        self.assertEqual(saved_metadata["match"]["jobId"], "jd-dental-1")
        self.assertGreater(saved_metadata["match"]["score"], 0)
        self.assertIn("Digital Radiography", saved_metadata["match"]["matched"])
        self.assertTrue(result["reused"])
        self.assertTrue(result["rematched"])
        self.assertEqual(result["creditsUsed"], 0)

    @patch("azureUtils.routes.azureJobEndpoints.linkedinResultsExport.build_linkedin_results_xlsx")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.listLinkedInEnrichedTemporaryProfiles")
    def test_linkedin_results_export_is_scoped_to_requested_workspace(self, list_profiles, build_workbook):
        rows = [{"personid": 2381, "linkedInEnriched": True}]
        list_profiles.return_value = rows
        build_workbook.return_value = b"xlsx-bytes"

        response = azureJobEndpoints.external_candidate_linkedin_results_export("dental")

        list_profiles.assert_called_once_with("dental", 500)
        build_workbook.assert_called_once_with(rows, "dental")
        self.assertEqual(
            response.media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("dental-linkedin-enriched-temp-profiles-", response.headers["content-disposition"])

    @patch("azureUtils.routes.azureJobEndpoints.candidates.applyTemporaryExternalProfileEnrichment")
    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.enrichPerson")
    @patch("azureUtils.routes.azureJobEndpoints.candidates.getTemporaryExternalProfileForEnrichment")
    def test_temp_enrichment_name_mismatch_does_not_update_profile(
        self,
        get_temp,
        enrich,
        apply_enrichment,
    ):
        get_temp.return_value = {
            "personid": 2389,
            "name": "Anne Podraza",
            "profileUrl": "https://www.linkedin.com/in/anne-podraza",
            "location": {},
            "externalProfile": {"enrichment": {"status": "not_requested"}},
        }
        enrich.return_value = {
            "status": 200,
            "likelihood": 10,
            "data": {
                "id": "pdl-other",
                "full_name": "Different Person",
                "linkedin_url": "linkedin.com/in/different-person",
            },
        }

        with self.assertRaises(HTTPException) as error:
            azureJobEndpoints.external_candidate_enrich_temp_profile(
                "2389",
                {"domain": "dental"},
            )

        self.assertEqual(error.exception.status_code, 409)
        apply_enrichment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
