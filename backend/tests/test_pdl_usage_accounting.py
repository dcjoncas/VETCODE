import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from azureUtils.routes import azureJobEndpoints
from peopleDataLabs import peopleSearch


def provider_response(status=200, headers=None, rows=None):
    response = Mock(status_code=status)
    response.headers = headers or {}
    response.json.return_value = {"status": status, "total": 125, "data": rows or [], "scroll_token": "next-page"}
    return response


class PdlUsageAccountingTests(unittest.TestCase):
    def setUp(self):
        self.api_key = patch.dict(os.environ, {"PDL_API_KEY": "usage-test-key"})
        self.api_key.start()
        self.addCleanup(self.api_key.stop)
        self.post = patch("peopleDataLabs.peopleSearch.requests.post").start()
        self.addCleanup(patch.stopall)

    def search(self, response):
        self.post.return_value = response
        return peopleSearch.searchSkills(["Python"], size=5)

    def test_provider_charge_is_independent_of_records_and_account_balance(self):
        result = self.search(provider_response(headers={
            "X-Call-Credits-Spent": "3", "X-Call-Credits-Type": "search",
            "X-TotalLimit-Remaining": "42", "X-TotalLimit-Purchased-Remaining": "40",
            "X-TotalLimit-Overages-Remaining": "2", "X-RateLimit-Remaining": '{"minute": 99}',
            "X-Api-Key": "must-not-be-retained",
        }, rows=[{"id": "one"}, {"id": "two"}]))
        audit = azureJobEndpoints._pdl_source_audit(result)
        usage = audit["providerUsage"]
        self.assertEqual(usage["creditsUsed"], 3)
        self.assertEqual(usage["accountRemainingCredits"], 42)
        self.assertEqual(usage["purchasedRemainingCredits"], 40)
        self.assertEqual(usage["overageRemainingCredits"], 2)
        self.assertEqual(usage["creditType"], "search")
        self.assertEqual(usage["status"], "reported")
        self.assertEqual(usage["balanceStatus"], "reported")
        self.assertEqual(audit["estimatedCreditsUsed"], 2)
        self.assertIn("estimate", audit["estimatedCreditsBasis"])
        self.assertEqual(result["total"], 125)
        self.assertEqual(result["scroll_token"], "next-page")
        self.assertNotIn("must-not-be-retained", json.dumps(result))
        self.assertNotIn("rateLimit", json.dumps(usage))

    def test_missing_headers_are_unavailable_not_zero_or_record_estimate(self):
        result = self.search(provider_response(rows=[{"id": "one"}]))
        usage = result["provider_usage"]
        self.assertIsNone(usage["creditsUsed"])
        self.assertIsNone(usage["accountRemainingCredits"])
        self.assertEqual(usage["status"], "unavailable")
        self.assertEqual(usage["requests"], 1)
        self.assertEqual(usage["reportedRequests"], 0)
        self.assertEqual(azureJobEndpoints._pdl_source_audit(result)["estimatedCreditsUsed"], 1)

    def test_zero_values_are_real_reported_values(self):
        result = self.search(provider_response(status=404, headers={
            "x-call-credits-spent": "0", "x-totallimit-remaining": "0",
        }))
        self.assertEqual(result["data"], [])
        self.assertIsNone(result["scroll_token"])
        self.assertEqual(result["provider_usage"]["creditsUsed"], 0)
        self.assertEqual(result["provider_usage"]["accountRemainingCredits"], 0)
        self.assertEqual(result["provider_usage"]["status"], "reported")

    def test_invalid_header_counts_and_request_rate_limits_are_not_balances(self):
        for value in ["null", "unlimited", "-1", "NaN", "Infinity", "3.5", True]:
            with self.subTest(value=value):
                result = self.search(provider_response(headers={
                    "x-call-credits-spent": value, "x-totallimit-remaining": value,
                    "x-ratelimit-remaining": '{"minute": 100}',
                }))
                self.assertIsNone(result["provider_usage"]["creditsUsed"])
                self.assertIsNone(result["provider_usage"]["accountRemainingCredits"])

    def test_credit_limited_retry_sums_both_calls_and_keeps_latest_balance(self):
        self.post.side_effect = [
            provider_response(402, {"x-call-credits-spent": "0", "x-totallimit-remaining": "1"}),
            provider_response(200, {"x-call-credits-spent": "1", "x-totallimit-remaining": "0"}, [{"id": "one"}]),
        ]
        result = peopleSearch.searchSkills(["Python"], size=5)
        usage = result["provider_usage"]
        self.assertEqual(usage["creditsUsed"], 1)
        self.assertEqual(usage["requests"], 2)
        self.assertEqual(usage["reportedRequests"], 2)
        self.assertEqual(usage["accountRemainingCredits"], 0)
        self.assertEqual(result["requested_size"], 5)
        self.assertEqual(result["effective_size"], 1)
        self.assertTrue(result["credit_limited"])

    def test_partial_retry_usage_never_claims_a_complete_total(self):
        self.post.side_effect = [provider_response(402), provider_response(200, {"x-call-credits-spent": "1"})]
        usage = peopleSearch.searchSkills(["Python"], size=5)["provider_usage"]
        self.assertEqual(usage["status"], "partial")
        self.assertIsNone(usage["creditsUsed"])
        self.assertEqual(usage["reportedCreditsUsed"], 1)
        self.assertEqual(usage["reportedRequests"], 1)

    def test_failed_search_keeps_billing_headers(self):
        self.post.return_value = provider_response(402, {
            "x-call-credits-spent": "0", "x-totallimit-remaining": "0",
        })
        with self.assertRaises(peopleSearch.PeopleDataLabsError) as raised:
            peopleSearch.searchSkills(["Python"], size=5)
        response = azureJobEndpoints._provider_search_error_response("pdl", raised.exception, 5)
        audit = json.loads(response.body)["sourceAudit"]
        self.assertEqual(audit["providerUsage"]["creditsUsed"], 0)
        self.assertEqual(audit["providerUsage"]["accountRemainingCredits"], 0)
        self.assertEqual(audit["providerUsage"]["requests"], 2)

    def test_timeout_does_not_claim_zero_spend(self):
        self.post.side_effect = requests.Timeout()
        with self.assertRaises(peopleSearch.PeopleDataLabsError) as raised:
            peopleSearch.searchSkills(["Python"], size=5)
        response = azureJobEndpoints._provider_search_error_response("pdl", raised.exception, 5)
        usage = json.loads(response.body)["sourceAudit"]["providerUsage"]
        self.assertIsNone(usage["creditsUsed"])
        self.assertEqual(usage["requests"], 1)
        self.assertEqual(usage["status"], "unavailable")

    def test_invalid_json_still_retains_provider_charge(self):
        response = provider_response(headers={"x-call-credits-spent": "5"})
        response.json.side_effect = ValueError()
        with self.assertRaises(peopleSearch.PeopleDataLabsError) as raised:
            self.search(response)
        self.assertEqual(raised.exception.provider_usage["creditsUsed"], 5)

    def test_round_aggregation_sums_charges_but_never_sums_balances(self):
        first = self.search(provider_response(headers={"x-call-credits-spent": "5", "x-totallimit-remaining": "20"}))
        second = self.search(provider_response(headers={"x-call-credits-spent": "2", "x-totallimit-remaining": "18"}))
        calls = first["provider_usage"]["calls"] + second["provider_usage"]["calls"]
        usage = peopleSearch.summarize_search_usage(calls)
        self.assertEqual(usage["creditsUsed"], 7)
        self.assertEqual(usage["accountRemainingCredits"], 18)
        self.assertEqual(usage["requests"], 2)

    def test_old_balance_is_not_reused_when_latest_call_has_no_balance_header(self):
        usage = peopleSearch.summarize_search_usage([
            {"creditsUsed": 5, "accountRemainingCredits": 20}, {"creditsUsed": 2},
        ])
        self.assertEqual(usage["creditsUsed"], 7)
        self.assertIsNone(usage["accountRemainingCredits"])
        self.assertEqual(usage["balanceStatus"], "unavailable")

    def test_cached_search_is_free_now_and_original_balance_is_historical(self):
        original = self.search(provider_response(headers={"x-call-credits-spent": "2", "x-totallimit-remaining": "18"}, rows=[{}, {}]))
        original_audit = azureJobEndpoints._pdl_source_audit(original)
        response = azureJobEndpoints._saved_search_cache_hit({"response": {
            "source": "pdl", "sourceAudit": original_audit, "results": [{"name": "Example"}],
        }})
        audit = response["sourceAudit"]
        self.assertEqual(audit["providerUsage"]["creditsUsed"], 0)
        self.assertEqual(audit["providerUsage"]["requests"], 0)
        self.assertEqual(audit["providerUsage"]["source"], "local_cache")
        self.assertIsNone(audit["providerUsage"]["accountRemainingCredits"])
        self.assertEqual(audit["originalProviderUsage"]["creditsUsed"], 2)
        self.assertEqual(audit["originalProviderUsage"]["accountRemainingCredits"], 18)
        self.assertEqual(audit["originalProviderUsage"]["balanceStatus"], "historical")
        self.assertEqual(audit["originalEstimatedCreditsUsed"], 2)
        self.assertEqual(original_audit["providerUsage"]["balanceStatus"], "reported")

    def test_saved_multi_page_history_preserves_complete_original_usage(self):
        pages = []
        for index, balance in enumerate([20, 18]):
            result = self.search(provider_response(headers={"x-call-credits-spent": "2", "x-totallimit-remaining": str(balance)}, rows=[{}, {}]))
            pages.append({"response": {"source": "pdl", "sourceAudit": azureJobEndpoints._pdl_source_audit(result), "results": [{"source_id": str(index)}]}})
        combined = azureJobEndpoints._combined_saved_search_response({"pages": pages})
        audit = combined["sourceAudit"]
        self.assertEqual(audit["providerUsage"]["creditsUsed"], 0)
        self.assertEqual(audit["originalProviderUsage"]["creditsUsed"], 4)
        self.assertEqual(audit["originalProviderUsage"]["accountRemainingCredits"], 18)
        self.assertEqual(audit["originalProviderUsage"]["balanceStatus"], "historical")
        self.assertEqual(audit["originalEstimatedCreditsUsed"], 4)
        self.assertEqual(len(combined["results"]), 2)

    def test_old_saved_search_does_not_turn_its_estimate_into_reported_usage(self):
        cached = azureJobEndpoints._saved_search_cache_hit({"response": {
            "source": "pdl", "sourceAudit": {"estimatedCreditsUsed": 5},
        }})
        audit = cached["sourceAudit"]
        self.assertEqual(audit["providerUsage"]["creditsUsed"], 0)
        self.assertIsNone(audit["originalProviderUsage"]["creditsUsed"])
        self.assertEqual(audit["originalEstimatedCreditsUsed"], 5)


if __name__ == "__main__":
    unittest.main()
