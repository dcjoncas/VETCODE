import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import process_builder


BACKEND = Path(__file__).resolve().parents[1]
PAGES = BACKEND / "ui" / "pages"


class ProcessBuilderApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(os.environ, {"PROCESS_BUILDER_DATABASE_ENABLED": "false"})
        self.database_patch.start()
        self.previous_store_path = process_builder.STORE_PATH
        process_builder.STORE_PATH = Path(self.temporary.name) / "process-builder.json"
        app = FastAPI()
        app.include_router(process_builder.router)
        self.client = TestClient(app)

    def tearDown(self):
        process_builder.STORE_PATH = self.previous_store_path
        self.database_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def process_payload(name="Client approval", activity="Review request"):
        return {
            "name": name,
            "client_name": "Northwind",
            "domain": "dev",
            "owner": "Operations",
            "purpose": "Approve a client request with traceable automation.",
            "scope": "Request through confirmation.",
            "trigger": "A request arrives.",
            "outcome": "The approved result is recorded.",
            "systems": ["CRM"],
            "controls": ["Manager approval"],
            "elements": [
                {"id": "start", "type": "bpmn:StartEvent", "name": "Start"},
                {"id": "review", "type": "bpmn:UserTask", "name": activity, "owner": "Manager"},
                {
                    "id": "record",
                    "type": "bpmn:ServiceTask",
                    "name": "Record approved outcome",
                    "api_endpoints": ["POST /api/approvals"],
                    "code_refs": ["backend/approvals.py"],
                    "mcp_tools": ["crm.record_approval"],
                },
                {"id": "end", "type": "bpmn:EndEvent", "name": "Complete"},
            ],
            "connections": [
                {"id": "f1", "from": "start", "to": "review"},
                {"id": "f2", "from": "review", "to": "record"},
                {"id": "f3", "from": "record", "to": "end"},
            ],
            "bpmn_xml": "<definitions />",
        }

    def test_health_seeds_foundry_and_reports_safe_connection_metadata(self):
        response = self.client.get("/api/process-builder/health")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["product"], "DevReady Process Builder")
        self.assertEqual(data["foundry"], "AIReady Foundry")
        self.assertGreaterEqual(data["components"], 3)
        self.assertNotIn("key", json.dumps(data).lower())
        self.assertEqual(data["mcp_endpoint"], "/api/process-builder/mcp")

    def test_save_checks_each_activity_then_creates_or_reuses_components(self):
        first = self.client.post("/api/process-builder/processes", json=self.process_payload())

        self.assertEqual(first.status_code, 201)
        first_data = first.json()
        self.assertEqual(first_data["reconciliation"]["checked"], 2)
        self.assertEqual(len(first_data["reconciliation"]["created"]), 2)
        self.assertTrue(first_data["process"]["validation"]["ok"])
        self.assertEqual(first_data["process"]["validation"]["score"], 100)

        second = self.client.post(
            "/api/process-builder/processes",
            json=self.process_payload(name="Renewal approval"),
        )
        self.assertEqual(second.status_code, 201)
        second_data = second.json()
        self.assertEqual(len(second_data["reconciliation"]["reused"]), 2)
        self.assertEqual(len(second_data["reconciliation"]["created"]), 0)

        components = self.client.get("/api/process-builder/components").json()["components"]
        review = next(item for item in components if item["name"] == "Review request")
        self.assertEqual(len(review["used_by_processes"]), 2)

    def test_traceability_connects_process_activity_to_code_api_and_mcp(self):
        created = self.client.post("/api/process-builder/processes", json=self.process_payload()).json()["process"]
        response = self.client.get(f"/api/process-builder/processes/{created['id']}/traceability")

        self.assertEqual(response.status_code, 200)
        trace = response.json()["traceability"]
        service_row = next(row for row in trace["rows"] if row["element_id"] == "record")
        self.assertIn("backend/approvals.py", service_row["code_refs"])
        self.assertIn("POST /api/approvals", service_row["api_endpoints"])
        self.assertIn("crm.record_approval", service_row["mcp_tools"])
        self.assertIsNotNone(service_row["component"])

    def test_validation_blocks_incomplete_structure_and_untraced_integrations(self):
        payload = self.process_payload()
        payload["elements"] = payload["elements"][:-1]
        payload["connections"] = payload["connections"][:-1]
        payload["elements"][2]["api_endpoints"] = []
        payload["elements"][2]["code_refs"] = []
        payload["elements"][2]["mcp_tools"] = []

        response = self.client.post("/api/process-builder/validate", json=payload)

        self.assertEqual(response.status_code, 200)
        validation = response.json()["validation"]
        self.assertFalse(validation["ok"])
        codes = {issue["code"] for issue in validation["issues"]}
        self.assertIn("missing_end", codes)
        self.assertIn("missing_outgoing", codes)
        self.assertIn("integration_untraced", codes)
        self.assertEqual(len(validation["client_validation_checklist"]), 5)

    def test_component_check_does_not_create_until_user_saves(self):
        before = self.client.get("/api/process-builder/components").json()["count"]
        response = self.client.post(
            "/api/process-builder/components/check",
            json={"elements": [{"id": "unique", "type": "bpmn:UserTask", "name": "A unique client review"}]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["missing"][0]["element_id"], "unique")
        after = self.client.get("/api/process-builder/components").json()["count"]
        self.assertEqual(after, before)

    def test_mcp_exposes_read_only_catalog_tools(self):
        initialize = self.client.post(
            "/api/process-builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        tools = self.client.post(
            "/api/process-builder/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

        self.assertEqual(initialize.status_code, 200)
        self.assertEqual(initialize.json()["result"]["serverInfo"]["name"], "devready-aiready-foundry")
        tool_names = {item["name"] for item in tools.json()["result"]["tools"]}
        self.assertEqual(tool_names, {"list_processes", "get_process", "find_components", "get_traceability"})
        manifest = self.client.get("/api/process-builder/mcp/manifest").json()
        self.assertTrue(manifest["read_only"])

    def test_ai_chat_uses_server_key_and_has_explicit_unconfigured_state(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = self.client.post("/api/process-builder/chat", json={"message": "Map our intake flow."})

        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENAI_API_KEY", response.json()["detail"])

    def test_ai_chat_uses_responses_api_structured_process_schema(self):
        result = {
            "assistant_message": "I drafted the flow for review.",
            "needs_clarification": False,
            "discovery_complete": True,
            "client_name": "Northwind",
            "processes": [],
        }
        responses = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(result)))
        client = SimpleNamespace(responses=responses)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_PROCESS_MODEL": "test-model"}), patch.object(
            process_builder, "getOpenAPIClient", return_value=client
        ):
            response = self.client.post("/api/process-builder/chat", json={"message": "Map our intake flow."})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api"], "responses")
        self.assertEqual(response.json()["model"], "test-model")
        self.assertEqual(response.json()["result"], result)

    def test_decision_gateways_are_reconciled_as_reusable_foundry_components(self):
        payload = self.process_payload()
        payload["elements"].insert(2, {"id": "approved", "type": "bpmn:ExclusiveGateway", "name": "Approved?"})
        payload["connections"] = [
            {"id": "f1", "from": "start", "to": "review"},
            {"id": "f2", "from": "review", "to": "approved"},
            {"id": "f3", "from": "approved", "to": "record", "label": "Yes"},
            {"id": "f4", "from": "record", "to": "end"},
        ]

        saved = self.client.post("/api/process-builder/processes", json=payload).json()

        self.assertEqual(saved["reconciliation"]["checked"], 3)
        decision = next(item for item in saved["process"]["elements"] if item["id"] == "approved")
        component_id = decision["component_id"]
        component = next(item for item in self.client.get("/api/process-builder/components").json()["components"] if item["id"] == component_id)
        self.assertEqual(component["kind"], "decision")

    def test_deletion_requires_confirmation_and_protects_used_components(self):
        created = self.client.post("/api/process-builder/processes", json=self.process_payload()).json()["process"]
        blocked = self.client.delete(f"/api/process-builder/processes/{created['id']}")
        deleted = self.client.delete(f"/api/process-builder/processes/{created['id']}?confirmed=true")

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(deleted.status_code, 200)


