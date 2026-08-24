import unittest
from pathlib import Path
from unittest.mock import patch

import main


BACKEND = Path(__file__).resolve().parents[1]
PAGES = BACKEND / "ui" / "pages"


class AtlasSourcingContextTests(unittest.TestCase):
    def test_find_talent_pages_mount_shared_atlas_client_attachment(self):
        for page_name in ("find-candidate.html", "match-role.html", "mine-candidate-external.html", "temp-profiles.html"):
            html = (PAGES / page_name).read_text(encoding="utf-8")
            self.assertIn('data-atlas-client-context', html, page_name)
            self.assertIn('JS/atlasClientContext.js', html, page_name)

    def test_shared_attachment_is_domain_scoped_and_uses_real_atlas_records(self):
        script = (PAGES / "JS" / "atlasClientContext.js").read_text(encoding="utf-8")
        self.assertIn('atlasSourcingClient:${domain}', script)
        self.assertIn('/api/crm/records?domain=', script)
        self.assertIn('sourcing_only=true', script)
        self.assertIn('isSyntheticSourcingRecord', script)
        self.assertIn('clientNameKey', script)
        self.assertIn('Demo and seed cards are excluded from sourcing', script)
        self.assertIn('atlasClientId', script)
        self.assertIn('contactLinkedIn', script)
        self.assertIn('Check client/JD alignment', script)
        self.assertIn('dev', script)
        self.assertIn('engineer', script)
        self.assertIn('law', script)
        self.assertIn('dental', script)

    def test_shortlist_email_uses_attached_atlas_recipient_and_context(self):
        html = (PAGES / "client-comm.html").read_text(encoding="utf-8")
        route = (BACKEND / "openAI" / "routes" / "aiEndpoints.py").read_text(encoding="utf-8")
        email_processing = (BACKEND / "openAI" / "emailProcessing.py").read_text(encoding="utf-8")
        self.assertIn('clientContext: atlasClient', html)
        self.assertIn('atlasClient?.contactEmail', html)
        self.assertNotIn('mailto:INSERT_EMAIL', html)
        self.assertIn('clientContext: dict = Field(default_factory=dict)', route)
        self.assertIn('Atlas client context:', email_processing)

    def test_interview_prefill_and_archive_keep_atlas_link(self):
        html = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")
        status = (PAGES / "status-tracker.html").read_text(encoding="utf-8")
        self.assertIn('applyAttachedAtlasClient()', html)
        self.assertIn('byId("clientCompanyInput").value = client.name', html)
        self.assertIn('byId("clientContactEmailInput").value = client.contactEmail', html)
        self.assertIn('atlasClientId: atlasClient?.id', html)
        self.assertIn('atlasClientUrl:', html)
        self.assertIn('clientContactPhone:', html)
        self.assertIn('Open attached Atlas record', status)
        self.assertIn('selectedRecord.clientAddress', status)

    def test_process_reset_removes_attached_client(self):
        process_flow = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")
        sidebar = (PAGES / "components" / "sidebar.html").read_text(encoding="utf-8")
        update_flow = (PAGES / "JS" / "updateProcessFlow.js").read_text(encoding="utf-8")
        self.assertIn('sessionStorage.removeItem(`atlasSourcingClient:${keepDomain}`)', process_flow)
        self.assertIn('sessionStorage.removeItem(`atlasSourcingClient:${activeDomain}`)', sidebar)
        self.assertIn('id="clientSelected"', process_flow)
        self.assertIn('function updateAtlasClient()', update_flow)

    @patch("main._atlas_crm_records_db")
    def test_legalready_sourcing_excludes_known_seed_and_demo_cards(self, load_records):
        load_records.return_value = [
            {"id": "CRM-SEED-LOCAL-LAW-01", "domain": "law", "customer": "BrightPath Legal Ops"},
            {"id": "CRM-SEED-DEVDB-LAW-01", "domain": "law", "customer": "BrightPath Legal Ops"},
            {"id": "CRM-DEMO-LAW-01", "domain": "law", "customer": "Civic Harbor Legal"},
            {
                "id": "random-browser-demo-id",
                "domain": "law",
                "customer": "Meridian Compliance Group",
                "history": "Sample law card created for dev testing.",
            },
            {"id": "CRM-LAW-ACTIVE-01", "domain": "law", "customer": "Murchison & Cumming LLP"},
        ]

        response = main.list_crm_records(domain="law", limit=100, sourcing_only=True)

        self.assertEqual([record["id"] for record in response["records"]], ["CRM-LAW-ACTIVE-01"])
        self.assertEqual(response["excludedSyntheticCount"], 4)
        self.assertTrue(response["sourcingOnly"])

    def test_non_law_sourcing_records_are_not_hidden_by_legalready_filter(self):
        record = {"id": "CRM-DEMO-DENTAL-01", "domain": "dental", "customer": "Dental Sample"}
        self.assertFalse(main._crm_record_is_synthetic_for_sourcing(record, "dental"))


if __name__ == "__main__":
    unittest.main()
