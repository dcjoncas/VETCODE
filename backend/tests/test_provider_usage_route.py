import unittest
from unittest.mock import Mock, call, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from azureUtils.routes.externalProviderUsage import create_router
from peopleDataLabs import accountUsage


class ProviderUsageRouteTests(unittest.TestCase):
    endpoint = "/api/azureJobs/external/provider-usage"
    valid_token = "test-provider-usage-admin-token"

    def setUp(self):
        def authorize(token):
            if token != self.valid_token:
                raise HTTPException(status_code=403, detail="Administrator access required.")
            return {"username": "administrator"}

        self.authorize = Mock(side_effect=authorize)
        self.network = self.enterContext(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Route tests must not contact a provider."),
        ))
        self.usage = self.enterContext(patch.object(accountUsage, "get_account_usage"))
        self.calls = Mock()
        self.calls.attach_mock(self.authorize, "authorize")
        self.calls.attach_mock(self.usage, "usage")
        app = FastAPI()
        app.include_router(create_router(self.authorize), prefix="/api/azureJobs")
        self.client = self.enterContext(TestClient(app))
        self.headers = {"X-DevReady-Admin-Token": self.valid_token}
        self.payload = {
            "provider": "People Data Labs",
            "source": "head_response_headers",
            "checkedAt": "2026-09-10T12:00:00Z",
            "cached": False,
            "products": {
                "search": {
                    "status": "reported",
                    "accountRemainingCredits": 0,
                    "purchasedRemainingCredits": 0,
                    "overageRemainingCredits": 0,
                },
                "enrich": {
                    "status": "unavailable",
                    "accountRemainingCredits": None,
                    "purchasedRemainingCredits": None,
                    "overageRemainingCredits": None,
                },
            },
        }
        self.usage.return_value = self.payload

    def tearDown(self):
        self.network.assert_not_called()

    def test_missing_invalid_or_alternate_header_is_rejected_before_usage_lookup(self):
        cases = [
            ({}, ""),
            ({"X-DevReady-Admin-Token": "invalid"}, "invalid"),
            ({"Authorization": f"Bearer {self.valid_token}"}, ""),
            ({"X-DevReady-Role": "administrator"}, ""),
        ]
        for headers, expected_token in cases:
            with self.subTest(headers=headers):
                self.authorize.reset_mock()
                response = self.client.get(self.endpoint, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.authorize.assert_called_once_with(expected_token)
                self.usage.assert_not_called()

    def test_authorized_response_preserves_schema_zeroes_nulls_and_cache_flag(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                self.calls.reset_mock()
                self.payload["cached"] = cached
                response = self.client.get(self.endpoint, headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), self.payload)
                self.assertIn("no-store", response.headers["cache-control"].split(", "))
                self.assertEqual(
                    self.calls.mock_calls,
                    [call.authorize(self.valid_token), call.usage()],
                )

    def test_admin_token_is_never_accepted_from_query_parameters(self):
        for key in ("token", "admin_token", "x_devready_admin_token", "X-DevReady-Admin-Token"):
            with self.subTest(key=key):
                self.authorize.reset_mock()
                response = self.client.get(self.endpoint, params={key: self.valid_token})
                self.assertEqual(response.status_code, 403)
                self.authorize.assert_called_once_with("")
                self.usage.assert_not_called()

    def test_query_token_cannot_replace_invalid_admin_header(self):
        response = self.client.get(
            self.endpoint,
            params={"admin_token": self.valid_token},
            headers={"X-DevReady-Admin-Token": "invalid"},
        )
        self.assertEqual(response.status_code, 403)
        self.authorize.assert_called_once_with("invalid")
        self.usage.assert_not_called()

    def test_domain_query_does_not_change_account_scope_or_helper_arguments(self):
        for domain in ("dev", "engineer", "law", "dental", "all", "unknown"):
            with self.subTest(domain=domain):
                self.usage.reset_mock()
                response = self.client.get(
                    self.endpoint, params={"domain": domain}, headers=self.headers,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), self.payload)
                self.usage.assert_called_once_with()

    def test_query_parameters_cannot_request_a_cache_bypass(self):
        response = self.client.get(
            self.endpoint,
            params={"force_refresh": "true", "forceRefresh": "true", "refresh": "1"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.usage.assert_called_once_with()

    def test_write_methods_are_not_supported_and_do_not_call_provider(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = self.client.request(method, self.endpoint, headers=self.headers)
                self.assertEqual(response.status_code, 405)
        self.usage.assert_not_called()
        self.authorize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
