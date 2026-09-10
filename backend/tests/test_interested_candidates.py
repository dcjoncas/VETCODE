import json
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from azureUtils.routes.externalInterested import create_router
from azureUtils.storage import interestedCandidates as store


def row(person_id, status="interested", domain="dev", temporary=True, contact=None, job_id="", email="", linkedin="", match=None):
    metadata = {"interestWorkflow": {"status": status, "jobId": job_id, "confirmedAt": "2026-09-10"}, "contact": contact or {}, "source": "PDL"}
    if match:
        metadata["match"] = match
    description = ("Temporary external profile. " if temporary else "External profile. ") + "Backend developer."
    return (person_id, "Saved", f"Candidate {person_id}", email, "Developer",
            description + "\nVETCODE_EXTERNAL_PROFILE_META:" + json.dumps(metadata),
            linkedin, "2026-09-10", "Denver", "CO", "US", domain)


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.calls, self.result = rows, [], []

    def execute(self, query, params):
        self.calls.append((query, params))
        if "FROM person" in query:
            domain, after, limit = params
            self.result = [r[:11] for r in self.rows if r[-1] == domain and r[0] > after][:limit]
        elif "FROM jobdescription" in query:
            self.result = [(i, "Saved job title") for i in params[1]]
        else:
            raise AssertionError("Unexpected query or mutation")

    def fetchall(self):
        return self.result


class InterestedStoreTests(unittest.TestCase):
    def call(self, rows, **kwargs):
        cur = FakeCursor(rows)
        conn = Mock()
        conn.cursor.return_value = cur
        with patch.object(store.client, "getConnection", return_value=conn):
            result = store.list_interested("dev", **kwargs)
        conn.close.assert_called_once()
        conn.commit.assert_not_called()
        return result, cur

    def test_scans_beyond_500_filters_exact_status_includes_promoted_and_missing_contact(self):
        rows = [row(i, "contacting") for i in range(1, 601)] + [row(601, temporary=False), row(602)]
        result, cur = self.call(rows)
        self.assertEqual([p["personid"] for p in result["profiles"]], [601, 602])
        self.assertFalse(result["profiles"][0]["temporary"])
        self.assertFalse(result["profiles"][0]["contactAvailable"])
        self.assertFalse(result["hasMore"])
        self.assertGreaterEqual(len(cur.calls), 3)
        self.assertFalse(result["providerContacted"])
        self.assertEqual(result["providerCreditsUsed"], 0)

    def test_pagination_comes_after_interest_filter_and_returns_every_match_once(self):
        rows = [row(i, "interested" if i % 2 else "not_interested") for i in range(1, 602)]
        after, ids = 0, []
        while True:
            data, _ = self.call(rows, limit=100, after=after)
            ids.extend(p["personid"] for p in data["profiles"])
            if not data["hasMore"]:
                break
            after = int(data["nextCursor"])
        self.assertEqual(ids, list(range(1, 602, 2)))
        self.assertEqual(len(ids), len(set(ids)))

    def test_sparse_scan_yields_cursor_instead_of_silently_truncating(self):
        rows = [row(i, "contacting") for i in range(1, 5001)] + [row(5001)]
        first, _ = self.call(rows)
        self.assertEqual(first["profiles"], [])
        self.assertTrue(first["hasMore"])
        second, _ = self.call(rows, after=int(first["nextCursor"]))
        self.assertEqual(second["profiles"][0]["personid"], 5001)

    def test_canonical_contacts_precede_metadata_fallbacks_and_unsafe_values_stay_unknown(self):
        contact = {"primaryEmail": "metadata@example.com", "professionalEmails": [{"address": "work@example.com"}], "phoneNumbers": [{"number": "+1 303 555 0100"}], "linkedinUrl": "https://linkedin.com/in/saved"}
        result, _ = self.call([row(1, contact=contact, email="canonical@example.com", linkedin="https://www.linkedin.com/in/canonical"), row(2, email="<script>", linkedin="javascript:alert(1)", contact={"primaryPhone": {"bad": "value"}})])
        first, bad = result["profiles"]
        self.assertEqual(first["email"], "canonical@example.com")
        self.assertEqual(first["emails"], ["canonical@example.com", "metadata@example.com", "work@example.com"])
        self.assertEqual(first["phone"], "+1 303 555 0100")
        self.assertEqual(first["linkedinUrl"], "https://www.linkedin.com/in/canonical")
        self.assertFalse(bad["contactAvailable"])

    def test_no_job_default_keeps_all_and_optional_job_filter_is_explicit(self):
        rows = [row(1, job_id="5"), row(2, job_id="6"), row(3)]
        data, cur = self.call(rows)
        self.assertEqual(len(data["profiles"]), 3)
        self.assertEqual(data["profiles"][0]["interestJobTitle"], "Saved job title")
        self.assertEqual(cur.calls[-1][1][0], "dev")
        only, _ = self.call(rows, jd_id="5")
        self.assertEqual([p["personid"] for p in only["profiles"]], [1])

    def test_query_dedupes_latest_professional_and_address_before_metadata_filter(self):
        sql = " ".join(store.PROFILE_PAGE_SQL.split())
        self.assertIn("ORDER BY modifieddate DESC NULLS LAST, id DESC LIMIT 1", sql)
        self.assertIn("ORDER BY id DESC LIMIT 1", sql)
        self.assertIn("WHERE person.domain = %s AND person.id > %s", sql)
        self.assertNotIn("Temporary external profile", sql)
        # Duplicate join rows cannot create duplicate report rows even defensively.
        data, _ = self.call([row(1), row(1), row(2, "not_interested"), row(3, domain="law")])
        self.assertEqual([p["personid"] for p in data["profiles"]], [1])

    def test_malformed_metadata_and_non_authoritative_states_are_not_interest(self):
        malformed = list(row(1)); malformed[5] = 'External profile.\nVETCODE_EXTERNAL_PROFILE_META:{bad';
        data, _ = self.call([tuple(malformed), row(2, "not_interested"), row(3, "contacting"), row(4)])
        self.assertEqual([p["personid"] for p in data["profiles"]], [4])

    def test_report_omits_potentially_stale_match_scores_even_with_same_job_id(self):
        good = {"status": "calculated", "jobId": "5", "score": 75, "evidenceType": "structured_professional_profile"}
        data, _ = self.call([row(1, job_id="5", match=good), row(2, job_id="6", match=good), row(3, job_id="5", match={**good, "score": None})])
        for profile in data["profiles"]:
            self.assertNotIn("matchScore", profile)
            self.assertNotIn("matchLabel", profile)

    def test_provider_contact_aliases_are_validated_fallbacks_not_false_unknowns(self):
        fallback = {
            "workEmail": "work@example.test", "recommendedPersonalEmail": "personal@example.test",
            "mobilePhone": "+1 202 555 0100", "emails": [{"email": "other@example.test"}, "not an email"],
            "phones": [{"phone": "+1 202 555 0101"}, "javascript:alert(1)"],
        }
        result, _ = self.call([
            row(1, contact=fallback),
            row(2, email="canonical@example.test", contact={**fallback, "primaryEmail": "primary@example.test", "primaryPhone": "+1 202 555 0102"}),
            row(3, contact={"workEmail": "bad", "recommendedPersonalEmail": {"bad": "value"}, "mobilePhone": "bad", "emails": ["array@example.test"], "phoneNumbers": ["+1 202 555 0103"]}),
        ])
        first, canonical, arrays = result["profiles"]
        self.assertEqual(first["email"], "work@example.test")
        self.assertEqual(first["emails"], ["work@example.test", "personal@example.test", "other@example.test"])
        self.assertEqual(first["phone"], "+1 202 555 0100")
        self.assertEqual(first["phones"], ["+1 202 555 0100", "+1 202 555 0101"])
        self.assertTrue(first["contactAvailable"])
        self.assertEqual(canonical["email"], "canonical@example.test")
        self.assertEqual(canonical["emails"][1], "primary@example.test")
        self.assertEqual(canonical["phone"], "+1 202 555 0102")
        self.assertEqual(arrays["email"], "array@example.test")
        self.assertEqual(arrays["phone"], "+1 202 555 0103")


