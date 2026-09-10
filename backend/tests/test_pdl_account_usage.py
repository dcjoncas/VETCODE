import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from peopleDataLabs import accountUsage, peopleSearch


def response(status=400, headers=None, payload=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, close=Mock(), json=Mock(return_value=payload))


def credit_headers(product="search", remaining="49989"):
    return {
        "X-Call-Credits-Type": product,
        "X-Call-Credits-Spent": "0",
        "X-TotalLimit-Remaining": remaining,
        "X-TotalLimit-Purchased-Remaining": "49990",
        "X-TotalLimit-Overages-Remaining": "0",
        "X-Lifetime-Used": "505",
        "X-Api-Key": "must-never-appear-in-results",
        "Set-Cookie": "private-provider-cookie",
    }


class PdlAccountUsageTests(unittest.TestCase):
    def setUp(self):
        with accountUsage._cache_lock:
            accountUsage._cache_key = None
            accountUsage._cache_products.clear()
            accountUsage._cache_expires.clear()
        self.env = patch.dict(os.environ, {"PDL_API_KEY": "synthetic-test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch("requests.Session.request", side_effect=AssertionError("Unexpected network request"))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.addCleanup(lambda: self.network_mock.assert_not_called())

    @staticmethod
    def product_response(url, **kwargs):
        product = "search" if url.endswith("/search") else "enrich"
        return response(headers=credit_headers(product))

    def test_head_400_with_headers_keeps_raw_provider_values_without_monthly_inference(self):
        provider = response(headers=credit_headers())
        with patch.object(accountUsage.requests, "head", return_value=provider) as head:
            result = accountUsage._head_product("search", "synthetic-test-key")
        head.assert_called_once_with(
            "https://api.peopledatalabs.com/v5/person/search",
            headers={"Accept": "application/json", "X-Api-Key": "synthetic-test-key"},
            timeout=(3, 8), allow_redirects=False,
        )
        self.assertEqual(result["status"], "reported")
        self.assertEqual(result["httpStatus"], 400)
        self.assertEqual(result["accountRemainingCredits"], 49989)
        self.assertEqual(result["purchasedRemainingCredits"], 49990)
        self.assertEqual(result["overageRemainingCredits"], 0)
        self.assertEqual(result["lifetimeCreditsUsed"], 505)
        self.assertEqual(result["checkCreditsUsed"], 0)
        self.assertTrue(result["balanceComponentsDiffer"])
        self.assertIsNone(result["currentTermUsed"])
        self.assertIsNone(result["currentTermTotal"])
        self.assertNotIn("must-never-appear", json.dumps(result))
        self.assertNotIn("private-provider-cookie", json.dumps(result))
        provider.json.assert_not_called()
        provider.close.assert_called_once()

    def test_missing_headers_do_not_become_zero(self):
        with patch.object(accountUsage.requests, "head", return_value=response()):
            result = accountUsage._head_product("search", "synthetic-test-key")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["errorCode"], "headers_unavailable")
        for key in ("accountRemainingCredits", "purchasedRemainingCredits", "overageRemainingCredits", "lifetimeCreditsUsed", "checkCreditsUsed"):
            self.assertIsNone(result[key])

    def test_partial_and_zero_balances_remain_distinct(self):
        for headers, expected_status in [
            ({"x-lifetime-used": "0"}, "partial"),
            ({"x-totallimit-purchased-remaining": "0"}, "partial"),
            ({"x-totallimit-remaining": "0"}, "reported"),
        ]:
            with self.subTest(headers=headers), patch.object(accountUsage.requests, "head", return_value=response(headers=headers)):
                result = accountUsage._head_product("search", "synthetic-test-key")
            self.assertEqual(result["status"], expected_status)
            self.assertFalse(result["balanceComponentsDiffer"])
            self.assertIsNone(result["currentTermUsed"])
            self.assertIsNone(result["currentTermTotal"])

    def test_invalid_numeric_headers_are_unavailable(self):
        for invalid in (True, False, -1, 2.5, "-1", "1.5", "NaN", "infinity", "١٢", ""):
            with self.subTest(value=invalid), patch.object(accountUsage.requests, "head", return_value=response(headers={"x-totallimit-remaining": invalid})):
                result = accountUsage._head_product("search", "synthetic-test-key")
            self.assertIsNone(result["accountRemainingCredits"])
            self.assertEqual(result["status"], "unavailable")

    def test_authentication_failures_never_trust_credit_headers(self):
        for status in (401, 403):
            with self.subTest(status=status), patch.object(accountUsage.requests, "head", return_value=response(status, credit_headers())):
                result = accountUsage._head_product("search", "synthetic-test-key")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["errorCode"], "authentication_required")
            self.assertIsNone(result["accountRemainingCredits"])
            self.assertIsNone(result["lifetimeCreditsUsed"])

    def test_exhausted_and_rate_limited_responses_preserve_available_headers(self):
        for status, error, remaining in [(402, "credits_exhausted", "0"), (429, "rate_limited", "17")]:
            with self.subTest(status=status), patch.object(accountUsage.requests, "head", return_value=response(status, credit_headers(remaining=remaining))):
                result = accountUsage._head_product("search", "synthetic-test-key")
            self.assertEqual(result["status"], "reported")
            self.assertEqual(result["errorCode"], error)
            self.assertEqual(result["accountRemainingCredits"], int(remaining))

    def test_redirect_server_error_and_wrong_credit_pool_are_not_balances(self):
        for provider in (response(302, credit_headers()), response(503, credit_headers()), response(headers=credit_headers("enrich"))):
            with self.subTest(status=provider.status_code), patch.object(accountUsage.requests, "head", return_value=provider):
                result = accountUsage._head_product("search", "synthetic-test-key")
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["accountRemainingCredits"])

    def test_timeout_for_one_pool_does_not_discard_other_pool_or_leak_error_text(self):
        def head(url, **kwargs):
            if url.endswith("/search"):
                raise requests.Timeout("private request context")
            return response(headers=credit_headers("enrich", "84"))
        with patch.object(accountUsage.requests, "head", side_effect=head):
            result = accountUsage.get_account_usage()
        self.assertEqual(result["products"]["search"]["errorCode"], "timeout")
        self.assertIsNone(result["products"]["search"]["observedAt"])
        self.assertEqual(result["products"]["enrich"]["accountRemainingCredits"], 84)
        self.assertNotIn("private request context", json.dumps(result))

    def test_missing_key_makes_no_head_request(self):
        with patch.dict(os.environ, {"PDL_API_KEY": ""}), patch.object(accountUsage.requests, "head") as head:
            result = accountUsage.get_account_usage()
        head.assert_not_called()
        self.assertTrue(all(item["errorCode"] == "not_configured" for item in result["products"].values()))

    def test_cache_is_detached_and_only_invalidated_product_is_refreshed(self):
        with patch.object(accountUsage.requests, "head", side_effect=self.product_response) as head:
            first = accountUsage.get_account_usage()
            first["products"]["search"]["accountRemainingCredits"] = 123456789
            cached = accountUsage.get_account_usage()
            self.assertEqual(head.call_count, 2)
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["products"]["search"]["accountRemainingCredits"], 49989)
            accountUsage.invalidate_account_usage("enrich")
            refreshed = accountUsage.get_account_usage()
            self.assertEqual(head.call_count, 3)
            self.assertTrue(head.call_args.args[0].endswith("/enrich"))
            self.assertTrue(refreshed["products"]["search"]["cached"])
            self.assertFalse(refreshed["products"]["enrich"]["cached"])
            self.assertFalse(refreshed["cached"])
            accountUsage.invalidate_account_usage("search")
            accountUsage.get_account_usage()
            self.assertEqual(head.call_count, 4)
            self.assertTrue(head.call_args.args[0].endswith("/search"))

    def test_cache_expires_after_thirty_seconds(self):
        with patch.object(accountUsage.requests, "head", side_effect=self.product_response) as head, patch.object(accountUsage.time, "monotonic", return_value=100) as clock:
            first = accountUsage.get_account_usage()
            clock.return_value = 129
            self.assertTrue(accountUsage.get_account_usage()["cached"])
            self.assertEqual(head.call_count, 2)
            clock.return_value = 131
            self.assertFalse(accountUsage.get_account_usage()["cached"])
            self.assertEqual(head.call_count, 4)
            self.assertEqual(first["cacheMaxAgeSeconds"], 30)

    def test_api_key_change_never_reuses_previous_key_balance(self):
        with patch.object(accountUsage.requests, "head", side_effect=self.product_response) as head:
            accountUsage.get_account_usage()
            with patch.dict(os.environ, {"PDL_API_KEY": "different-synthetic-key"}):
                result = accountUsage.get_account_usage()
            self.assertEqual(head.call_count, 4)
            self.assertFalse(result["cached"])
            self.assertNotIn("synthetic-key", json.dumps(result))

    def test_actual_search_attempts_invalidate_search_only_even_on_errors(self):
        for outcome in (response(200, credit_headers(), {"data": []}), response(400), requests.Timeout(), requests.ConnectionError()):
            with self.subTest(outcome=type(outcome).__name__), patch.object(peopleSearch.requests, "post", side_effect=outcome if isinstance(outcome, Exception) else None, return_value=outcome), patch.object(peopleSearch, "invalidate_account_usage") as invalidate:
                try:
                    peopleSearch._post_search({"size": 1})
                except peopleSearch.PeopleDataLabsError:
                    pass
                invalidate.assert_called_once_with("search")

    def test_enrichment_preserves_header_metadata_and_invalidates_enrichment_only(self):
        with patch.object(peopleSearch.requests, "get", return_value=response(200, credit_headers("enrich", "84"), {"data": {"id": "synthetic-profile"}})), patch.object(peopleSearch, "invalidate_account_usage") as invalidate:
            result = peopleSearch.enrichPerson(pdl_id="synthetic-profile")
        self.assertEqual(result["data"]["id"], "synthetic-profile")
        self.assertEqual(result["provider_usage"]["accountRemainingCredits"], 84)
        self.assertEqual(result["provider_usage"]["creditType"], "enrich")
        invalidate.assert_called_once_with("enrich")

    def test_failed_enrichment_attempts_invalidate_and_keep_unknown_distinct_from_zero(self):
        for outcome in (response(404, credit_headers("enrich")), response(402, credit_headers("enrich", "0")), requests.Timeout(), requests.ConnectionError()):
            with self.subTest(outcome=type(outcome).__name__), patch.object(peopleSearch.requests, "get", side_effect=outcome if isinstance(outcome, Exception) else None, return_value=outcome), patch.object(peopleSearch, "invalidate_account_usage") as invalidate:
                try:
                    usage = peopleSearch.enrichPerson(pdl_id="synthetic-profile")["provider_usage"]
                except peopleSearch.PeopleDataLabsError as error:
                    usage = error.provider_usage
                invalidate.assert_called_once_with("enrich")
                self.assertEqual(usage["requests"], 1)
                self.assertEqual(usage["status"], "unavailable" if isinstance(outcome, Exception) else "reported")
                self.assertEqual(usage["creditsUsed"], None if isinstance(outcome, Exception) else 0)


if __name__ == "__main__":
    unittest.main()