class ProcessBuilderFrontendTests(unittest.TestCase):
    def test_page_is_devready_branded_and_keeps_manual_plus_ai_workflows(self):
        html = (PAGES / "process-builder.html").read_text(encoding="utf-8")

        self.assertIn("DevReady - Process Builder", html)
        self.assertIn("AI Intake", html)
        self.assertIn("Save + reconcile Foundry", html)
        self.assertIn("BPMN", html)
        self.assertNotIn("Syntax Process Forge", html)
        self.assertNotIn("SAP Cloud ALM", html)
        self.assertNotIn("HPCC", html)

    def test_frontend_links_diagram_to_foundry_code_api_and_mcp(self):
        script = (PAGES / "JS" / "processBuilder.js").read_text(encoding="utf-8")

        self.assertIn("renderReferenceOverlays", script)
        self.assertIn("componentId", script)
        self.assertIn("codeRefs", script)
        self.assertIn("apiEndpoints", script)
        self.assertIn("mcpTools", script)
        self.assertIn("deleteComponent", script)
        self.assertIn("library-delete", script)
        self.assertIn("/mcp/manifest", (PAGES / "process-builder.html").read_text(encoding="utf-8"))

    def test_shared_navigation_exposes_builder_and_foundry(self):
        navigation = (PAGES / "components" / "sideNav.html").read_text(encoding="utf-8")

        self.assertIn('href="process-builder.html"', navigation)
        self.assertIn('href="process-builder.html#foundry"', navigation)
        self.assertIn('data-menu-key="process_builder"', navigation)
        self.assertIn('data-menu-key="foundry"', navigation)


if __name__ == "__main__":
    unittest.main()
