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
        self.assertEqual(data["product"], "aiReady Application Factory")
        self.assertEqual(data["process_builder"], "DevReady Process Builder")
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
        self.assertEqual(initialize.json()["result"]["serverInfo"]["name"], "aiready-application-factory")
        tool_names = {item["name"] for item in tools.json()["result"]["tools"]}
        self.assertEqual(
            tool_names,
            {
                "list_applications",
                "get_application",
                "list_portfolios",
                "get_portfolio",
                "list_processes",
                "get_process",
                "find_components",
                "get_traceability",
            },
        )
        manifest = self.client.get("/api/process-builder/mcp/manifest").json()
        self.assertTrue(manifest["read_only"])

    def test_application_blueprint_separates_design_cards_from_reusable_implementations(self):
        process = self.process_payload(name="Phase 01 Intake", activity="Review client intake")
        process.update({"temp_id": "phase-01", "phase_id": "phase-01", "phase_name": "Intake", "phase_order": 1})
        saved = self.client.post(
            "/api/process-builder/portfolios",
            json={
                "client_name": "Aularis",
                "portfolio": {"name": "Aularis lifecycle", "expected_process_count": 1, "lanes": [], "handoffs": []},
                "processes": [process],
            },
        ).json()

        blueprint = self.client.post(
            "/api/process-builder/applications",
            json={"portfolio_id": saved["portfolio"]["id"], "name": "Aularis Process Operations"},
        )

        self.assertEqual(blueprint.status_code, 201)
        application = blueprint.json()["application"]
        self.assertEqual(application["summary"]["processes"], 1)
        self.assertGreaterEqual(application["summary"]["build_required"], 9)
        review_requirement = next(item for item in application["requirements"] if item["name"] == "Review client intake")
        self.assertEqual(review_requirement["resolution"], "build-required")
        self.assertFalse(review_requirement["readiness"]["implemented"])
        self.assertFalse(next(gate for gate in application["release_gates"] if gate["key"] == "components-implemented")["passed"])

    def test_verified_component_manifest_upgrades_matching_application_requirement(self):
        process = self.process_payload(name="Phase 01 Intake", activity="Review client intake")
        process.update({"temp_id": "phase-01", "phase_id": "phase-01", "phase_name": "Intake", "phase_order": 1})
        saved = self.client.post(
            "/api/process-builder/portfolios",
            json={
                "client_name": "Aularis",
                "portfolio": {"name": "Aularis lifecycle", "expected_process_count": 1, "lanes": [], "handoffs": []},
                "processes": [process],
            },
        ).json()
        imported = self.client.post(
            "/api/process-builder/components/import",
            json={
                "source": {
                    "application": "Aularis",
                    "repository": "https://github.com/dcjoncas/AULARIS",
                    "commit": "abc123",
                    "version": "1.0.0",
                    "verified_at": "2026-08-30T00:00:00Z",
                },
                "components": [
                    {
                        "key": "aularis-intake-review",
                        "name": "Aularis Intake Review",
                        "kind": "workflow-module",
                        "status": "ready",
                        "implementation_status": "implemented",
                        "supported_activities": ["Review client intake"],
                        "code_refs": ["server.py"],
                        "api_endpoints": ["PATCH /api/intake-records/{intake_id}"],
                        "test_refs": ["tests/test_app.py::test_intake_review"],
                    }
                ],
            },
        )
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["ready"], 1)

        application = self.client.post(
            "/api/process-builder/applications",
            json={"portfolio_id": saved["portfolio"]["id"], "name": "Aularis Process Operations"},
        ).json()["application"]
        requirement = next(item for item in application["requirements"] if item["name"] == "Review client intake")
        self.assertEqual(requirement["resolution"], "reuse")
        self.assertEqual(requirement["component_name"], "Aularis Intake Review")
        self.assertTrue(requirement["readiness"]["provenance_verified"])

    def test_component_build_spec_is_saved_but_does_not_claim_implementation(self):
        component = self.client.post(
            "/api/process-builder/components",
            json={"name": "Missing workflow", "implementation_status": "missing"},
        ).json()["component"]
        response = self.client.post(
            f"/api/process-builder/components/{component['id']}/build-spec",
            json={
                "build_spec": {
                    "summary": "Implement the missing workflow.",
                    "acceptance_criteria": ["Tenant-scoped result is retained"],
                    "data_entities": ["workflow_record"],
                    "api_contracts": ["POST /api/workflows"],
                    "mcp_contracts": ["workflows.create"],
                    "security_controls": ["Human approval"],
                    "test_scenarios": ["Reject cross-tenant access"],
                    "dependencies": ["PostgreSQL"],
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["component"]
        self.assertEqual(updated["implementation_status"], "build-planned")
        self.assertEqual(updated["status"], "review")
        self.assertFalse(process_builder._component_is_implemented(updated))

    def test_ai_chat_uses_server_key_and_has_explicit_unconfigured_state(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = self.client.post("/api/process-builder/chat", json={"message": "Map our intake flow."})

        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENAI_API_KEY", response.json()["detail"])

    def test_ai_chat_uses_responses_api_structured_portfolio_plan_schema(self):
        result = {
            "assistant_message": "I drafted the flow for review.",
            "needs_clarification": False,
            "discovery_complete": True,
            "client_name": "Northwind",
            "portfolio": {
                "temp_id": "northwind",
                "name": "Northwind portfolio",
                "purpose": "Connected operations",
                "expected_process_count": 1,
                "lanes": [],
                "handoffs": [],
            },
            "processes": [],
        }
        captured = {}

        def create_response(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text=json.dumps(result))

        responses = SimpleNamespace(create=create_response)
        client = SimpleNamespace(responses=responses)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_PROCESS_MODEL": "test-model"}), patch.object(
            process_builder, "getOpenAPIClient", return_value=client
        ):
            response = self.client.post("/api/process-builder/chat", json={"message": "Map our intake flow."})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api"], "responses")
        self.assertEqual(response.json()["model"], "test-model")
        self.assertEqual(response.json()["result"]["portfolio"]["name"], "Northwind portfolio")
        process_schema = captured["text"]["format"]["schema"]["properties"]["processes"]
        self.assertEqual(process_schema["maxItems"], 12)
        self.assertIn("activity_names", process_schema["items"]["properties"])
        self.assertNotIn("steps", process_schema["items"]["properties"])
        self.assertEqual(captured["max_output_tokens"], 12000)

    def test_ai_chat_expands_each_planned_phase_with_bounded_schema(self):
        phase_plan = {
            "temp_id": "phase-01",
            "phase_id": "phase-01",
            "phase_name": "Intake",
            "phase_order": 1,
            "variant": "shared",
            "lane_names": ["Operations"],
            "entry_criteria": ["Opportunity accepted"],
            "exit_criteria": ["Intake complete"],
            "predecessor_temp_ids": [],
            "successor_temp_ids": [],
            "name": "Client intake",
            "purpose": "Collect client data",
            "owner": "Operations",
            "scope": "Intake",
            "trigger": "Opportunity accepted",
            "outcome": "Complete file",
            "inputs": ["Client request"],
            "outputs": ["Complete file"],
            "systems": [],
            "controls": ["Identity review"],
            "kpis": ["Cycle time"],
            "activity_names": ["Collect client data"],
        }
        plan = {
            "assistant_message": "Drafted one phase.",
            "needs_clarification": False,
            "discovery_complete": True,
            "client_name": "Northwind",
            "portfolio": {
                "temp_id": "northwind",
                "name": "Northwind portfolio",
                "purpose": "Connected operations",
                "expected_process_count": 1,
                "lanes": [],
                "handoffs": [],
            },
            "processes": [phase_plan],
        }
        expanded = {
            **{key: value for key, value in phase_plan.items() if key != "activity_names"},
            "steps": [
                {
                    "id": "start",
                    "name": "Opportunity accepted",
                    "type": "start_event",
                    "owner": "Operations",
                    "system": "",
                    "description": "Start intake.",
                    "control": "",
                    "sla": "",
                    "code_refs": [],
                    "api_endpoints": [],
                    "mcp_tools": [],
                    "links": [],
                },
                {
                    "id": "collect",
                    "name": "Collect client data",
                    "type": "user_task",
                    "owner": "Operations",
                    "system": "",
                    "description": "Collect the required data.",
                    "control": "Identity review",
                    "sla": "",
                    "code_refs": [],
                    "api_endpoints": [],
                    "mcp_tools": [],
                    "links": [],
                },
                {
                    "id": "end",
                    "name": "Intake complete",
                    "type": "end_event",
                    "owner": "Operations",
                    "system": "",
                    "description": "Complete intake.",
                    "control": "",
                    "sla": "",
                    "code_refs": [],
                    "api_endpoints": [],
                    "mcp_tools": [],
                    "links": [],
                },
            ],
            "connections": [
                {"id": "flow-1", "from": "start", "to": "collect", "label": ""},
                {"id": "flow-2", "from": "collect", "to": "end", "label": ""},
            ],
        }
        captured = []

        def create_response(**kwargs):
            captured.append(kwargs)
            schema_name = kwargs["text"]["format"]["name"]
            value = plan if schema_name == "devready_portfolio_plan" else {
                "steps": expanded["steps"],
                "connections": expanded["connections"],
            }
            return SimpleNamespace(output_text=json.dumps(value), status="completed")

        client = SimpleNamespace(responses=SimpleNamespace(create=create_response))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_PROCESS_MODEL": "gpt-5"}), patch.object(
            process_builder, "getOpenAPIClient", return_value=client
        ):
            response = self.client.post("/api/process-builder/chat", json={"message": "Map our intake flow."})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["text"]["format"]["name"] for item in captured], [
            "devready_portfolio_plan",
            "devready_phase_expansion",
        ])
        process = response.json()["result"]["processes"][0]
        self.assertEqual(process["phase_name"], "Intake")
        self.assertEqual(len(process["steps"]), 3)
        self.assertEqual(captured[1]["max_output_tokens"], 8000)
        self.assertEqual(captured[1]["reasoning"], {"effort": "low"})
        self.assertEqual(set(captured[1]["text"]["format"]["schema"]["properties"]), {"steps", "connections"})

    def test_phase_expansion_repairs_missing_planned_activities_before_returning(self):
        phase_plan = {
            "temp_id": "phase-01",
            "phase_id": "phase-01",
            "phase_name": "Review",
            "phase_order": 1,
            "variant": "shared",
            "lane_names": ["Operations"],
            "entry_criteria": ["Request received"],
            "exit_criteria": ["Request reviewed"],
            "predecessor_temp_ids": [],
            "successor_temp_ids": [],
            "name": "Review request",
            "purpose": "Review",
            "owner": "Operations",
            "scope": "Review",
            "trigger": "Request received",
            "outcome": "Reviewed",
            "inputs": [],
            "outputs": [],
            "systems": [],
            "controls": [],
            "kpis": [],
            "activity_names": ["Review request"],
        }
        invalid = {
            "steps": [
                {"id": "start", "type": "start_event", "name": "Start"},
                {"id": "end", "type": "end_event", "name": "End"},
            ],
            "connections": [{"id": "flow-1", "from": "start", "to": "end", "label": ""}],
        }
        repaired = {
            "steps": [
                {"id": "start", "type": "start_event", "name": "Start"},
                {"id": "review", "type": "user_task", "name": "Review request"},
                {"id": "end", "type": "end_event", "name": "End"},
            ],
            "connections": [
                {"id": "flow-1", "from": "start", "to": "review", "label": ""},
                {"id": "flow-2", "from": "review", "to": "end", "label": ""},
            ],
        }
        captured = []

        def create_response(**kwargs):
            captured.append(kwargs)
            value = invalid if len(captured) == 1 else repaired
            return SimpleNamespace(output_text=json.dumps(value), status="completed")

        result = process_builder._expand_ai_phase(
            client=SimpleNamespace(responses=SimpleNamespace(create=create_response)),
            model="test-model",
            message="Review the request.",
            portfolio={"name": "Test"},
            phase_plan=phase_plan,
            adjacent_phases=[],
        )

        self.assertEqual(len(captured), 2)
        repair_context = json.loads(captured[1]["input"][0]["content"])["repair"]
        self.assertTrue(any("Review request" in item for item in repair_context["validation_errors"]))
        self.assertEqual(result["source_activity_names"], ["Review request"])
        self.assertEqual(len(result["steps"]), 3)

    def test_phase_aware_interpretation_preserves_eight_connected_processes_and_removes_lane(self):
        processes = []
        for index in range(1, 9):
            processes.append(
                {
                    "temp_id": f"phase-{index}",
                    "phase_id": f"phase-{index:02d}",
                    "phase_name": f"Phase {index}",
                    "phase_order": index,
                    "variant": "shared",
                    "lane_names": ["Operations", "Solution Architect"],
                    "entry_criteria": ["Prior phase complete"],
                    "exit_criteria": ["Phase complete"],
                    "predecessor_temp_ids": [],
                    "successor_temp_ids": [],
                    "name": f"Phase {index}",
                    "purpose": "Test",
                    "owner": "Operations",
                    "scope": "Test",
                    "trigger": "Start",
                    "outcome": "Complete",
                    "inputs": [],
                    "outputs": [f"Phase {index} result"],
                    "systems": [],
                    "controls": [],
                    "kpis": [],
                    "steps": [
                        {"id": "start", "type": "start_event"},
                        {"id": "end", "type": "end_event"},
                    ],
                    "connections": [{"id": "flow", "from": "start", "to": "end", "label": ""}],
                }
            )
        normalized = process_builder._normalize_ai_discovery(
            {
                "assistant_message": "Drafted.",
                "needs_clarification": True,
                "discovery_complete": False,
                "client_name": "Aularis",
                "portfolio": {
                    "temp_id": "aularis",
                    "name": "Aularis lifecycle",
                    "purpose": "Eight phases",
                    "expected_process_count": 8,
                    "lanes": [
                        {"id": "ops", "name": "Operations", "accountable": "Ops", "description": ""},
                        {
                            "id": "sa",
                            "name": "Solution Architect",
                            "accountable": "Nate",
                            "description": "Remove this lane",
                        },
                    ],
                    "handoffs": [],
                },
                "processes": processes,
            },
            "There are Phase 01 through Phase 08. Remove the Solution Architecture & Technical lane. "
            "Model Phase 08 renewal as a loop back to Phase 01.",
        )

        self.assertEqual(len(normalized["processes"]), 8)
        self.assertEqual(len(normalized["portfolio"]["handoffs"]), 8)
        self.assertEqual([item["phase_order"] for item in normalized["processes"]], list(range(1, 9)))
        self.assertEqual([item["name"] for item in normalized["portfolio"]["lanes"]], ["Operations"])
        self.assertTrue(normalized["interpretation"]["removed_solution_architect_lane"])
        self.assertTrue(normalized["interpretation"]["complete_phase_coverage"])
        self.assertTrue(normalized["interpretation"]["structurally_complete"])
        self.assertEqual(normalized["interpretation"]["invalid_handoff_count"], 0)
        self.assertTrue(normalized["discovery_complete"])
        self.assertTrue(normalized["needs_clarification"])
        self.assertIn("phase-8", normalized["processes"][0]["predecessor_temp_ids"])
        self.assertIn("phase-1", normalized["processes"][-1]["successor_temp_ids"])

    def test_portfolio_save_is_atomic_connected_and_reuses_processes_and_components(self):
        first = self.process_payload(name="Phase 01 Intake", activity="Shared client review")
        first.update(
            {
                "temp_id": "phase-01",
                "phase_id": "phase-01",
                "phase_name": "Intake",
                "phase_order": 1,
                "successor_process_ids": ["phase-02"],
            }
        )
        second = self.process_payload(name="Phase 02 Approval", activity="Shared client review")
        second.update(
            {
                "temp_id": "phase-02",
                "phase_id": "phase-02",
                "phase_name": "Approval",
                "phase_order": 2,
                "predecessor_process_ids": ["phase-01"],
            }
        )
        payload = {
            "client_name": "Aularis",
            "portfolio": {
                "temp_id": "aularis-tax",
                "name": "Aularis Tax Equity Lifecycle",
                "purpose": "Connected phases",
                "expected_process_count": 2,
                "lanes": [
                    {"id": "client", "name": "Client", "accountable": "Client", "description": ""}
                ],
                "handoffs": [
                    {
                        "id": "h1",
                        "from_process_temp_id": "phase-01",
                        "to_process_temp_id": "phase-02",
                        "condition": "Intake complete",
                        "artifact": "Client file",
                        "description": "Normal handoff",
                    }
                ],
            },
            "processes": [first, second],
        }

        created = self.client.post("/api/process-builder/portfolios", json=payload)
        self.assertEqual(created.status_code, 201)
        created_data = created.json()
        self.assertTrue(created_data["created"])
        self.assertEqual(len(created_data["processes"]), 2)
        self.assertEqual(len(created_data["reconciliation"]["created"]), 2)
        self.assertEqual(len(created_data["reconciliation"]["reused"]), 2)
        first_id, second_id = created_data["portfolio"]["process_ids"]
        self.assertEqual(created_data["portfolio"]["handoffs"][0]["from_process_id"], first_id)
        self.assertEqual(created_data["portfolio"]["handoffs"][0]["to_process_id"], second_id)
        self.assertEqual(created_data["processes"][0]["successor_process_ids"], [second_id])
        self.assertEqual(created_data["processes"][1]["predecessor_process_ids"], [first_id])

        updated = self.client.post("/api/process-builder/portfolios", json=payload).json()
        self.assertFalse(updated["created"])
        self.assertEqual(updated["portfolio"]["process_ids"], [first_id, second_id])
        self.assertEqual(self.client.get("/api/process-builder/portfolios").json()["count"], 1)
        self.assertEqual(self.client.get("/api/process-builder/processes").json()["count"], 2)

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

        self.assertIn("aiReady Application Factory", html)
        self.assertIn("AI Intake", html)
        self.assertIn("Save process + check Foundry", html)
        self.assertIn('maxlength="30000"', html)
        self.assertIn("up to 12 named phases", html)
        self.assertIn("BPMN", html)
        self.assertIn("Create / refresh blueprint", html)
        self.assertIn("Import verified manifest", html)
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
        self.assertIn("/portfolios", script)
        self.assertIn("Save connected portfolio", script)
        self.assertIn("await loadXml(savedActive.bpmn_xml, savedActive)", script)
        self.assertIn("source_activity_names", script)
        self.assertIn("slice(0, 60)", script)
        self.assertIn("slice(0, 12)", script)
        self.assertIn("/mcp/manifest", (PAGES / "process-builder.html").read_text(encoding="utf-8"))

    def test_shared_navigation_exposes_one_application_factory(self):
        navigation = (PAGES / "components" / "sideNav.html").read_text(encoding="utf-8")

        self.assertIn('href="process-builder.html"', navigation)
        self.assertIn('data-menu-key="process_builder"', navigation)
        self.assertIn('>Application Factory</a', navigation)
        self.assertNotIn('data-menu-key="foundry"', navigation)


if __name__ == "__main__":
    unittest.main()