class InterestedRouteTests(unittest.TestCase):
    def setUp(self):
        def authorize(token):
            if token != "test-valid-token":
                raise HTTPException(status_code=403, detail="Administrator access required.")
        self.app = FastAPI()
        self.app.include_router(create_router(authorize), prefix="/api/azureJobs")
        self.client = TestClient(self.app)
        self.headers = {"X-DevReady-Admin-Token": "test-valid-token"}

    def test_missing_or_invalid_token_forbidden_before_any_database_call(self):
        with patch.object(store, "list_interested") as reader:
            for headers in [{}, {"X-DevReady-Admin-Token": "forged"}, {"Authorization": "Administrator"}]:
                self.assertEqual(self.client.get("/api/azureJobs/external/interested?domain=dev", headers=headers).status_code, 403)
            reader.assert_not_called()

    def test_authorized_exact_workspace_and_get_only(self):
        with patch.object(store, "list_interested", return_value={"profiles": [], "domain": "law"}) as reader:
            response = self.client.get("/api/azureJobs/external/interested?domain=law&limit=20&after=900", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            reader.assert_called_once_with("law", 20, 900, "")
            self.assertEqual(self.client.post("/api/azureJobs/external/interested?domain=law", headers=self.headers).status_code, 405)

    def test_missing_invalid_all_scope_and_bad_pagination_fail_before_database(self):
        with patch.object(store, "list_interested") as reader:
            for query in ["", "?domain=all", "?domain=oops", "?domain=dev&after=-1", "?domain=dev&limit=999"]:
                self.assertIn(self.client.get("/api/azureJobs/external/interested" + query, headers=self.headers).status_code, [400, 422])
            reader.assert_not_called()

    def test_unavailability_is_not_empty_success_and_does_not_expose_connection_secrets(self):
        with patch.object(store, "list_interested", side_effect=RuntimeError("private connection string")):
            response = self.client.get("/api/azureJobs/external/interested?domain=dev", headers=self.headers)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private connection", response.text)


if __name__ == "__main__":
    unittest.main()
