import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from azureUtils.routes import azureJobEndpoints as routes


def usage(credits=2):
    return routes.peopleDataLabs.summarize_search_usage([{
        "creditsUsed": credits, "creditType": "enrich",
        "accountRemainingCredits": 123, "observedAt": "2026-09-10T00:00:00Z",
    }])


class PdlEnrichmentAccountingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.candidate = {"source": "pdl", "source_id": "sample", "name": "Sample Developer",
                          "profile_url": "https://www.linkedin.com/in/sample-developer", "skills": []}
        self.response = {"status": 200, "likelihood": 9, "data": {
            "id": "sample", "first_name": "Sample", "last_name": "Developer", "job_title": "Developer",
            "linkedin_url": self.candidate["profile_url"], "skills": [],
        }, "provider_usage": usage()}
        self.enrich = self.mock("peopleDataLabs.enrichPerson", return_value=self.response)
        self.mock("candidates.findTemporaryExternalProfile", return_value=None)
        self.upload = self.mock("candidates.uploadProfile", side_effect=lambda **kwargs: {"personid": 7})
        self.stored = {"personid": 7, "name": "Sample Developer", "profileUrl": self.candidate["profile_url"],
                       "externalProfile": {"source": "People Data Labs", "enrichment": {"status": "not_requested"}}}
        self.mock("candidates.getTemporaryExternalProfileForEnrichment", return_value=self.stored)
        self.apply = self.mock("candidates.applyTemporaryExternalProfileEnrichment", side_effect=lambda *args: {"personid": 7})
        self.mock("coreSignal.configured", return_value=False)

    def mock(self, target, **kwargs):
        return self.stack.enter_context(patch("azureUtils.routes.azureJobEndpoints." + target, **kwargs))

    def result(self):
        return routes.external_candidate_enrich_result({"domain": "dev", "candidate": copy.deepcopy(self.candidate)})

    def imported(self):
        return routes.external_candidate_import({"domain": "dev", "candidate": copy.deepcopy(self.candidate), "enrich_contacts": True})

    def saved(self):
        return routes.external_candidate_enrich_temp_profile("7", {"domain": "dev"})

    def court(self):
        return routes.external_court_lead_validate_profile({"domain": "law", "candidate": {
            "source": "courtlistener", "result_type": "court_attorney_lead", "name": "Sample Developer",
        }})

    def test_reported_charges_and_balances_propagate_across_all_fresh_paths(self):
        for action in [self.result, self.imported, self.saved, self.court]:
            with self.subTest(action=action.__name__):
                result = action()
                self.assertEqual(result["creditsUsed"], 2)
                self.assertEqual(result["providerUsage"]["accountRemainingCredits"], 123)
                self.assertIsNone(result["estimatedCreditsUsed"])
        metadata = routes.candidates.splitExternalProfileDescription(self.upload.call_args.kwargs["candidateDescription"])[1]
        self.assertEqual(metadata["enrichment"]["providerUsage"], usage())
        self.assertEqual(self.apply.call_args.args[3]["enrichment"]["providerUsage"], usage())

    def test_absent_headers_never_claim_actual_one_or_zero_for_success(self):
        self.response.pop("provider_usage")
        for action in [self.result, self.imported, self.saved, self.court]:
            with self.subTest(action=action.__name__):
                result = action()
                self.assertIsNone(result["creditsUsed"])
                self.assertEqual(result["estimatedCreditsUsed"], 1)
                self.assertEqual(result["providerUsage"]["status"], "unavailable")

    def test_reported_zero_is_preserved_not_replaced_with_an_estimate(self):
        self.response["provider_usage"] = usage(0)
        result = self.result()
        self.assertEqual(result["creditsUsed"], 0)
        self.assertIsNone(result["estimatedCreditsUsed"])

    def test_404_preserves_usage_on_errors_and_court_no_match_result(self):
        self.response.update(status=404, data=None, provider_usage=usage(0))
        for action in [self.result, self.imported, self.saved]:
            with self.subTest(action=action.__name__):
                with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
                    action()
                self.assertEqual(raised.exception.status_code, 404)
                self.assertIsInstance(raised.exception.detail, str)
                self.assertEqual(raised.exception.accounting["creditsUsed"], 0)
        result = self.court()
        self.assertEqual(result["profileValidation"]["status"], "no_match")
        self.assertEqual(result["creditsUsed"], 0)
        self.upload.assert_not_called()
        self.apply.assert_not_called()

    def test_provider_errors_preserve_sanitized_usage_and_existing_status_codes(self):
        self.enrich.side_effect = routes.peopleDataLabs.PeopleDataLabsError("Synthetic failure", 429, usage(0))
        for action in [self.result, self.imported, self.saved, self.court]:
            with self.subTest(action=action.__name__):
                with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
                    action()
                self.assertEqual(raised.exception.status_code, 502 if action == self.imported else 429)
                self.assertIn("Synthetic failure", raised.exception.detail)
                self.assertEqual(raised.exception.accounting["providerUsage"], usage(0))

    def test_404_without_headers_stays_unknown_not_inferred_free(self):
        self.response.update(status=404, data=None)
        self.response.pop("provider_usage")
        with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
            self.result()
        self.assertIsNone(raised.exception.accounting["creditsUsed"])
        self.assertIsNone(raised.exception.accounting["estimatedCreditsUsed"])
        self.assertIsNone(self.court()["creditsUsed"])

    def test_identity_rejected_after_success_keeps_reported_charge_without_saving(self):
        self.response["data"]["first_name"] = "Different"
        with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
            self.saved()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.accounting["creditsUsed"], 2)
        self.apply.assert_not_called()

    def test_reuse_has_zero_new_cost_and_separate_historical_balance(self):
        original = {"status": "completed", "profileVersion": 2, "provider": "People Data Labs Person Enrichment", "providerUsage": usage(), "creditsUsed": 2}
        self.candidate.update(external_enrichment=original, professional_enrichment_complete=True)
        self.stored["externalProfile"]["enrichment"] = original
        for action in [self.result, self.imported, self.saved]:
            with self.subTest(action=action.__name__):
                result = action()
                self.assertEqual(result["creditsUsed"], 0)
                self.assertEqual(result["providerUsage"]["status"], "not_called")
                self.assertIsNone(result["providerUsage"]["accountRemainingCredits"])
                self.assertEqual(result["originalProviderUsage"]["accountRemainingCredits"], 123)
                self.assertEqual(result["originalProviderUsage"]["balanceStatus"], "historical")
        self.enrich.assert_not_called()
        self.assertEqual(original["providerUsage"]["balanceStatus"], "reported")

    def test_court_validation_cache_and_import_do_not_count_the_same_request_again(self):
        first = self.court()
        self.enrich.reset_mock()
        cached = routes.external_court_lead_validate_profile({"domain": "law", "candidate": first["candidate"]})
        imported = routes.external_candidate_import({"domain": "law", "candidate": first["candidate"]})
        self.assertEqual(cached["creditsUsed"], 0)
        self.assertEqual(imported["providerCreditsUsed"], 0)
        self.enrich.assert_not_called()

    def test_new_pdl_discovery_import_reports_no_call_or_historical_balance(self):
        result = routes.external_candidate_import({"domain": "dev", "candidate": self.candidate})
        self.assertEqual(result["providerCreditsUsed"], 0)
        self.assertEqual(result["providerUsage"]["requests"], 0)
        self.assertEqual(result["originalProviderUsage"]["requests"], 0)
        self.enrich.assert_not_called()

    def test_import_http_storage_failure_preserves_status_detail_headers_and_completed_usage(self):
        original = routes.HTTPException(409, "Synthetic storage conflict", headers={"Retry-After": "4"})
        self.upload.side_effect = original
        with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
            self.imported()
        self.assertEqual(raised.exception.status_code, original.status_code)
        self.assertEqual(raised.exception.detail, original.detail)
        self.assertEqual(raised.exception.headers, original.headers)
        self.assertEqual(raised.exception.accounting["providerUsage"], usage())
        self.assertEqual(raised.exception.accounting["creditsUsed"], 2)
        self.enrich.assert_called_once()

    def test_import_generic_storage_failure_keeps_existing_detail_plus_usage(self):
        self.upload.side_effect = RuntimeError("Synthetic save failure")
        response = self.imported()
        data = json.loads(response.body)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["detail"], "Unable to create profile from external candidate: Synthetic save failure")
        self.assertEqual(data["providerUsage"], usage())
        self.assertEqual(data["creditsUsed"], 2)

    def test_saved_http_storage_failure_preserves_status_detail_headers_and_completed_usage(self):
        original = routes.HTTPException(503, "Synthetic storage temporarily unavailable", headers={"Retry-After": "2"})
        self.apply.side_effect = original
        with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
            self.saved()
        self.assertEqual(raised.exception.status_code, original.status_code)
        self.assertEqual(raised.exception.detail, original.detail)
        self.assertEqual(raised.exception.headers, original.headers)
        self.assertEqual(raised.exception.accounting["creditsUsed"], 2)
        self.enrich.assert_called_once()

    def test_saved_unexpected_storage_error_does_not_expose_internals_or_invent_usage(self):
        self.apply.side_effect = RuntimeError("Private synthetic database internals")
        self.response.pop("provider_usage")
        with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
            self.saved()
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Internal Server Error")
        self.assertIsNone(raised.exception.accounting["creditsUsed"])
        self.assertEqual(raised.exception.accounting["estimatedCreditsUsed"], 1)

    def test_non_pdl_and_no_enrichment_import_storage_failures_remain_unchanged(self):
        self.upload.side_effect = RuntimeError("Synthetic save failure")
        for source in ["github", "pdl"]:
            with self.subTest(source=source):
                response = routes.external_candidate_import({"domain": "dev", "candidate": {**self.candidate, "source": source}})
                self.assertEqual(response.status_code, 500)
                self.assertEqual(json.loads(response.body), {"detail": "Unable to create profile from external candidate: Synthetic save failure"})
        self.enrich.assert_not_called()

    def test_coresignal_saved_storage_http_failure_is_not_reclassified_as_pdl(self):
        self.stored["externalProfile"]["source"] = "Coresignal"
        self.mock("coreSignal.configured", return_value=True)
        self.mock("coreSignal.collect_person", return_value={"status": 200, "data": {}, "credits_used": 10})
        self.mock("_coresignal_collected_row", return_value=copy.deepcopy(self.candidate))
        original = routes.HTTPException(503, "Synthetic Coresignal storage failure")
        self.apply.side_effect = original
        with self.assertRaises(routes.HTTPException) as raised:
            self.saved()
        self.assertIs(raised.exception, original)
        self.enrich.assert_not_called()


if __name__ == "__main__":
    unittest.main()
