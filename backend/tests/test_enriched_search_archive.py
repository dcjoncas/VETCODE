import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from azureUtils.routes import azureJobEndpoints as routes
from azureUtils.storage import externalSearchHistory as history


def candidate(source="pdl"):
    return {"source": source, "source_id": "provider-person-1", "name": "Sample Person",
            "profile_url": "https://www.linkedin.com/in/sample-person", "score": 80}


def group(person):
    return {"metadata": {"rootId": 12, "domain": "law" if person["source"] == "courtlistener" else "dev"},
            "pages": [{"response": {"results": [copy.deepcopy(person)]}}]}


class EnrichedArchiveStorageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(history, "ensure_tables"))
        self.conn = Mock()
        self.cur = self.conn.cursor.return_value
        self.cur.fetchone.return_value = (12,)
        self.stack.enter_context(patch.object(history.client, "getConnection", return_value=self.conn))

    def test_identity_uses_provider_id_or_strict_linkedin_never_name(self):
        self.assertEqual(history.candidate_archive_identity(candidate()), ("pdl", "id", "provider-person-1"))
        self.assertIsNone(history.candidate_archive_identity({"source": "pdl", "name": "Sample Person"}))
        for url in ["linkedin.com/in/sample/", "https://www.linkedin.com/in/sample?trk=x"]:
            self.assertEqual(history.candidate_archive_identity({"source": "pdl", "profile_url": url}), ("pdl", "linkedin", "/in/sample"))
        for url in ["https://linkedin.com.evil/in/sample", "javascript:alert(1)", "https://u:p@linkedin.com/in/sample", "https://linkedin.com/in/"]:
            self.assertIsNone(history.candidate_archive_identity({"source": "pdl", "profile_url": url}))

    def test_updates_only_matching_existing_results_preserves_search_and_usage(self):
        original = candidate()
        other = {**candidate(), "source_id": "other", "name": "Other Person"}
        page = {"results": [original, other], "sourceAudit": {"providerUsage": {"creditsUsed": 5}},
                "pagination": {"nextScrollToken": "stored-token"}, "criteria": {"titles": ["Lawyer"]}}
        self.cur.fetchall.return_value = [(12, copy.deepcopy(page)), (13, json.dumps(page)), (99, {"results": [other]})]
        enriched = {**original, "email": "real@example.test", "external_enrichment": {"status": "completed"},
                    "match_pending": True, "sourceAudit": {"creditsUsed": 999}, "untrustedExtra": "not saved"}
        self.assertEqual(history.preserve_candidate_enrichment(12, "dev", original, enriched), 2)
        updates = [call for call in self.cur.execute.call_args_list if call.args[0].startswith("UPDATE")]
        self.assertEqual(len(updates), 2)
        for call in updates:
            saved = json.loads(call.args[1][0])
            self.assertEqual(saved["sourceAudit"], page["sourceAudit"])
            self.assertEqual(saved["pagination"], page["pagination"])
            self.assertEqual(saved["criteria"], page["criteria"])
            self.assertEqual(saved["results"][1], other)
            self.assertEqual(saved["results"][0]["email"], "real@example.test")
            self.assertEqual(saved["results"][0]["source_id"], original["source_id"])
            self.assertIsNone(saved["results"][0]["score"])
            self.assertNotIn("sourceAudit", saved["results"][0])
            self.assertNotIn("untrustedExtra", saved["results"][0])
            self.assertEqual(call.args[1][2], "dev")
        self.assertIn("FOR UPDATE", self.cur.execute.call_args_list[1].args[0])
        self.conn.commit.assert_called_once()
        self.conn.rollback.assert_not_called()
        self.conn.close.assert_called_once()

    def test_missing_root_or_candidate_rolls_back_and_never_inserts(self):
        for missing_root in [True, False]:
            with self.subTest(missing_root=missing_root):
                self.conn.reset_mock()
                self.cur.fetchone.return_value = None if missing_root else (12,)
                self.cur.fetchall.return_value = [(12, {"results": [{**candidate(), "source_id": "other"}]})]
                with self.assertRaises(ValueError):
                    history.preserve_candidate_enrichment(12, "law", candidate(), {"email": "x@example.test"})
                self.conn.rollback.assert_called_once()
                self.conn.commit.assert_not_called()
                self.assertFalse(any(call.args[0].startswith(("INSERT", "UPDATE")) for call in self.cur.execute.call_args_list))

    def test_write_failure_rolls_back_without_partial_commit(self):
        self.cur.fetchall.return_value = [(12, {"results": [candidate()]})]
        def execute(sql, params):
            if sql.startswith("UPDATE"):
                raise RuntimeError("synthetic storage failure")
        self.cur.execute.side_effect = execute
        with self.assertRaises(RuntimeError):
            history.preserve_candidate_enrichment(12, "dev", candidate(), {"email": "x@example.test"})
        self.conn.rollback.assert_called_once()
        self.conn.commit.assert_not_called()
        self.conn.close.assert_called_once()


class EnrichedArchiveRouteTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(history.client, "getConnection", side_effect=AssertionError("No database")))
        self.original = candidate()
        self.read = self.stack.enter_context(patch.object(history, "get_search_group", return_value=group(self.original)))
        self.save = self.stack.enter_context(patch.object(history, "preserve_candidate_enrichment", return_value=1))
        self.response = {"status": 200, "likelihood": 9, "data": {
            "id": "provider-person-1", "first_name": "Sample", "last_name": "Person",
            "linkedin_url": "linkedin.com/in/sample-person", "work_email": "real@example.test"},
            "provider_usage": routes.peopleDataLabs.summarize_search_usage([{"creditsUsed": 2, "creditType": "enrich"}])}
        self.enrich = self.stack.enter_context(patch.object(routes.peopleDataLabs, "enrichPerson", return_value=self.response))

    def invoke(self, person=None, search_id="12", domain="dev"):
        payload = {"domain": domain, "candidate": person or copy.deepcopy(self.original)}
        if search_id is not None:
            payload["search_id"] = search_id
        return routes.external_candidate_enrich_result(payload)

    def test_fresh_enrichment_uses_stored_candidate_and_preserves_actual_accounting(self):
        supplied = {**self.original, "name": "Forged Name", "profile_url": "https://evil.test", "email": "fake@example.test"}
        result = self.invoke(supplied)
        self.read.assert_called_once_with("12", "dev")
        self.enrich.assert_called_once_with(profile=self.original["profile_url"], pdl_id=self.original["source_id"])
        self.assertEqual(result["candidate"]["name"], "Sample Person")
        self.assertEqual(result["candidate"]["email"], "real@example.test")
        self.assertEqual(result["creditsUsed"], 2)
        self.assertEqual(result["archivePersistence"], {"status": "saved", "searchId": 12})
        self.assertEqual(self.save.call_args.args[:3], (12, "dev", self.original))

    def test_missing_wrong_workspace_or_wrong_person_blocks_provider(self):
        for wrong_person in [False, True]:
            with self.subTest(wrong_person=wrong_person):
                self.read.return_value = group({**self.original, "source_id": "other"}) if wrong_person else None
                with self.assertRaises(routes.HTTPException) as raised:
                    self.invoke(domain="law")
                self.assertEqual(raised.exception.status_code, 404)
                self.enrich.assert_not_called()
                self.save.assert_not_called()

    def test_invalid_id_or_name_only_identity_blocks_provider(self):
        for search_id in [True, -1, "1;DROP", "", 0]:
            if search_id == "":
                continue  # Empty is intentionally the legacy, non-archive route.
            with self.subTest(search_id=search_id):
                with self.assertRaises(routes.HTTPException) as raised:
                    self.invoke(search_id=search_id)
                self.assertEqual(raised.exception.status_code, 400)
        with self.assertRaises(routes.HTTPException):
            self.invoke(person={"source": "pdl", "name": "Sample Person"})
        self.enrich.assert_not_called()

    def test_storage_unavailable_blocks_paid_call_without_exposing_error(self):
        self.read.side_effect = RuntimeError("private connection text")
        with self.assertRaises(routes.HTTPException) as raised:
            self.invoke()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("private", raised.exception.detail)
        self.enrich.assert_not_called()

    def test_failed_archive_write_keeps_completed_result_and_credit_metadata(self):
        self.save.side_effect = RuntimeError("private database text")
        result = self.invoke()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["candidate"]["email"], "real@example.test")
        self.assertEqual(result["creditsUsed"], 2)
        self.assertEqual(result["archivePersistence"]["status"], "failed")
        self.assertIn("Do not repeat", result["archivePersistence"]["notice"])
        self.assertNotIn("private", result["archivePersistence"]["notice"])

    def test_server_archived_reuse_needs_neither_provider_nor_write(self):
        enriched = {**self.original, "email": "archived@example.test", "external_enrichment": {
            "status": "completed", "profileVersion": 2, "provider": "People Data Labs Person Enrichment"}}
        self.read.return_value = group(enriched)
        result = self.invoke()
        self.assertTrue(result["reused"])
        self.assertEqual(result["creditsUsed"], 0)
        self.assertEqual(result["candidate"]["email"], "archived@example.test")
        self.assertEqual(result["archivePersistence"]["status"], "saved")
        self.enrich.assert_not_called()
        self.save.assert_not_called()

    def test_client_only_reuse_is_not_trusted_or_billed_again(self):
        forged = {**self.original, "external_enrichment": {
            "status": "completed", "profileVersion": 2, "provider": "People Data Labs Person Enrichment"}}
        with self.assertRaises(routes.HTTPException) as raised:
            self.invoke(forged)
        self.assertEqual(raised.exception.status_code, 409)
        self.enrich.assert_not_called()
        self.save.assert_not_called()

    def test_no_archive_id_retains_existing_behavior(self):
        result = self.invoke(search_id=None)
        self.assertNotIn("archivePersistence", result)
        self.read.assert_not_called()
        self.save.assert_not_called()
        self.enrich.assert_called_once()

    def test_provider_error_never_writes_archive(self):
        self.enrich.side_effect = routes.peopleDataLabs.PeopleDataLabsError("Synthetic", 429, self.response["provider_usage"])
        with self.assertRaises(routes.PdlEnrichmentHTTPException) as raised:
            self.invoke()
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.accounting["creditsUsed"], 2)
        self.save.assert_not_called()

    def test_law_validation_success_no_match_and_reuse_preserved(self):
        person = {**candidate("courtlistener"), "result_type": "court_attorney_lead"}
        self.read.return_value = group(person)
        payload = {"domain": "law", "search_id": "12", "candidate": person}
        first = routes.external_court_lead_validate_profile(payload)
        self.assertEqual(first["archivePersistence"]["status"], "saved")
        self.assertEqual(first["profileValidation"]["status"], "confirmed_profile_match")
        self.assertEqual(self.save.call_args.args[3]["profile_validation"], first["profileValidation"])
        self.response.update(status=404, data=None)
        unmatched = routes.external_court_lead_validate_profile(payload)
        self.assertEqual(unmatched["profileValidation"]["status"], "no_match")
        self.assertEqual(unmatched["archivePersistence"]["status"], "saved")
        self.read.return_value = group(first["candidate"])
        self.enrich.reset_mock()
        self.save.reset_mock()
        reused = routes.external_court_lead_validate_profile(payload)
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["archivePersistence"]["status"], "saved")
        self.enrich.assert_not_called()
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
