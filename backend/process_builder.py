"""aiReady Application Factory APIs.

The browser owns BPMN rendering and manual editing. This module owns durable
process records, reusable component reconciliation, application assembly plans,
deterministic validation, AI-assisted discovery, traceability, and a read-only
MCP catalog surface.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response

from openAI.client import getOpenAPIClient


router = APIRouter(prefix="/api/process-builder", tags=["DevReady Process Builder"])

MODULE_DIR = Path(__file__).resolve().parent
STORE_PATH = Path(
    os.getenv(
        "PROCESS_BUILDER_STORE_PATH",
        str(MODULE_DIR / "data" / "devready_process_builder.json"),
    )
)
STORE_LOCK = threading.RLock()
STORAGE_STATE = "json"
MAX_BPMN_BYTES = 3_000_000
MAX_PROCESS_ELEMENTS = 250
MAX_PROCESS_CONNECTIONS = 500
MAX_AI_PROCESSES = 12
MAX_AI_MESSAGE_CHARS = 30_000
MAX_APPLICATION_REQUIREMENTS = 1_000

DELIVERY_PROFILES = {
    "rapid": {
        "label": "Rapid Railway",
        "description": "Fast, governed delivery for a standard business application without unnecessary platform complexity.",
        "deployment_target": "railway",
        "availability": "standard",
        "environment_names": ["development", "production"],
        "release_strategy": "promote-one-artifact",
    },
    "business-critical": {
        "label": "Business Critical",
        "description": "Stronger availability, recovery, observability, and staged promotion for operationally important applications.",
        "deployment_target": "railway",
        "availability": "high-availability",
        "environment_names": ["development", "staging", "production"],
        "release_strategy": "progressive-promotion",
    },
    "enterprise": {
        "label": "Enterprise Fabric",
        "description": "Container-orchestrated delivery for applications whose measured scale, isolation, or resilience requirements justify Kubernetes.",
        "deployment_target": "kubernetes",
        "availability": "multi-zone",
        "environment_names": ["development", "staging", "production"],
        "release_strategy": "progressive-promotion",
    },
}

DELIVERY_CHOICES = {
    "repository_strategy": {"new-repository", "existing-repository", "monorepo"},
    "deployment_target": {"railway", "kubernetes", "hybrid"},
    "availability": {"standard", "high-availability", "multi-zone"},
    "pipeline_provider": {"github-actions", "azure-devops", "gitlab-ci"},
}

STANDARD_FOUNDATION_REQUIREMENTS = [
    {
        "key": "multitenant-identity-access",
        "name": "Multi-tenant Identity & Access",
        "kind": "platform-service",
        "description": "Tenant-isolated authentication, authorization, session security, role administration, and access audit evidence.",
    },
    {
        "key": "general-administration",
        "name": "General Administration",
        "kind": "platform-service",
        "description": "Tenant, user, role, environment, configuration, and operational administration controls.",
    },
    {
        "key": "postgres-tenant-data",
        "name": "PostgreSQL Multi-tenant Data",
        "kind": "data-service",
        "description": "PostgreSQL persistence with tenant keys, tenant-scoped access, migrations, backup, and recovery controls.",
    },
    {
        "key": "agent-aware-context",
        "name": "Agent-aware Context",
        "kind": "agent-service",
        "description": "Authenticated tenant, user, client, process, activity, and approval context for every agent request.",
    },
    {
        "key": "rag-knowledge-retrieval",
        "name": "RAG Knowledge Retrieval",
        "kind": "rag-service",
        "description": "Source-linked retrieval-augmented generation with tenant filtering, vector search, citations, and retention controls.",
    },
    {
        "key": "meeting-invitation-orchestration",
        "name": "Meeting Invitation Orchestration",
        "kind": "integration-service",
        "description": "Idempotent meeting creation, participant invitations, calendar publishing, agent consent, capture, and follow-up routing.",
    },
    {
        "key": "signature-governance",
        "name": "Signature Governance",
        "kind": "integration-service",
        "description": "Version-locked document preparation, governed fields, signer capacity, provider evidence, signed-artifact review, and human approval.",
    },
    {
        "key": "api-mcp-administration",
        "name": "API & MCP Administration",
        "kind": "platform-service",
        "description": "API and MCP catalog, connection health, permissions, rate controls, audit evidence, and environment configuration.",
    },
    {
        "key": "human-approval-audit",
        "name": "Human Approval & Audit",
        "kind": "control-service",
        "description": "Explicit approval gates and immutable evidence for consequential legal, tax, investment, signature, money-movement, and release actions.",
    },
]

PROCESS_NODE_TYPES = {
    "bpmn:Task",
    "bpmn:UserTask",
    "bpmn:ServiceTask",
    "bpmn:ManualTask",
    "bpmn:BusinessRuleTask",
    "bpmn:SendTask",
    "bpmn:ReceiveTask",
    "bpmn:CallActivity",
    "bpmn:StartEvent",
    "bpmn:EndEvent",
    "bpmn:ExclusiveGateway",
    "bpmn:ParallelGateway",
    "bpmn:InclusiveGateway",
    "bpmn:EventBasedGateway",
    "bpmn:IntermediateCatchEvent",
    "bpmn:IntermediateThrowEvent",
    "bpmn:DataObjectReference",
    "bpmn:DataStoreReference",
}
REUSABLE_NODE_TYPES = {
    "bpmn:Task",
    "bpmn:UserTask",
    "bpmn:ServiceTask",
    "bpmn:ManualTask",
    "bpmn:BusinessRuleTask",
    "bpmn:SendTask",
    "bpmn:ReceiveTask",
    "bpmn:CallActivity",
    "bpmn:ExclusiveGateway",
    "bpmn:ParallelGateway",
    "bpmn:InclusiveGateway",
    "bpmn:EventBasedGateway",
}
INTEGRATION_NODE_TYPES = {
    "bpmn:ServiceTask",
    "bpmn:SendTask",
    "bpmn:ReceiveTask",
    "bpmn:CallActivity",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, *, limit: int = 30, item_limit: int = 500) -> list[str]:
    if isinstance(value, str):
        source = [line.strip() for line in re.split(r"[\r\n,]+", value)]
    elif isinstance(value, list):
        source = value
    else:
        source = []
    result: list[str] = []
    seen: set[str] = set()
    for raw in source:
        item = _text(raw, item_limit)
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _slug(value: Any) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", _text(value, 200).casefold()).strip("-")
    return clean[:120] or "component"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _seed_store() -> dict[str, Any]:
    now = _utc_now()
    return {
        "schema_version": 4,
        "updated_at": now,
        "portfolios": [],
        "processes": [],
        "applications": [],
        "components": [
            {
                "id": "cmp_devready_ai_intake",
                "name": "DevReady AI Process Intake",
                "slug": "devready-ai-process-intake",
                "kind": "agent",
                "status": "ready",
                "version": "1.0.0",
                "description": "Turns a client's plain-language business-flow description into reviewable process drafts without applying changes automatically.",
                "aliases": ["AI process discovery", "business flow intake"],
                "implementation_status": "implemented",
                "code_refs": ["backend/process_builder.py"],
                "api_endpoints": ["POST /api/process-builder/chat"],
                "mcp_tools": [],
                "links": ["/ui/pages/process-builder.html?domain=dev#intake"],
                "used_by_processes": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "cmp_foundry_reconcile",
                "name": "AIReady Foundry Reconciliation",
                "slug": "aiready-foundry-reconciliation",
                "kind": "service",
                "status": "ready",
                "version": "1.0.0",
                "description": "Checks every reusable process activity against the Foundry before linking an existing component or registering a new draft component.",
                "aliases": ["component existence check", "component reconciliation"],
                "implementation_status": "implemented",
                "code_refs": ["backend/process_builder.py"],
                "api_endpoints": [
                    "POST /api/process-builder/components/check",
                    "POST /api/process-builder/processes",
                ],
                "mcp_tools": ["find_components", "get_traceability"],
                "links": ["/ui/pages/process-builder.html?domain=dev#foundry"],
                "used_by_processes": [],
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "cmp_foundry_mcp_catalog",
                "name": "AIReady Foundry MCP Catalog",
                "slug": "aiready-foundry-mcp-catalog",
                "kind": "mcp-server",
                "status": "ready",
                "version": "1.0.0",
                "description": "Read-only MCP tools for discovering DevReady processes, reusable components, and traceability evidence.",
                "aliases": ["process MCP", "Foundry MCP"],
                "implementation_status": "implemented",
                "code_refs": ["backend/process_builder.py"],
                "api_endpoints": [
                    "GET /api/process-builder/mcp/manifest",
                    "POST /api/process-builder/mcp",
                ],
                "mcp_tools": [
                    "list_processes",
                    "get_process",
                    "find_components",
                    "get_traceability",
                ],
                "links": ["/api/process-builder/mcp/manifest"],
                "used_by_processes": [],
                "created_at": now,
                "updated_at": now,
            },
        ],
    }


def _database_configured() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in (
            "AZURE_DATABASE_HOST",
            "AZURE_DATABASE_NAME",
            "AZURE_DATABASE_USER",
            "AZURE_DATABASE_PASSWORD",
        )
    ) and os.getenv("PROCESS_BUILDER_DATABASE_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _read_store_database() -> dict[str, Any] | None:
    global STORAGE_STATE
    from azureUtils.storage import client as azure_client

    connection = None
    try:
        connection = azure_client.getConnection()
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS devready_process_builder_store (
              id TEXT PRIMARY KEY,
              data JSONB NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("SELECT data FROM devready_process_builder_store WHERE id = %s", ("default",))
        row = cursor.fetchone()
        connection.commit()
        STORAGE_STATE = "postgres"
        return row[0] if row and isinstance(row[0], dict) else None
    finally:
        if connection:
            connection.close()


def _write_store_database(data: dict[str, Any]) -> None:
    global STORAGE_STATE
    from azureUtils.storage import client as azure_client
    from psycopg.types.json import Jsonb

    connection = None
    try:
        connection = azure_client.getConnection()
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS devready_process_builder_store (
              id TEXT PRIMARY KEY,
              data JSONB NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO devready_process_builder_store (id, data, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
            """,
            ("default", Jsonb(data)),
        )
        connection.commit()
        STORAGE_STATE = "postgres"
    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()


def _read_store() -> dict[str, Any]:
    global STORAGE_STATE
    with STORE_LOCK:
        database_needs_seed = False
        if _database_configured():
            try:
                database_data = _read_store_database()
                if database_data is not None:
                    data = database_data
                    data["schema_version"] = max(4, int(data.get("schema_version") or 0))
                    data.setdefault("portfolios", [])
                    data.setdefault("processes", [])
                    data.setdefault("applications", [])
                    data.setdefault("components", [])
                    if not data["components"]:
                        data["components"] = _seed_store()["components"]
                        _write_store_unlocked(data)
                    return data
                database_needs_seed = True
            except Exception:
                STORAGE_STATE = "json-fallback"
        if not STORE_PATH.exists():
            data = _seed_store()
            _write_store_unlocked(data)
            return data
        try:
            data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"Process Builder store is unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=500, detail="Process Builder store has an invalid root object.")
        data["schema_version"] = max(4, int(data.get("schema_version") or 0))
        data.setdefault("portfolios", [])
        data.setdefault("processes", [])
        data.setdefault("applications", [])
        data.setdefault("components", [])
        if not data["components"]:
            data["components"] = _seed_store()["components"]
            _write_store_unlocked(data)
        elif database_needs_seed:
            _write_store_unlocked(data)
        return data


def _write_store_unlocked(data: dict[str, Any]) -> None:
    global STORAGE_STATE
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _utc_now()
    database_saved = False
    if _database_configured():
        try:
            _write_store_database(data)
            database_saved = True
        except Exception:
            STORAGE_STATE = "json-fallback"
    try:
        temporary = STORE_PATH.with_suffix(f"{STORE_PATH.suffix}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(STORE_PATH)
        if not database_saved and STORAGE_STATE != "json-fallback":
            STORAGE_STATE = "json"
    except OSError:
        if not database_saved:
            raise


def _write_store(data: dict[str, Any]) -> None:
    with STORE_LOCK:
        _write_store_unlocked(data)


def _sanitize_element(raw: Any, index: int) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    node_type = _text(item.get("type"), 80)
    if node_type not in PROCESS_NODE_TYPES:
        node_type = "bpmn:Task"
    element_id = _text(item.get("id"), 160) or f"Element_{index + 1}"
    return {
        "id": element_id,
        "type": node_type,
        "name": _text(item.get("name"), 240),
        "description": _text(item.get("description") or item.get("documentation"), 4_000),
        "owner": _text(item.get("owner"), 240),
        "system": _text(item.get("system"), 240),
        "control": _text(item.get("control"), 1_000),
        "sla": _text(item.get("sla"), 240),
        "component_id": _text(item.get("component_id") or item.get("componentId"), 160),
        "code_refs": _string_list(item.get("code_refs") or item.get("codeRefs")),
        "api_endpoints": _string_list(item.get("api_endpoints") or item.get("apiEndpoints")),
        "mcp_tools": _string_list(item.get("mcp_tools") or item.get("mcpTools")),
        "links": _string_list(item.get("links")),
    }


def _sanitize_connection(raw: Any, index: int) -> dict[str, str]:
    item = raw if isinstance(raw, dict) else {}
    return {
        "id": _text(item.get("id"), 160) or f"Flow_{index + 1}",
        "from": _text(item.get("from") or item.get("source"), 160),
        "to": _text(item.get("to") or item.get("target"), 160),
        "label": _text(item.get("label") or item.get("name"), 240),
    }


def _sanitize_lane(raw: Any, index: int) -> dict[str, str]:
    item = raw if isinstance(raw, dict) else {}
    return {
        "id": _text(item.get("id"), 160) or f"lane_{index + 1}",
        "name": _text(item.get("name"), 240),
        "accountable": _text(item.get("accountable") or item.get("owner"), 240),
        "description": _text(item.get("description"), 1_000),
    }


def _sanitize_handoff(raw: Any, index: int) -> dict[str, str]:
    item = raw if isinstance(raw, dict) else {}
    return {
        "id": _text(item.get("id"), 160) or f"handoff_{index + 1}",
        "from_process_id": _text(
            item.get("from_process_id") or item.get("from_process_temp_id") or item.get("from"),
            160,
        ),
        "to_process_id": _text(
            item.get("to_process_id") or item.get("to_process_temp_id") or item.get("to"),
            160,
        ),
        "condition": _text(item.get("condition"), 1_000),
        "artifact": _text(item.get("artifact"), 1_000),
        "description": _text(item.get("description"), 1_000),
    }


def _phase_order(value: Any) -> int:
    try:
        return max(0, min(99, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _sanitize_process_payload(payload: dict[str, Any], *, process_id: str = "") -> dict[str, Any]:
    name = _text(payload.get("name") or payload.get("title"), 240)
    if not name:
        raise HTTPException(status_code=400, detail="Process name is required.")
    bpmn_xml = str(payload.get("bpmn_xml") or payload.get("bpmnXml") or "")
    if len(bpmn_xml.encode("utf-8")) > MAX_BPMN_BYTES:
        raise HTTPException(status_code=413, detail="BPMN document is too large.")
    elements = [
        _sanitize_element(raw, index)
        for index, raw in enumerate((payload.get("elements") or [])[:MAX_PROCESS_ELEMENTS])
    ]
    connections = [
        _sanitize_connection(raw, index)
        for index, raw in enumerate((payload.get("connections") or [])[:MAX_PROCESS_CONNECTIONS])
    ]
    domain = _text(payload.get("domain"), 40).lower() or "dev"
    if domain not in {"dev", "engineer", "law", "dental"}:
        domain = "dev"
    return {
        "id": process_id or _text(payload.get("id"), 160) or _new_id("proc"),
        "name": name,
        "slug": _slug(name),
        "client_name": _text(payload.get("client_name") or payload.get("clientName"), 240),
        "domain": domain,
        "status": _text(payload.get("status"), 40) or "draft",
        "portfolio_id": _text(payload.get("portfolio_id") or payload.get("portfolioId"), 160),
        "portfolio_name": _text(payload.get("portfolio_name") or payload.get("portfolioName"), 240),
        "portfolio_key": _text(payload.get("portfolio_key") or payload.get("portfolioKey"), 240),
        "phase_id": _text(payload.get("phase_id") or payload.get("phaseId"), 160),
        "phase_name": _text(payload.get("phase_name") or payload.get("phaseName"), 240),
        "phase_order": _phase_order(payload.get("phase_order") or payload.get("phaseOrder")),
        "variant": _text(payload.get("variant"), 80) or "shared",
        "lane_names": _string_list(payload.get("lane_names") or payload.get("laneNames"), limit=30, item_limit=240),
        "entry_criteria": _string_list(
            payload.get("entry_criteria") or payload.get("entryCriteria"), limit=30, item_limit=1_000
        ),
        "exit_criteria": _string_list(
            payload.get("exit_criteria") or payload.get("exitCriteria"), limit=30, item_limit=1_000
        ),
        "source_activity_names": _string_list(
            payload.get("source_activity_names") or payload.get("sourceActivityNames"),
            limit=60,
            item_limit=500,
        ),
        "predecessor_process_ids": _string_list(
            payload.get("predecessor_process_ids") or payload.get("predecessorProcessIds"),
            limit=30,
            item_limit=160,
        ),
        "successor_process_ids": _string_list(
            payload.get("successor_process_ids") or payload.get("successorProcessIds"),
            limit=30,
            item_limit=160,
        ),
        "owner": _text(payload.get("owner"), 240),
        "purpose": _text(payload.get("purpose") or payload.get("description"), 4_000),
        "scope": _text(payload.get("scope"), 4_000),
        "trigger": _text(payload.get("trigger"), 1_000),
        "outcome": _text(payload.get("outcome"), 1_000),
        "inputs": _string_list(payload.get("inputs")),
        "outputs": _string_list(payload.get("outputs")),
        "systems": _string_list(payload.get("systems")),
        "controls": _string_list(payload.get("controls")),
        "kpis": _string_list(payload.get("kpis")),
        "bpmn_xml": bpmn_xml,
        "elements": elements,
        "connections": connections,
        "source": _text(payload.get("source"), 80) or "manual",
    }


def _sanitize_portfolio_payload(
    payload: dict[str, Any],
    *,
    portfolio_id: str = "",
    client_name: str = "",
) -> dict[str, Any]:
    name = _text(payload.get("name") or payload.get("title"), 240)
    if not name:
        raise HTTPException(status_code=400, detail="Portfolio name is required.")
    resolved_client = _text(client_name or payload.get("client_name") or payload.get("clientName"), 240)
    lanes = [
        _sanitize_lane(raw, index)
        for index, raw in enumerate((payload.get("lanes") or [])[:30])
        if isinstance(raw, dict)
    ]
    handoffs = [
        _sanitize_handoff(raw, index)
        for index, raw in enumerate((payload.get("handoffs") or [])[:100])
        if isinstance(raw, dict)
    ]
    return {
        "id": portfolio_id or _text(payload.get("id") or payload.get("temp_id"), 160) or _new_id("portfolio"),
        "key": _slug(f"{resolved_client}-{name}"),
        "name": name,
        "client_name": resolved_client,
        "purpose": _text(payload.get("purpose"), 4_000),
        "status": _text(payload.get("status"), 40) or "draft",
        "expected_process_count": max(
            1,
            min(MAX_AI_PROCESSES, _phase_order(payload.get("expected_process_count") or 1)),
        ),
        "lanes": lanes,
        "handoffs": handoffs,
        "process_ids": _string_list(payload.get("process_ids"), limit=MAX_AI_PROCESSES, item_limit=160),
        "source": _text(payload.get("source"), 80) or "ai-intake",
    }


def _sanitize_component_payload(payload: dict[str, Any], *, component_id: str = "") -> dict[str, Any]:
    name = _text(payload.get("name"), 240)
    if not name:
        raise HTTPException(status_code=400, detail="Component name is required.")
    now = _utc_now()
    refs = {
        "code_refs": _string_list(payload.get("code_refs") or payload.get("codeRefs")),
        "api_endpoints": _string_list(payload.get("api_endpoints") or payload.get("apiEndpoints")),
        "mcp_tools": _string_list(payload.get("mcp_tools") or payload.get("mcpTools")),
        "links": _string_list(payload.get("links")),
    }
    provenance_raw = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    build_spec_raw = payload.get("build_spec") if isinstance(payload.get("build_spec"), dict) else {}
    provenance = {
        "source_application": _text(provenance_raw.get("source_application"), 240),
        "source_repository": _text(provenance_raw.get("source_repository"), 1_000),
        "source_commit": _text(provenance_raw.get("source_commit"), 160),
        "manifest_version": _text(provenance_raw.get("manifest_version"), 80),
        "verified_at": _text(provenance_raw.get("verified_at"), 80),
    }
    build_spec = {
        "summary": _text(build_spec_raw.get("summary"), 4_000),
        "acceptance_criteria": _string_list(build_spec_raw.get("acceptance_criteria"), limit=40, item_limit=1_000),
        "data_entities": _string_list(build_spec_raw.get("data_entities"), limit=40, item_limit=500),
        "api_contracts": _string_list(build_spec_raw.get("api_contracts"), limit=60, item_limit=1_000),
        "mcp_contracts": _string_list(build_spec_raw.get("mcp_contracts"), limit=40, item_limit=1_000),
        "security_controls": _string_list(build_spec_raw.get("security_controls"), limit=40, item_limit=1_000),
        "test_scenarios": _string_list(build_spec_raw.get("test_scenarios"), limit=60, item_limit=1_000),
        "dependencies": _string_list(build_spec_raw.get("dependencies"), limit=40, item_limit=500),
        "generated_at": _text(build_spec_raw.get("generated_at"), 80),
        "model": _text(build_spec_raw.get("model"), 120),
    }
    explicit_implementation_status = _text(payload.get("implementation_status"), 80)
    return {
        "id": component_id or _text(payload.get("id"), 160) or _new_id("cmp"),
        "external_key": _text(payload.get("external_key") or payload.get("key"), 200),
        "name": name,
        "slug": _slug(name),
        "kind": _text(payload.get("kind"), 80) or "activity",
        "status": _text(payload.get("status"), 40) or "draft",
        "version": _text(payload.get("version"), 40) or "0.1.0",
        "description": _text(payload.get("description"), 4_000),
        "aliases": _string_list(payload.get("aliases"), limit=20, item_limit=240),
        "implementation_status": explicit_implementation_status
        or ("implemented" if any(refs.values()) else "design-only"),
        "capabilities": _string_list(payload.get("capabilities"), limit=80, item_limit=500),
        "supported_activities": _string_list(
            payload.get("supported_activities") or payload.get("supportedActivities"),
            limit=250,
            item_limit=500,
        ),
        "dependencies": _string_list(payload.get("dependencies"), limit=60, item_limit=500),
        "configuration_keys": _string_list(
            payload.get("configuration_keys") or payload.get("configurationKeys"),
            limit=80,
            item_limit=240,
        ),
        "test_refs": _string_list(payload.get("test_refs") or payload.get("testRefs"), limit=80, item_limit=500),
        "reuse_mode": _text(payload.get("reuse_mode"), 80) or "reference-implementation",
        "standard_foundation": bool(payload.get("standard_foundation")),
        "reusable_across_tenants": bool(payload.get("reusable_across_tenants")),
        "provenance": provenance,
        "build_spec": build_spec,
        **refs,
        "used_by_processes": _string_list(payload.get("used_by_processes"), limit=500, item_limit=160),
        "created_at": _text(payload.get("created_at"), 80) or now,
        "updated_at": now,
    }


def _component_search_keys(component: dict[str, Any]) -> set[str]:
    values = [
        component.get("name"),
        component.get("slug"),
        component.get("external_key"),
        *(component.get("aliases") or []),
        *(component.get("supported_activities") or []),
    ]
    return {_slug(value) for value in values if _text(value)}


def _component_is_implemented(component: dict[str, Any]) -> bool:
    evidence = any(
        component.get(field)
        for field in ("code_refs", "api_endpoints", "mcp_tools", "test_refs")
    )
    return (
        component.get("status") == "ready"
        and component.get("implementation_status") == "implemented"
        and evidence
    )


def _component_readiness(component: dict[str, Any]) -> dict[str, Any]:
    evidence = {
        "code": len(component.get("code_refs") or []),
        "api": len(component.get("api_endpoints") or []),
        "mcp": len(component.get("mcp_tools") or []),
        "tests": len(component.get("test_refs") or []),
        "links": len(component.get("links") or []),
    }
    return {
        "implemented": _component_is_implemented(component),
        "status": component.get("status") or "draft",
        "implementation_status": component.get("implementation_status") or "design-only",
        "evidence": evidence,
        "provenance_verified": bool((component.get("provenance") or {}).get("verified_at")),
    }


def _find_component(components: list[dict[str, Any]], element: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = _text(element.get("component_id"), 160)
    direct = None
    if requested_id:
        direct = next((component for component in components if component.get("id") == requested_id), None)
    key = _slug(element.get("name"))
    if key == "component":
        return None
    exact = [component for component in components if key in _component_search_keys(component)]
    if exact:
        exact.sort(key=lambda item: (_component_is_implemented(item), item is direct), reverse=True)
        return exact[0]
    if direct:
        return direct
    element_refs = {
        "api_endpoints": {item.casefold() for item in element.get("api_endpoints") or []},
        "mcp_tools": {item.casefold() for item in element.get("mcp_tools") or []},
        "code_refs": {item.casefold() for item in element.get("code_refs") or []},
    }
    for component in components:
        if any(
            values and values.intersection({str(item).casefold() for item in component.get(field) or []})
            for field, values in element_refs.items()
        ):
            return component
    return None


def _kind_for_element(node_type: str) -> str:
    return {
        "bpmn:UserTask": "human-task",
        "bpmn:ManualTask": "manual-task",
        "bpmn:ServiceTask": "service",
        "bpmn:BusinessRuleTask": "business-rule",
        "bpmn:SendTask": "message-sender",
        "bpmn:ReceiveTask": "message-receiver",
        "bpmn:CallActivity": "subprocess",
        "bpmn:ExclusiveGateway": "decision",
        "bpmn:ParallelGateway": "parallel-decision",
        "bpmn:InclusiveGateway": "inclusive-decision",
        "bpmn:EventBasedGateway": "event-decision",
    }.get(node_type, "activity")


def _rebuild_component_usage(store: dict[str, Any]) -> None:
    usage: dict[str, set[str]] = {}
    for process in store.get("processes", []):
        for element in process.get("elements", []):
            component_id = _text(element.get("component_id"), 160)
            if component_id:
                usage.setdefault(component_id, set()).add(process.get("id"))
    now = _utc_now()
    for component in store.get("components", []):
        next_usage = sorted(usage.get(component.get("id"), set()))
        if next_usage != component.get("used_by_processes", []):
            component["used_by_processes"] = next_usage
            component["updated_at"] = now


def _reconcile_components(store: dict[str, Any], process: dict[str, Any]) -> dict[str, Any]:
    reused: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    mapping: list[dict[str, str]] = []
    components = store.setdefault("components", [])
    now = _utc_now()
    for element in process.get("elements", []):
        if element.get("type") not in REUSABLE_NODE_TYPES or not element.get("name"):
            continue
        component = _find_component(components, element)
        if component:
            bucket = reused
        else:
            component = _sanitize_component_payload(
                {
                    "name": element.get("name"),
                    "kind": _kind_for_element(element.get("type")),
                    "description": element.get("description"),
                    "code_refs": element.get("code_refs"),
                    "api_endpoints": element.get("api_endpoints"),
                    "mcp_tools": element.get("mcp_tools"),
                    "links": element.get("links"),
                    "status": "draft",
                    "version": "0.1.0",
                }
            )
            component["created_from_process"] = process.get("id")
            component["created_from_element"] = element.get("id")
            components.append(component)
            bucket = created
        element["component_id"] = component["id"]
        component["updated_at"] = now
        bucket.append({"id": component["id"], "name": component["name"]})
        mapping.append({"element_id": element["id"], "component_id": component["id"]})
    return {
        "checked": len(mapping),
        "reused": reused,
        "created": created,
        "mapping": mapping,
    }


def _validation(process: dict[str, Any]) -> dict[str, Any]:
    elements = process.get("elements") or []
    connections = process.get("connections") or []
    element_by_id = {item.get("id"): item for item in elements if item.get("id")}
    issues: list[dict[str, Any]] = []

    def add(severity: str, code: str, message: str, element_id: str = "") -> None:
        issues.append(
            {
                "severity": severity,
                "code": code,
                "message": message,
                "element_id": element_id,
            }
        )

    starts = [item for item in elements if item.get("type") == "bpmn:StartEvent"]
    ends = [item for item in elements if item.get("type") == "bpmn:EndEvent"]
    if not starts:
        add("error", "missing_start", "Add at least one start event.")
    if not ends:
        add("error", "missing_end", "Add at least one end event.")
    if not process.get("owner"):
        add("warning", "missing_owner", "Assign an accountable process owner.")
    if not process.get("purpose"):
        add("warning", "missing_purpose", "Document the business purpose.")

    incoming: dict[str, int] = {key: 0 for key in element_by_id}
    outgoing: dict[str, int] = {key: 0 for key in element_by_id}
    for connection in connections:
        source = connection.get("from")
        target = connection.get("to")
        if source not in element_by_id or target not in element_by_id:
            add("error", "broken_connection", "A connector points to an element that does not exist.", connection.get("id", ""))
            continue
        outgoing[source] += 1
        incoming[target] += 1

    for element in elements:
        element_id = element.get("id", "")
        node_type = element.get("type", "")
        if node_type not in {"bpmn:StartEvent", "bpmn:EndEvent"} and not element.get("name"):
            add("error", "unnamed_element", "Name this process element.", element_id)
        if node_type != "bpmn:StartEvent" and incoming.get(element_id, 0) == 0:
            add("error", "missing_incoming", "Connect this element to the preceding step.", element_id)
        if node_type != "bpmn:EndEvent" and outgoing.get(element_id, 0) == 0:
            add("error", "missing_outgoing", "Connect this element to the next step.", element_id)
        if node_type in REUSABLE_NODE_TYPES and not element.get("component_id"):
            add("warning", "component_unlinked", "Link or register this activity in AIReady Foundry.", element_id)
        if node_type in INTEGRATION_NODE_TYPES and not any(
            element.get(field) for field in ("code_refs", "api_endpoints", "mcp_tools", "links")
        ):
            add("warning", "integration_untraced", "Add a code, API, MCP, or external-link reference for this integration.", element_id)

    errors = sum(issue["severity"] == "error" for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)
    score = max(0, 100 - (errors * 18) - (warnings * 6))
    checklist = [
        {"key": "scope", "label": "Client confirms the trigger, start, end, and exclusions.", "confirmed": False},
        {"key": "owners", "label": "Client confirms owners and handoffs for every activity.", "confirmed": False},
        {"key": "exceptions", "label": "Client confirms decision branches, exceptions, and rework paths.", "confirmed": False},
        {"key": "systems", "label": "Technical owner confirms systems, data, APIs, and MCP tools.", "confirmed": False},
        {"key": "controls", "label": "Business owner confirms controls, evidence, SLAs, and success measures.", "confirmed": False},
    ]
    return {
        "ok": errors == 0,
        "score": score,
        "errors": errors,
        "warnings": warnings,
        "issues": issues,
        "client_validation_checklist": checklist,
        "validated_at": _utc_now(),
    }


def _save_process(payload: dict[str, Any], process_id: str = "") -> tuple[dict[str, Any], dict[str, Any], bool]:
    process = _sanitize_process_payload(payload, process_id=process_id)
    with STORE_LOCK:
        store = _read_store()
        existing_index = next(
            (index for index, item in enumerate(store["processes"]) if item.get("id") == process["id"]),
            None,
        )
        now = _utc_now()
        created = existing_index is None
        if existing_index is None:
            process["created_at"] = now
            process["version"] = 1
        else:
            prior = store["processes"][existing_index]
            process["created_at"] = prior.get("created_at", now)
            process["version"] = int(prior.get("version") or 0) + 1
        process["updated_at"] = now
        reconciliation = _reconcile_components(store, process)
        process["validation"] = _validation(process)
        process["foundry_summary"] = {
            "checked": reconciliation["checked"],
            "reused": len(reconciliation["reused"]),
            "created": len(reconciliation["created"]),
        }
        if existing_index is None:
            store["processes"].append(process)
        else:
            store["processes"][existing_index] = process
        _rebuild_component_usage(store)
        _write_store_unlocked(store)
        return copy.deepcopy(process), reconciliation, created


def _process_summary(process: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(process.get(key))
        for key in (
            "id",
            "name",
            "slug",
            "client_name",
            "domain",
            "status",
            "portfolio_id",
            "portfolio_name",
            "portfolio_key",
            "phase_id",
            "phase_name",
            "phase_order",
            "variant",
            "lane_names",
            "entry_criteria",
            "exit_criteria",
            "source_activity_names",
            "predecessor_process_ids",
            "successor_process_ids",
            "owner",
            "purpose",
            "source",
            "version",
            "foundry_summary",
            "validation",
            "created_at",
            "updated_at",
        )
    } | {
        "element_count": len(process.get("elements") or []),
        "connection_count": len(process.get("connections") or []),
    }


def _portfolio_summary(portfolio: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(portfolio.get(key))
        for key in (
            "id",
            "key",
            "name",
            "client_name",
            "purpose",
            "status",
            "expected_process_count",
            "lanes",
            "handoffs",
            "process_ids",
            "source",
            "version",
            "created_at",
            "updated_at",
        )
    } | {"process_count": len(portfolio.get("process_ids") or [])}


def _traceability(store: dict[str, Any], process: dict[str, Any]) -> dict[str, Any]:
    components = {item.get("id"): item for item in store.get("components", [])}
    rows = []
    for element in process.get("elements", []):
        component = components.get(element.get("component_id"))
        refs = {
            "code_refs": element.get("code_refs") or (component or {}).get("code_refs") or [],
            "api_endpoints": element.get("api_endpoints") or (component or {}).get("api_endpoints") or [],
            "mcp_tools": element.get("mcp_tools") or (component or {}).get("mcp_tools") or [],
            "links": element.get("links") or (component or {}).get("links") or [],
        }
        rows.append(
            {
                "element_id": element.get("id"),
                "element_name": element.get("name"),
                "element_type": element.get("type"),
                "component": copy.deepcopy(component) if component else None,
                **copy.deepcopy(refs),
            }
        )
    return {
        "process_id": process.get("id"),
        "process_name": process.get("name"),
        "rows": rows,
        "mcp_endpoint": "/api/process-builder/mcp",
        "rest_base": "/api/process-builder",
    }


def _output_text(response: Any) -> str:
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    if isinstance(response, dict) and isinstance(response.get("output_text"), str):
        return response["output_text"]
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    for item in output or []:
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        for part in content or []:
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


AI_STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id",
        "name",
        "type",
        "owner",
        "system",
        "description",
        "control",
        "sla",
        "code_refs",
        "api_endpoints",
        "mcp_tools",
        "links",
    ],
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "type": {
            "type": "string",
            "enum": [
                "start_event",
                "end_event",
                "task",
                "user_task",
                "service_task",
                "manual_task",
                "business_rule_task",
                "send_task",
                "receive_task",
                "call_activity",
                "decision",
                "parallel_gateway",
                "inclusive_gateway",
                "event_based_gateway",
                "intermediate_catch_event",
                "intermediate_throw_event",
            ],
        },
        "owner": {"type": "string"},
        "system": {"type": "string"},
        "description": {"type": "string"},
        "control": {"type": "string"},
        "sla": {"type": "string"},
        "code_refs": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
        "api_endpoints": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
        "mcp_tools": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
        "links": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
    },
}

AI_PROCESS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "temp_id",
        "phase_id",
        "phase_name",
        "phase_order",
        "variant",
        "lane_names",
        "entry_criteria",
        "exit_criteria",
        "predecessor_temp_ids",
        "successor_temp_ids",
        "name",
        "purpose",
        "owner",
        "scope",
        "trigger",
        "outcome",
        "inputs",
        "outputs",
        "systems",
        "controls",
        "kpis",
        "steps",
        "connections",
    ],
    "properties": {
        "temp_id": {"type": "string"},
        "phase_id": {"type": "string"},
        "phase_name": {"type": "string"},
        "phase_order": {"type": "integer", "minimum": 1, "maximum": MAX_AI_PROCESSES},
        "variant": {"type": "string", "enum": ["shared", "renewable", "film", "mixed"]},
        "lane_names": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "entry_criteria": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "exit_criteria": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "predecessor_temp_ids": {
            "type": "array",
            "maxItems": MAX_AI_PROCESSES,
            "items": {"type": "string"},
        },
        "successor_temp_ids": {
            "type": "array",
            "maxItems": MAX_AI_PROCESSES,
            "items": {"type": "string"},
        },
        "name": {"type": "string"},
        "purpose": {"type": "string"},
        "owner": {"type": "string"},
        "scope": {"type": "string"},
        "trigger": {"type": "string"},
        "outcome": {"type": "string"},
        "inputs": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "outputs": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "systems": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "controls": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "kpis": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "steps": {"type": "array", "minItems": 2, "maxItems": 60, "items": AI_STEP_SCHEMA},
        "connections": {
            "type": "array",
            "maxItems": 120,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "from", "to", "label"],
                "properties": {
                    "id": {"type": "string"},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "label": {"type": "string"},
                },
            },
        },
    },
}

AI_PROCESS_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "temp_id",
        "phase_id",
        "phase_name",
        "phase_order",
        "variant",
        "lane_names",
        "entry_criteria",
        "exit_criteria",
        "predecessor_temp_ids",
        "successor_temp_ids",
        "name",
        "purpose",
        "owner",
        "scope",
        "trigger",
        "outcome",
        "inputs",
        "outputs",
        "systems",
        "controls",
        "kpis",
        "activity_names",
    ],
    "properties": {
        key: copy.deepcopy(value)
        for key, value in AI_PROCESS_SCHEMA["properties"].items()
        if key not in {"steps", "connections"}
    },
}
AI_PROCESS_PLAN_SCHEMA["properties"]["activity_names"] = {
    "type": "array",
    "minItems": 1,
    "maxItems": 60,
    "items": {"type": "string"},
}

AI_LANE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "name", "accountable", "description"],
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "accountable": {"type": "string"},
        "description": {"type": "string"},
    },
}

AI_HANDOFF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "from_process_temp_id", "to_process_temp_id", "condition", "artifact", "description"],
    "properties": {
        "id": {"type": "string"},
        "from_process_temp_id": {"type": "string"},
        "to_process_temp_id": {"type": "string"},
        "condition": {"type": "string"},
        "artifact": {"type": "string"},
        "description": {"type": "string"},
    },
}

AI_PORTFOLIO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["temp_id", "name", "purpose", "expected_process_count", "lanes", "handoffs"],
    "properties": {
        "temp_id": {"type": "string"},
        "name": {"type": "string"},
        "purpose": {"type": "string"},
        "expected_process_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_AI_PROCESSES,
        },
        "lanes": {"type": "array", "maxItems": 30, "items": AI_LANE_SCHEMA},
        "handoffs": {"type": "array", "maxItems": 100, "items": AI_HANDOFF_SCHEMA},
    },
}

AI_DISCOVERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "assistant_message",
        "needs_clarification",
        "discovery_complete",
        "client_name",
        "portfolio",
        "processes",
    ],
    "properties": {
        "assistant_message": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "discovery_complete": {"type": "boolean"},
        "client_name": {"type": "string"},
        "portfolio": AI_PORTFOLIO_SCHEMA,
        "processes": {"type": "array", "maxItems": MAX_AI_PROCESSES, "items": AI_PROCESS_SCHEMA},
    },
}

AI_DISCOVERY_PLAN_SCHEMA = copy.deepcopy(AI_DISCOVERY_SCHEMA)
AI_DISCOVERY_PLAN_SCHEMA["properties"]["processes"] = {
    "type": "array",
    "maxItems": MAX_AI_PROCESSES,
    "items": AI_PROCESS_PLAN_SCHEMA,
}

AI_PHASE_EXPANSION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps", "connections"],
    "properties": {
        "steps": copy.deepcopy(AI_PROCESS_SCHEMA["properties"]["steps"]),
        "connections": copy.deepcopy(AI_PROCESS_SCHEMA["properties"]["connections"]),
    },
}

AI_DISCOVERY_INSTRUCTIONS = """You are the DevReady Process Discovery Agent for client business-flow intake.
Turn ordinary client descriptions into a connected, reviewable BPMN-oriented process portfolio. This product is DevReady and AIReady Foundry; it is not SAP, HPCC, or Syntax.

Conversation rules:
- The portfolio may contain up to twelve business processes. When the client explicitly enumerates phases, stages, or major flows, create one process draft per enumerated phase by default. Never compress eight named phases into five drafts.
- Distinguish phases from functional lanes: phases become connected process drafts; lanes are accountable business roles that can recur across drafts.
- Connect every process end-to-end. Each process must declare predecessor_temp_ids, successor_temp_ids, entry criteria, exit criteria, and the portfolio must contain explicit handoffs with a condition and transferred artifact. Normal completion should advance to the next numbered phase; model rework and renewal loops when described.
- If the client removes a role or lane, retain its business activities and reassign them to an accountable remaining business lane or mark the owner TBD. Do not keep a removed role as a lane and do not silently drop its work.
- Prefer the client's explicit decomposition even when the processes share activities. Use call_activity for a reusable subprocess and keep consistent activity names so AIReady Foundry can reuse one component across processes.
- If essential facts are missing, ask exactly one concise, high-value question and preserve any already-built drafts.
- Essential facts are: process purpose, trigger, end outcome, major actors/owners, systems, decision paths, exceptions, controls, and success measures.
- Build a draft once the description is sufficient. Drafts still require human validation; never claim client approval.
- Set discovery_complete true when all requested process drafts are structurally generated. Use needs_clarification for unresolved owners, assumptions, controls, or client-validation gaps; those gaps do not make an otherwise complete draft portfolio structurally incomplete.
- Include a start_event and at least one end_event. Every connection must reference a step ID in the same process.
- Use service_task for API/automation, call_activity for a reusable subprocess, user_task for system-assisted human work, manual_task for offline work, business_rule_task for a rule check, decision for exclusive branching, parallel_gateway for concurrent work, and inclusive_gateway when one or more branches may apply.
- Start and end names must identify the cross-process handoff, not generic words alone. Put program-specific renewable and film behavior behind named gateways or identify the process variant explicitly.
- Do not fabricate code paths, API routes, MCP tools, URLs, credentials, systems, controls, or owners. Leave unknown reference arrays empty and ask for the missing technical evidence later.
- When the client names code, an API, an MCP tool/server, or a link, attach it to the exact step that uses it.
- Each reusable activity will be checked against AIReady Foundry after the user accepts the draft. Do not decide that a component exists unless the supplied Foundry summary proves it.
- Preserve every named activity, decision, external party, compliance control, program branch, and open validation gap from the client's description. Do not treat a long specification as a request to summarize.
- Return only the requested strict JSON object."""

AI_PORTFOLIO_PLAN_INSTRUCTIONS = AI_DISCOVERY_INSTRUCTIONS + """

This is the portfolio-planning pass. Return concise process plans, not full BPMN steps or connections.
- Put every explicitly named source activity into exactly one phase plan's activity_names array; preserve the source wording where practical.
- Use activity_names to prove coverage before detailed phase generation.
- Keep phase metadata, lanes, inputs, outputs, controls, handoffs, variants, and validation gaps specific enough for a second pass to expand each phase independently.
- If eight phases are named, return eight plans ordered one through eight."""

AI_PHASE_EXPANSION_INSTRUCTIONS = """You are the DevReady BPMN Phase Expansion Agent.
Expand exactly one supplied phase plan into a technically coherent BPMN-oriented process draft.
- Preserve every activity in phase_plan.activity_names as a named step or an explicitly named gateway/event. Do not merge away source activities.
- Use the supplied phase identity, order, variant, lane names, entry/exit criteria, predecessor and successor IDs unchanged.
- Include one specific start event and at least one specific end event. Connect all non-start/non-end nodes and label decision branches.
- Model rework, exception, concurrent, inclusive, and renewal behavior described in the client request.
- Use consistent activity names so AIReady Foundry can reuse components.
- Reassign removed-role work as directed; never restore a removed lane.
- Do not invent code references, API routes, MCP tools, links, credentials, systems, controls, or owners. Leave unknown reference arrays empty.
- Return only the phase's steps and internal connections; phase metadata is supplied by the portfolio plan and will be joined deterministically.
- Return only the requested strict JSON object."""


def _normalize_ai_discovery(result: Any, message: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("Process discovery result must be an object.")
    raw_processes = result.get("processes") if isinstance(result.get("processes"), list) else []
    processes: list[dict[str, Any]] = []
    used_temp_ids: set[str] = set()
    temp_id_map: dict[str, str] = {}
    for index, raw in enumerate(raw_processes[:MAX_AI_PROCESSES]):
        if not isinstance(raw, dict):
            continue
        process = copy.deepcopy(raw)
        source_temp_id = _text(
            process.get("temp_id") or process.get("phase_id") or process.get("name") or f"phase-{index + 1}",
            160,
        )
        base_id = _slug(source_temp_id)
        temp_id = base_id
        suffix = 2
        while temp_id in used_temp_ids:
            temp_id = f"{base_id}-{suffix}"
            suffix += 1
        used_temp_ids.add(temp_id)
        process["temp_id"] = temp_id
        temp_id_map[source_temp_id] = temp_id
        temp_id_map[_slug(source_temp_id)] = temp_id
        if process.get("phase_id"):
            temp_id_map[_text(process.get("phase_id"), 160)] = temp_id
            temp_id_map[_slug(process.get("phase_id"))] = temp_id
        process["phase_order"] = _phase_order(process.get("phase_order")) or index + 1
        process["phase_id"] = _text(process.get("phase_id"), 160) or f"phase-{process['phase_order']:02d}"
        process["phase_name"] = _text(process.get("phase_name"), 240) or _text(process.get("name"), 240)
        process["variant"] = _text(process.get("variant"), 80) or "shared"
        process["lane_names"] = _string_list(process.get("lane_names"), limit=20, item_limit=240)
        process["entry_criteria"] = _string_list(process.get("entry_criteria"), limit=20, item_limit=1_000)
        process["exit_criteria"] = _string_list(process.get("exit_criteria"), limit=20, item_limit=1_000)
        process["predecessor_temp_ids"] = _string_list(
            process.get("predecessor_temp_ids"), limit=MAX_AI_PROCESSES, item_limit=160
        )
        process["successor_temp_ids"] = _string_list(
            process.get("successor_temp_ids"), limit=MAX_AI_PROCESSES, item_limit=160
        )
        processes.append(process)
    processes.sort(key=lambda item: (item.get("phase_order") or 0, item.get("phase_name") or ""))

    for process in processes:
        for field in ("predecessor_temp_ids", "successor_temp_ids"):
            process[field] = _string_list(
                [temp_id_map.get(item, temp_id_map.get(_slug(item), item)) for item in process[field]],
                limit=MAX_AI_PROCESSES,
                item_limit=160,
            )

    for index, process in enumerate(processes):
        if index > 0:
            prior = processes[index - 1]["temp_id"]
            process["predecessor_temp_ids"] = _string_list(
                [*process["predecessor_temp_ids"], prior],
                limit=MAX_AI_PROCESSES,
                item_limit=160,
            )
        if index + 1 < len(processes):
            following = processes[index + 1]["temp_id"]
            process["successor_temp_ids"] = _string_list(
                [*process["successor_temp_ids"], following],
                limit=MAX_AI_PROCESSES,
                item_limit=160,
            )

    portfolio_raw = result.get("portfolio") if isinstance(result.get("portfolio"), dict) else {}
    portfolio = {
        **copy.deepcopy(portfolio_raw),
        "temp_id": _text(portfolio_raw.get("temp_id"), 160) or _new_id("portfolio_draft"),
        "name": _text(portfolio_raw.get("name"), 240) or "Client business process portfolio",
        "purpose": _text(portfolio_raw.get("purpose"), 4_000),
        "expected_process_count": len(processes) or 1,
        "lanes": [
            _sanitize_lane(raw, index)
            for index, raw in enumerate((portfolio_raw.get("lanes") or [])[:30])
            if isinstance(raw, dict)
        ],
    }
    remove_solution_architect = bool(
        re.search(r"\b(remove|exclude|without|drop)\b[^\n]{0,80}\bsolution architect(?:ure)?\b", message, re.I)
        or re.search(r"\bsolution architect(?:ure)?\b[^\n]{0,80}\b(remove|exclude|without|drop)\b", message, re.I)
    )
    if remove_solution_architect:
        portfolio["lanes"] = [
            lane for lane in portfolio["lanes"] if "solution architect" not in lane.get("name", "").casefold()
        ]
        for process in processes:
            process["lane_names"] = [
                lane for lane in process["lane_names"] if "solution architect" not in lane.casefold()
            ]

    raw_handoffs = [
        _sanitize_handoff(raw, index)
        for index, raw in enumerate((portfolio_raw.get("handoffs") or [])[:100])
        if isinstance(raw, dict)
    ]
    process_ids = {process["temp_id"] for process in processes}
    handoffs = []
    for handoff in raw_handoffs:
        handoff["from_process_id"] = temp_id_map.get(
            handoff["from_process_id"],
            temp_id_map.get(_slug(handoff["from_process_id"]), handoff["from_process_id"]),
        )
        handoff["to_process_id"] = temp_id_map.get(
            handoff["to_process_id"],
            temp_id_map.get(_slug(handoff["to_process_id"]), handoff["to_process_id"]),
        )
        if handoff["from_process_id"] in process_ids and handoff["to_process_id"] in process_ids:
            handoffs.append(handoff)
    invalid_handoff_count = len(raw_handoffs) - len(handoffs)
    connected_pairs = {
        (handoff.get("from_process_id"), handoff.get("to_process_id")) for handoff in handoffs
    }
    for index in range(len(processes) - 1):
        source = processes[index]
        target = processes[index + 1]
        pair = (source["temp_id"], target["temp_id"])
        if pair in connected_pairs:
            continue
        handoffs.append(
            {
                "id": f"handoff_{source['phase_order']:02d}_{target['phase_order']:02d}",
                "from_process_id": source["temp_id"],
                "to_process_id": target["temp_id"],
                "condition": _text(source.get("outcome"), 1_000) or "Prior phase exit criteria met",
                "artifact": _text((source.get("outputs") or [""])[0], 1_000),
                "description": f"Normal completion advances from {source['phase_name']} to {target['phase_name']}.",
            }
        )
    phase_by_order = {process.get("phase_order"): process for process in processes}
    known_pairs = {
        (handoff.get("from_process_id"), handoff.get("to_process_id")) for handoff in handoffs
    }
    for match in re.finditer(
        r"\bphase\s+0?(\d{1,2})\b(?:(?!\bphase\s+0?\d{1,2}\b)[^\n]){0,180}"
        r"\b(?:loop|return|renewal)\w*\b(?:(?!\bphase\s+0?\d{1,2}\b)[^\n]){0,180}"
        r"\b(?:back\s+)?to\s+phase\s+0?(\d{1,2})\b",
        message,
        re.I,
    ):
        source = phase_by_order.get(int(match.group(1)))
        target = phase_by_order.get(int(match.group(2)))
        if not source or not target or source is target:
            continue
        pair = (source["temp_id"], target["temp_id"])
        if pair in known_pairs:
            continue
        handoffs.append(
            {
                "id": f"handoff_loop_{source['phase_order']:02d}_{target['phase_order']:02d}",
                "from_process_id": source["temp_id"],
                "to_process_id": target["temp_id"],
                "condition": "Client-described loop condition is met",
                "artifact": _text((source.get("outputs") or [""])[0], 1_000),
                "description": f"Client-described loop from {source['phase_name']} to {target['phase_name']}.",
            }
        )
        known_pairs.add(pair)
    process_by_id = {process["temp_id"]: process for process in processes}
    for handoff in handoffs:
        source = process_by_id.get(handoff.get("from_process_id"))
        target = process_by_id.get(handoff.get("to_process_id"))
        if not source or not target or source is target:
            continue
        source["successor_temp_ids"] = _string_list(
            [*source["successor_temp_ids"], target["temp_id"]],
            limit=MAX_AI_PROCESSES,
            item_limit=160,
        )
        target["predecessor_temp_ids"] = _string_list(
            [*target["predecessor_temp_ids"], source["temp_id"]],
            limit=MAX_AI_PROCESSES,
            item_limit=160,
        )
    portfolio["handoffs"] = handoffs

    explicit_phases = {
        int(match.group(1))
        for match in re.finditer(r"\bphase\s+0?(\d{1,2})\b", message, re.I)
        if 1 <= int(match.group(1)) <= MAX_AI_PROCESSES
    }
    result["portfolio"] = portfolio
    result["processes"] = processes
    phase_coverage_complete = not explicit_phases or len(processes) >= len(explicit_phases)
    structurally_complete = bool(processes) and phase_coverage_complete and all(
        not _phase_expansion_issues(
            {"activity_names": process.get("source_activity_names") or []},
            {"steps": process.get("steps") or [], "connections": process.get("connections") or []},
        )
        and bool(process.get("entry_criteria"))
        and bool(process.get("exit_criteria"))
        for process in processes
    ) and len(handoffs) >= max(0, len(processes) - 1) and invalid_handoff_count == 0
    result["interpretation"] = {
        "process_count": len(processes),
        "explicit_phase_count": len(explicit_phases),
        "handoff_count": len(handoffs),
        "invalid_handoff_count": invalid_handoff_count,
        "removed_solution_architect_lane": remove_solution_architect,
        "complete_phase_coverage": phase_coverage_complete,
        "structurally_complete": structurally_complete,
    }
    result["discovery_complete"] = structurally_complete
    if not phase_coverage_complete:
        result["discovery_complete"] = False
        result["assistant_message"] = (
            f"I identified {len(explicit_phases)} named phases but only produced {len(processes)} drafts. "
            "The portfolio is staged as incomplete and needs regeneration before saving."
        )
    return result


def _ai_model() -> str:
    return (
        os.getenv("OPENAI_PROCESS_MODEL")
        or os.getenv("OPENAI_AGENT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    )


def _response_diagnostic(response: Any) -> str:
    status = getattr(response, "status", None)
    if status is None and isinstance(response, dict):
        status = response.get("status")
    details = getattr(response, "incomplete_details", None)
    if details is None and isinstance(response, dict):
        details = response.get("incomplete_details")
    reason = getattr(details, "reason", None)
    if reason is None and isinstance(details, dict):
        reason = details.get("reason")
    parts = [str(item) for item in (status, reason) if item]
    return "/".join(parts) or "empty structured output"


def _structured_ai_result(
    *,
    client: Any,
    model: str,
    instructions: str,
    context: dict[str, Any],
    schema: dict[str, Any],
    schema_name: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    request_args = {
        "model": model,
        "instructions": instructions,
        "input": [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": max_output_tokens,
    }
    if model.casefold().startswith("gpt-5"):
        request_args["reasoning"] = {"effort": "low"}
    response = client.responses.create(
        **request_args,
    )
    output = _output_text(response)
    if not output:
        raise ValueError(f"OpenAI returned no JSON ({_response_diagnostic(response)}).")
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"OpenAI returned truncated or invalid JSON ({_response_diagnostic(response)}; {len(output)} characters)."
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("OpenAI structured output was not an object.")
    return parsed


def _expand_ai_phase(
    *,
    client: Any,
    model: str,
    message: str,
    portfolio: dict[str, Any],
    phase_plan: dict[str, Any],
    adjacent_phases: list[dict[str, Any]],
) -> dict[str, Any]:
    prior_attempt: dict[str, Any] = {}
    issues: list[str] = []
    for attempt in range(2):
        try:
            expanded = _structured_ai_result(
                client=client,
                model=model,
                instructions=AI_PHASE_EXPANSION_INSTRUCTIONS,
                context={
                    "client_request": message,
                    "portfolio": portfolio,
                    "phase_plan": phase_plan,
                    "adjacent_phases": adjacent_phases,
                    "repair": {
                        "attempt": attempt + 1,
                        "validation_errors": issues,
                        "prior_output": prior_attempt,
                        "instruction": (
                            "The prior output failed deterministic validation. Correct every listed error and return the full phase again."
                            if issues
                            else "Generate the phase and preserve every planned activity."
                        ),
                    },
                },
                schema=AI_PHASE_EXPANSION_SCHEMA,
                schema_name="devready_phase_expansion",
                max_output_tokens=8_000,
            )
        except ValueError as exc:
            issues = [str(exc)]
            prior_attempt = {}
            if attempt == 1:
                raise
            continue
        issues = _phase_expansion_issues(phase_plan, expanded)
        if not issues:
            return {
                **{key: copy.deepcopy(value) for key, value in phase_plan.items() if key != "activity_names"},
                "source_activity_names": copy.deepcopy(phase_plan.get("activity_names") or []),
                "steps": expanded.get("steps") if isinstance(expanded.get("steps"), list) else [],
                "connections": expanded.get("connections") if isinstance(expanded.get("connections"), list) else [],
            }
        prior_attempt = expanded
    raise ValueError("phase output remained invalid after repair: " + "; ".join(issues[:12]))


def _phase_expansion_issues(phase_plan: dict[str, Any], expanded: dict[str, Any]) -> list[str]:
    steps = expanded.get("steps") if isinstance(expanded.get("steps"), list) else []
    connections = expanded.get("connections") if isinstance(expanded.get("connections"), list) else []
    issues: list[str] = []
    ids = [_text(step.get("id"), 160) for step in steps if isinstance(step, dict)]
    id_set = {item for item in ids if item}
    if len(ids) != len(id_set):
        issues.append("step IDs must be non-empty and unique")
    types = {_text(step.get("type"), 80) for step in steps if isinstance(step, dict)}
    if "start_event" not in types:
        issues.append("add a start_event")
    if "end_event" not in types:
        issues.append("add an end_event")
    combined_names = "\n".join(
        _text(step.get("name"), 500).casefold() for step in steps if isinstance(step, dict)
    )
    for activity in _string_list(phase_plan.get("activity_names"), limit=60, item_limit=500):
        if activity.casefold() not in combined_names:
            issues.append(f"include planned activity exactly: {activity}")
    incoming = {step_id: 0 for step_id in id_set}
    outgoing = {step_id: 0 for step_id in id_set}
    for connection in connections:
        if not isinstance(connection, dict):
            issues.append("connections must be objects")
            continue
        source = _text(connection.get("from"), 160)
        target = _text(connection.get("to"), 160)
        if source not in id_set or target not in id_set:
            issues.append(f"connection {_text(connection.get('id'), 160) or '?'} references a missing step")
            continue
        if source == target:
            issues.append(f"connection {_text(connection.get('id'), 160) or '?'} cannot connect a step to itself")
            continue
        outgoing[source] += 1
        incoming[target] += 1
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = _text(step.get("id"), 160)
        step_type = _text(step.get("type"), 80)
        if step_type != "start_event" and incoming.get(step_id, 0) == 0:
            issues.append(f"connect an incoming flow to {step_id}")
        if step_type != "end_event" and outgoing.get(step_id, 0) == 0:
            issues.append(f"connect an outgoing flow from {step_id}")
    return _string_list(issues, limit=50, item_limit=1_000)


COMPONENT_BUILD_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "data_entities": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "api_contracts": {"type": "array", "items": {"type": "string"}, "maxItems": 60},
        "mcp_contracts": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "security_controls": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        "test_scenarios": {"type": "array", "items": {"type": "string"}, "maxItems": 60},
        "dependencies": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
    },
    "required": [
        "summary",
        "acceptance_criteria",
        "data_entities",
        "api_contracts",
        "mcp_contracts",
        "security_controls",
        "test_scenarios",
        "dependencies",
    ],
    "additionalProperties": False,
}

COMPONENT_BUILD_INSTRUCTIONS = """You are the aiReady Application Factory component architect.
Create an implementation contract for one missing reusable application component.
The contract must be concrete enough for a coding agent and a human reviewer to implement and test.
Preserve every supplied business rule, owner, approval, tenant, evidence, and integration constraint.
Do not claim code, APIs, MCP tools, provider connections, credentials, or tests already exist.
All consequential legal, tax, investment, signature, money-movement, and release actions require explicit human approval and audit evidence.
Use tenant-scoped PostgreSQL data contracts, source-linked RAG when knowledge retrieval is needed, idempotent writes, least privilege, and observable failure states.
Return only the requested structured object."""


def _foundation_component(store: dict[str, Any], requirement: dict[str, str]) -> dict[str, Any]:
    components = store.setdefault("components", [])
    key = requirement["key"]
    component = next(
        (
            item
            for item in components
            if item.get("external_key") == key
            or item.get("slug") == key
            or key in _component_search_keys(item)
        ),
        None,
    )
    if component:
        return component
    component = _sanitize_component_payload(
        {
            "external_key": key,
            "name": requirement["name"],
            "kind": requirement["kind"],
            "description": requirement["description"],
            "status": "draft",
            "implementation_status": "missing",
            "standard_foundation": True,
            "reuse_mode": "shared-platform",
        }
    )
    components.append(component)
    return component


def _implemented_component_for_name(
    components: list[dict[str, Any]],
    name: str,
    preferred_id: str = "",
) -> dict[str, Any] | None:
    key = _slug(name)
    candidates = [item for item in components if key in _component_search_keys(item)]
    candidates.sort(
        key=lambda item: (
            _component_is_implemented(item),
            item.get("id") == preferred_id,
            bool((item.get("provenance") or {}).get("verified_at")),
        ),
        reverse=True,
    )
    return next((item for item in candidates if _component_is_implemented(item)), None)


def _delivery_choice(value: Any, choices: set[str], fallback: str) -> str:
    candidate = _slug(value)
    return candidate if candidate in choices else fallback


def _application_delivery_plan(payload: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    previous = copy.deepcopy((prior or {}).get("delivery") or {})
    previous_profile = previous.get("profile") or "rapid"
    profile_key = _delivery_choice(
        payload.get("delivery_profile") or previous_profile,
        set(DELIVERY_PROFILES),
        "rapid",
    )
    profile = DELIVERY_PROFILES[profile_key]
    same_profile = profile_key == previous_profile
    deployment_target = _delivery_choice(
        payload.get("deployment_target") or (previous.get("deployment_target") if same_profile else None),
        DELIVERY_CHOICES["deployment_target"],
        profile["deployment_target"],
    )
    availability = _delivery_choice(
        payload.get("availability") or (previous.get("reliability", {}).get("availability") if same_profile else None),
        DELIVERY_CHOICES["availability"],
        profile["availability"],
    )
    repository_strategy = _delivery_choice(
        payload.get("repository_strategy") or previous.get("repository", {}).get("strategy"),
        DELIVERY_CHOICES["repository_strategy"],
        "new-repository",
    )
    pipeline_provider = _delivery_choice(
        payload.get("pipeline_provider") or previous.get("pipeline", {}).get("provider"),
        DELIVERY_CHOICES["pipeline_provider"],
        "github-actions",
    )
    evidence_payload = payload.get("delivery_evidence")
    evidence = copy.deepcopy(evidence_payload if isinstance(evidence_payload, dict) else previous.get("evidence") or {})
    environment_names = list(profile["environment_names"])
    environments = [
        {
            "name": name,
            "purpose": {
                "development": "Integration, automated tests, and client review before promotion.",
                "staging": "Production-like verification, migration rehearsal, and release approval.",
                "production": "Approved customer workload with monitoring, backup, and rollback evidence.",
            }[name],
            "isolated": True,
            "required": True,
            "evidence_verified": bool(evidence.get(f"{name}_environment_verified")),
        }
        for name in environment_names
    ]
    kubernetes_required = deployment_target in {"kubernetes", "hybrid"}
    production_ready = bool(evidence.get("production_acceptance_verified"))
    return {
        "profile": profile_key,
        "profile_label": profile["label"],
        "description": profile["description"],
        "deployment_target": deployment_target,
        "repository": {
            "provider": "github",
            "strategy": repository_strategy,
            "url": _text(payload.get("target_repository") or (prior or {}).get("target_repository"), 1_000),
            "default_branch": "main",
            "development_branch": "development",
            "protected": bool(evidence.get("repository_protection_verified")),
        },
        "environments": environments,
        "pipeline": {
            "provider": pipeline_provider,
            "artifact_strategy": "build-once-promote-same-artifact",
            "release_strategy": profile["release_strategy"],
            "required_checks": [
                "unit-and-contract-tests",
                "integration-and-security-tests",
                "database-migration-check",
                "container-health-check",
                "human-production-approval",
            ],
        },
        "container": {
            "docker_required": True,
            "kubernetes_required": kubernetes_required,
            "orchestrator": "kubernetes" if kubernetes_required else "railway",
            "immutable_image": True,
        },
        "reliability": {
            "availability": availability,
            "health_endpoint_required": True,
            "observability": ["structured-logs", "metrics", "traces", "release-markers", "alert-routing"],
            "data_protection": ["automated-backup", "point-in-time-recovery", "restore-drill"],
            "failover_required": availability != "standard",
            "rto_rpo_must_be_confirmed": availability != "standard",
        },
        "production_definition": [
            "confirmed-business-processes",
            "implementation-and-test-evidence",
            "tenant-and-security-verification",
            "same-artifact-promotion",
            "rollback-and-restore-evidence",
            "production-smoke-and-acceptance-tests",
        ],
        "evidence": evidence,
        "production_ready": production_ready,
    }


def _application_blueprint(
    store: dict[str, Any],
    portfolio: dict[str, Any],
    payload: dict[str, Any],
    *,
    application_id: str,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delivery = _application_delivery_plan(payload, prior)
    process_ids = set(portfolio.get("process_ids") or [])
    processes = sorted(
        [item for item in store.get("processes", []) if item.get("id") in process_ids],
        key=lambda item: (item.get("phase_order") or 0, item.get("name") or ""),
    )
    components = store.setdefault("components", [])
    requirements: list[dict[str, Any]] = []
    for process in processes:
        for element in process.get("elements", []):
            if element.get("type") not in REUSABLE_NODE_TYPES or not element.get("name"):
                continue
            current = next(
                (item for item in components if item.get("id") == element.get("component_id")),
                None,
            )
            implemented = _implemented_component_for_name(
                components,
                element.get("name", ""),
                element.get("component_id", ""),
            )
            chosen = implemented or current
            requirements.append(
                {
                    "id": f"activity:{process.get('id')}:{element.get('id')}",
                    "requirement_type": "process-activity",
                    "process_id": process.get("id"),
                    "process_name": process.get("name"),
                    "phase_order": process.get("phase_order") or 0,
                    "element_id": element.get("id"),
                    "name": element.get("name"),
                    "owner": element.get("owner"),
                    "system": element.get("system"),
                    "component_id": chosen.get("id") if chosen else "",
                    "component_name": chosen.get("name") if chosen else "",
                    "resolution": "reuse" if implemented else "build-required",
                    "readiness": _component_readiness(chosen) if chosen else {
                        "implemented": False,
                        "status": "missing",
                        "implementation_status": "missing",
                        "evidence": {"code": 0, "api": 0, "mcp": 0, "tests": 0, "links": 0},
                        "provenance_verified": False,
                    },
                }
            )
    for foundation in STANDARD_FOUNDATION_REQUIREMENTS:
        current = _foundation_component(store, foundation)
        implemented = _implemented_component_for_name(components, foundation["name"], current.get("id", ""))
        chosen = implemented or current
        requirements.append(
            {
                "id": f"foundation:{foundation['key']}",
                "requirement_type": "standard-foundation",
                "process_id": "",
                "process_name": "Every application",
                "phase_order": 0,
                "element_id": "",
                "name": foundation["name"],
                "owner": "Platform owner",
                "system": "aiReady platform",
                "component_id": chosen.get("id"),
                "component_name": chosen.get("name"),
                "resolution": "reuse" if implemented else "build-required",
                "readiness": _component_readiness(chosen),
            }
        )
    requirements = requirements[:MAX_APPLICATION_REQUIREMENTS]
    reused = [item for item in requirements if item["resolution"] == "reuse"]
    gaps = [item for item in requirements if item["resolution"] == "build-required"]
    selected_component_ids = {item.get("component_id") for item in requirements if item.get("component_id")}
    selected_components = [item for item in components if item.get("id") in selected_component_ids]
    integrations = {
        "api_endpoints": sorted({ref for component in selected_components for ref in component.get("api_endpoints") or []}),
        "mcp_tools": sorted({ref for component in selected_components for ref in component.get("mcp_tools") or []}),
        "external_links": sorted({ref for component in selected_components for ref in component.get("links") or []}),
        "decisions": payload.get("integration_decisions")
        if isinstance(payload.get("integration_decisions"), dict)
        else copy.deepcopy((prior or {}).get("integrations", {}).get("decisions", {})),
    }
    process_confirmed = bool(processes) and all(item.get("status") == "validated" for item in processes)
    foundation_by_key = {item["id"].split(":", 1)[1]: item for item in requirements if item["requirement_type"] == "standard-foundation"}
    release_gates = [
        {
            "key": "business-process-confirmed",
            "label": "Business processes confirmed",
            "passed": process_confirmed,
            "detail": f"{sum(item.get('status') == 'validated' for item in processes)}/{len(processes)} processes validated",
        },
        {
            "key": "components-implemented",
            "label": "Required components implemented",
            "passed": not gaps,
            "detail": f"{len(reused)}/{len(requirements)} requirements have reusable implementation evidence",
        },
        {
            "key": "postgres-multitenancy",
            "label": "PostgreSQL tenant isolation verified",
            "passed": foundation_by_key.get("postgres-tenant-data", {}).get("resolution") == "reuse",
            "detail": "Requires code, API or migration evidence plus tests; a design-only catalog card does not pass.",
        },
        {
            "key": "security-approval-audit",
            "label": "Security and human approval controls verified",
            "passed": all(
                foundation_by_key.get(key, {}).get("resolution") == "reuse"
                for key in ("multitenant-identity-access", "human-approval-audit")
            ),
            "detail": "Consequential actions remain human-approved and auditable.",
        },
        {
            "key": "rag-context",
            "label": "Agent context and source-linked RAG verified",
            "passed": all(
                foundation_by_key.get(key, {}).get("resolution") == "reuse"
                for key in ("agent-aware-context", "rag-knowledge-retrieval")
            ),
            "detail": "RAG is required; CAG-only context does not pass this gate.",
        },
        {
            "key": "repository-governance",
            "label": "Repository and branch governance verified",
            "passed": bool(
                delivery["repository"]["url"]
                and delivery["repository"]["protected"]
                and delivery["evidence"].get("repository_connected")
            ),
            "detail": f"{delivery['repository']['strategy']} on GitHub; protected production path and connection evidence required.",
        },
        {
            "key": "environment-isolation",
            "label": "Development and production isolation verified",
            "passed": all(item["evidence_verified"] for item in delivery["environments"]),
            "detail": f"{len(delivery['environments'])} isolated environments planned for {delivery['profile_label']}.",
        },
        {
            "key": "container-pipeline",
            "label": "Container and promotion pipeline verified",
            "passed": all(
                bool(delivery["evidence"].get(key))
                for key in ("container_verified", "pipeline_verified", "same_artifact_promotion_verified")
            ),
            "detail": f"Docker through {delivery['pipeline']['provider']}; build once and promote the same immutable artifact.",
        },
        {
            "key": "operational-readiness",
            "label": "Operations, recovery, and failover verified",
            "passed": all(
                bool(delivery["evidence"].get(key))
                for key in ("observability_verified", "backup_restore_verified", "rollback_verified")
            ) and (
                not delivery["reliability"]["failover_required"]
                or bool(delivery["evidence"].get("failover_verified"))
            ),
            "detail": f"{delivery['reliability']['availability']} availability; monitoring, restore, rollback, and applicable failover proof required.",
        },
        {
            "key": "production-acceptance",
            "label": "Production smoke and customer acceptance verified",
            "passed": delivery["production_ready"],
            "detail": "A successful build is not delivery; the live production application must pass smoke and acceptance tests.",
        },
    ]
    now = _utc_now()
    name = _text(payload.get("name"), 240) or f"{portfolio.get('client_name') or 'Client'} application"
    return {
        "id": application_id,
        "key": _slug(f"{portfolio.get('client_name')}-{name}"),
        "name": name,
        "client_name": _text(payload.get("client_name") or portfolio.get("client_name"), 240),
        "portfolio_id": portfolio.get("id"),
        "portfolio_name": portfolio.get("name"),
        "status": "release-ready" if all(item["passed"] for item in release_gates) else "factory-planning",
        "target_repository": delivery["repository"]["url"],
        "target_environment": _text(payload.get("target_environment") or (prior or {}).get("target_environment"), 240),
        "target_service": _text(payload.get("target_service") or (prior or {}).get("target_service"), 240),
        "process_ids": [item.get("id") for item in processes],
        "requirements": requirements,
        "summary": {
            "processes": len(processes),
            "requirements": len(requirements),
            "reused": len(reused),
            "build_required": len(gaps),
            "standard_foundations": len(STANDARD_FOUNDATION_REQUIREMENTS),
        },
        "assembly": {
            "handoffs": copy.deepcopy(portfolio.get("handoffs") or []),
            "sequence": [
                {"process_id": item.get("id"), "phase_order": item.get("phase_order"), "name": item.get("name")}
                for item in processes
            ],
        },
        "integrations": integrations,
        "delivery": delivery,
        "release_gates": release_gates,
        "created_at": (prior or {}).get("created_at") or now,
        "updated_at": now,
        "version": int((prior or {}).get("version") or 0) + 1,
    }


@router.get("/health")
def process_builder_health() -> dict[str, Any]:
    store = _read_store()
    return {
        "ok": True,
        "product": "aiReady Application Factory",
        "process_builder": "DevReady Process Builder",
        "foundry": "AIReady Foundry",
        "portfolios": len(store.get("portfolios", [])),
        "processes": len(store.get("processes", [])),
        "applications": len(store.get("applications", [])),
        "components": len(store.get("components", [])),
        "ai_ready": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "model": _ai_model(),
        "rest_base": "/api/process-builder",
        "mcp_endpoint": "/api/process-builder/mcp",
        "storage": STORAGE_STATE,
    }


@router.get("/applications")
def list_applications(client_name: str = Query(default="")) -> dict[str, Any]:
    applications = _read_store().get("applications", [])
    if client_name:
        applications = [
            item
            for item in applications
            if client_name.casefold() in str(item.get("client_name", "")).casefold()
        ]
    applications = sorted(applications, key=lambda item: item.get("updated_at", ""), reverse=True)
    summaries = [
        {
            key: copy.deepcopy(item.get(key))
            for key in (
                "id", "key", "name", "client_name", "portfolio_id", "portfolio_name", "status",
                "target_repository", "target_environment", "target_service", "delivery", "summary", "release_gates",
                "created_at", "updated_at", "version",
            )
        }
        for item in applications
    ]
    for summary in summaries:
        if isinstance(summary.get("delivery"), dict):
            summary["delivery"].pop("evidence", None)
    return {"ok": True, "applications": summaries, "count": len(summaries)}


@router.get("/applications/{application_id}")
def get_application(application_id: str) -> dict[str, Any]:
    application = next(
        (item for item in _read_store().get("applications", []) if item.get("id") == application_id),
        None,
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application blueprint not found.")
    return {"ok": True, "application": application}


@router.post("/applications", status_code=201)
async def create_or_refresh_application(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Application payload must be an object.")
    portfolio_id = _text(payload.get("portfolio_id"), 160)
    if not portfolio_id:
        raise HTTPException(status_code=400, detail="Choose a confirmed process portfolio first.")
    with STORE_LOCK:
        store = _read_store()
        portfolio = next(
            (item for item in store.get("portfolios", []) if item.get("id") == portfolio_id),
            None,
        )
        if not portfolio:
            raise HTTPException(status_code=404, detail="Process portfolio not found.")
        name = _text(payload.get("name"), 240) or f"{portfolio.get('client_name') or 'Client'} application"
        key = _slug(f"{portfolio.get('client_name')}-{name}")
        existing_index = next(
            (
                index
                for index, item in enumerate(store.setdefault("applications", []))
                if item.get("key") == key or item.get("id") == payload.get("id")
            ),
            None,
        )
        prior = store["applications"][existing_index] if existing_index is not None else None
        application_id = (prior or {}).get("id") or _new_id("app")
        application = _application_blueprint(
            store,
            portfolio,
            {**payload, "name": name},
            application_id=application_id,
            prior=prior,
        )
        if existing_index is None:
            store["applications"].append(application)
        else:
            store["applications"][existing_index] = application
        _write_store_unlocked(store)
    return {
        "ok": True,
        "created": existing_index is None,
        "application": copy.deepcopy(application),
    }


@router.get("/processes")
def list_processes(
    client_name: str = Query(default=""),
    domain: str = Query(default=""),
) -> dict[str, Any]:
    processes = _read_store().get("processes", [])
    if client_name:
        processes = [item for item in processes if client_name.casefold() in str(item.get("client_name", "")).casefold()]
    if domain:
        processes = [item for item in processes if str(item.get("domain", "")).casefold() == domain.casefold()]
    processes = sorted(
        processes,
        key=lambda item: (item.get("updated_at", ""), -(item.get("phase_order") or 0)),
        reverse=True,
    )
    return {"ok": True, "processes": [_process_summary(item) for item in processes], "count": len(processes)}


@router.get("/portfolios")
def list_portfolios(client_name: str = Query(default="")) -> dict[str, Any]:
    portfolios = _read_store().get("portfolios", [])
    if client_name:
        portfolios = [
            item
            for item in portfolios
            if client_name.casefold() in str(item.get("client_name", "")).casefold()
        ]
    portfolios = sorted(portfolios, key=lambda item: item.get("updated_at", ""), reverse=True)
    return {
        "ok": True,
        "portfolios": [_portfolio_summary(item) for item in portfolios],
        "count": len(portfolios),
    }


@router.get("/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: str) -> dict[str, Any]:
    store = _read_store()
    portfolio = next((item for item in store.get("portfolios", []) if item.get("id") == portfolio_id), None)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Process portfolio not found.")
    process_ids = set(portfolio.get("process_ids") or [])
    processes = [
        copy.deepcopy(item)
        for item in store.get("processes", [])
        if item.get("id") in process_ids
    ]
    processes.sort(key=lambda item: (item.get("phase_order") or 0, item.get("name") or ""))
    return {"ok": True, "portfolio": portfolio, "processes": processes}


@router.post("/portfolios", status_code=201)
async def save_portfolio(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Portfolio payload must be an object.")
    raw_portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}
    raw_processes = payload.get("processes") if isinstance(payload.get("processes"), list) else []
    if not raw_processes:
        raise HTTPException(status_code=400, detail="Provide at least one process draft.")
    if len(raw_processes) > MAX_AI_PROCESSES:
        raise HTTPException(
            status_code=400,
            detail=f"A portfolio can contain at most {MAX_AI_PROCESSES} process drafts.",
        )
    client_name = _text(payload.get("client_name") or raw_portfolio.get("client_name"), 240)
    now = _utc_now()
    with STORE_LOCK:
        store = _read_store()
        store.setdefault("portfolios", [])
        portfolio_name = _text(raw_portfolio.get("name"), 240) or f"{client_name or 'Client'} process portfolio"
        portfolio_key = _slug(f"{client_name}-{portfolio_name}")
        existing_portfolio_index = next(
            (
                index
                for index, item in enumerate(store["portfolios"])
                if item.get("key") == portfolio_key
            ),
            None,
        )
        prior_portfolio = (
            store["portfolios"][existing_portfolio_index]
            if existing_portfolio_index is not None
            else None
        )
        portfolio_id = (prior_portfolio or {}).get("id") or _new_id("portfolio")
        portfolio = _sanitize_portfolio_payload(
            {**raw_portfolio, "name": portfolio_name},
            portfolio_id=portfolio_id,
            client_name=client_name,
        )

        id_map: dict[str, str] = {}
        process_plan: list[tuple[dict[str, Any], str, int | None]] = []
        for index, raw in enumerate(raw_processes):
            if not isinstance(raw, dict):
                raise HTTPException(status_code=400, detail=f"Process draft {index + 1} must be an object.")
            temp_id = _text(raw.get("temp_id") or raw.get("phase_id") or raw.get("name"), 160) or f"phase_{index + 1}"
            phase_order = _phase_order(raw.get("phase_order")) or index + 1
            phase_id = _text(raw.get("phase_id"), 160)
            phase_name = _text(raw.get("phase_name"), 240)
            existing_index = next(
                (
                    process_index
                    for process_index, item in enumerate(store.get("processes", []))
                    if item.get("portfolio_id") == portfolio_id
                    and (
                        (phase_id and item.get("phase_id") == phase_id)
                        or (phase_order and item.get("phase_order") == phase_order)
                        or (phase_name and item.get("phase_name") == phase_name)
                    )
                ),
                None,
            )
            process_id = (
                store["processes"][existing_index].get("id")
                if existing_index is not None
                else _new_id("proc")
            )
            id_map[temp_id] = process_id
            process_plan.append((raw, process_id, existing_index))

        saved_processes: list[dict[str, Any]] = []
        aggregate = {"checked": 0, "reused": [], "created": [], "mapping": []}
        for index, (raw, process_id, existing_index) in enumerate(process_plan):
            predecessor_refs = _string_list(
                raw.get("predecessor_process_ids") or raw.get("predecessor_temp_ids"),
                limit=MAX_AI_PROCESSES,
                item_limit=160,
            )
            successor_refs = _string_list(
                raw.get("successor_process_ids") or raw.get("successor_temp_ids"),
                limit=MAX_AI_PROCESSES,
                item_limit=160,
            )
            process = _sanitize_process_payload(
                {
                    **raw,
                    "client_name": client_name,
                    "portfolio_id": portfolio_id,
                    "portfolio_name": portfolio["name"],
                    "portfolio_key": portfolio["key"],
                    "phase_order": _phase_order(raw.get("phase_order")) or index + 1,
                    "predecessor_process_ids": [id_map.get(item, item) for item in predecessor_refs],
                    "successor_process_ids": [id_map.get(item, item) for item in successor_refs],
                    "source": _text(raw.get("source"), 80) or "ai-intake",
                },
                process_id=process_id,
            )
            if existing_index is None:
                process["created_at"] = now
                process["version"] = 1
            else:
                prior = store["processes"][existing_index]
                process["created_at"] = prior.get("created_at", now)
                process["version"] = int(prior.get("version") or 0) + 1
            process["updated_at"] = now
            reconciliation = _reconcile_components(store, process)
            process["validation"] = _validation(process)
            process["foundry_summary"] = {
                "checked": reconciliation["checked"],
                "reused": len(reconciliation["reused"]),
                "created": len(reconciliation["created"]),
            }
            if existing_index is None:
                store["processes"].append(process)
            else:
                store["processes"][existing_index] = process
            aggregate["checked"] += reconciliation["checked"]
            aggregate["reused"].extend(reconciliation["reused"])
            aggregate["created"].extend(reconciliation["created"])
            aggregate["mapping"].extend(
                {**row, "process_id": process_id} for row in reconciliation["mapping"]
            )
            saved_processes.append(copy.deepcopy(process))

        portfolio["handoffs"] = [
            {
                **handoff,
                "from_process_id": id_map.get(handoff.get("from_process_id"), handoff.get("from_process_id")),
                "to_process_id": id_map.get(handoff.get("to_process_id"), handoff.get("to_process_id")),
            }
            for handoff in portfolio.get("handoffs", [])
        ]
        portfolio["process_ids"] = [item["id"] for item in sorted(saved_processes, key=lambda row: row["phase_order"])]
        portfolio["expected_process_count"] = len(saved_processes)
        portfolio["created_at"] = (prior_portfolio or {}).get("created_at", now)
        portfolio["updated_at"] = now
        portfolio["version"] = int((prior_portfolio or {}).get("version") or 0) + 1
        if existing_portfolio_index is None:
            store["portfolios"].append(portfolio)
        else:
            store["portfolios"][existing_portfolio_index] = portfolio
        _rebuild_component_usage(store)
        _write_store_unlocked(store)
    return {
        "ok": True,
        "created": existing_portfolio_index is None,
        "portfolio": copy.deepcopy(portfolio),
        "processes": saved_processes,
        "reconciliation": aggregate,
    }


@router.get("/processes/{process_id}")
def get_process(process_id: str) -> dict[str, Any]:
    process = next((item for item in _read_store().get("processes", []) if item.get("id") == process_id), None)
    if not process:
        raise HTTPException(status_code=404, detail="Process not found.")
    return {"ok": True, "process": process}


@router.post("/processes", status_code=201)
async def create_process(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Process payload must be an object.")
    process, reconciliation, created = _save_process(payload)
    return {"ok": True, "created": created, "process": process, "reconciliation": reconciliation}


@router.put("/processes/{process_id}")
async def update_process(process_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Process payload must be an object.")
    store = _read_store()
    if not any(item.get("id") == process_id for item in store.get("processes", [])):
        raise HTTPException(status_code=404, detail="Process not found.")
    process, reconciliation, _ = _save_process(payload, process_id=process_id)
    return {"ok": True, "created": False, "process": process, "reconciliation": reconciliation}


@router.delete("/processes/{process_id}")
def delete_process(process_id: str, confirmed: bool = Query(default=False)) -> dict[str, Any]:
    if not confirmed:
        raise HTTPException(status_code=409, detail="Deletion requires confirmed=true.")
    with STORE_LOCK:
        store = _read_store()
        before = len(store.get("processes", []))
        store["processes"] = [item for item in store.get("processes", []) if item.get("id") != process_id]
        if len(store["processes"]) == before:
            raise HTTPException(status_code=404, detail="Process not found.")
        _rebuild_component_usage(store)
        _write_store_unlocked(store)
    return {"ok": True, "deleted": process_id}


@router.post("/validate")
async def validate_process(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Validation payload must be an object.")
    if payload.get("process_id"):
        process = next(
            (item for item in _read_store().get("processes", []) if item.get("id") == payload.get("process_id")),
            None,
        )
        if not process:
            raise HTTPException(status_code=404, detail="Process not found.")
    else:
        process = _sanitize_process_payload(payload)
    return {"ok": True, "validation": _validation(process)}


@router.get("/processes/{process_id}/traceability")
def get_process_traceability(process_id: str) -> dict[str, Any]:
    store = _read_store()
    process = next((item for item in store.get("processes", []) if item.get("id") == process_id), None)
    if not process:
        raise HTTPException(status_code=404, detail="Process not found.")
    return {"ok": True, "traceability": _traceability(store, process)}


@router.get("/components")
def list_components(query: str = Query(default="")) -> dict[str, Any]:
    components = _read_store().get("components", [])
    if query:
        needle = query.casefold()
        components = [
            item
            for item in components
            if needle in json.dumps(
                {
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "aliases": item.get("aliases"),
                    "code_refs": item.get("code_refs"),
                    "api_endpoints": item.get("api_endpoints"),
                    "mcp_tools": item.get("mcp_tools"),
                },
                ensure_ascii=False,
            ).casefold()
        ]
    components = sorted(components, key=lambda item: (item.get("name") or "").casefold())
    enriched = [{**copy.deepcopy(item), "readiness": _component_readiness(item)} for item in components]
    return {"ok": True, "components": enriched, "count": len(enriched)}


@router.post("/components", status_code=201)
async def create_component(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Component payload must be an object.")
    component = _sanitize_component_payload(payload)
    with STORE_LOCK:
        store = _read_store()
        duplicate = next(
            (item for item in store.get("components", []) if component["slug"] in _component_search_keys(item)),
            None,
        )
        if duplicate:
            return {"ok": True, "created": False, "reused": True, "component": duplicate}
        store.setdefault("components", []).append(component)
        _write_store_unlocked(store)
    return {"ok": True, "created": True, "reused": False, "component": component}


@router.put("/components/{component_id}")
async def update_component(component_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Component payload must be an object.")
    with STORE_LOCK:
        store = _read_store()
        index = next(
            (index for index, item in enumerate(store.get("components", [])) if item.get("id") == component_id),
            None,
        )
        if index is None:
            raise HTTPException(status_code=404, detail="Component not found.")
        merged = {**store["components"][index], **payload}
        component = _sanitize_component_payload(merged, component_id=component_id)
        component["used_by_processes"] = store["components"][index].get("used_by_processes", [])
        store["components"][index] = component
        _write_store_unlocked(store)
    return {"ok": True, "component": component}


@router.delete("/components/{component_id}")
def delete_component(component_id: str, confirmed: bool = Query(default=False)) -> dict[str, Any]:
    if not confirmed:
        raise HTTPException(status_code=409, detail="Deletion requires confirmed=true.")
    with STORE_LOCK:
        store = _read_store()
        component = next((item for item in store.get("components", []) if item.get("id") == component_id), None)
        if not component:
            raise HTTPException(status_code=404, detail="Component not found.")
        if component.get("used_by_processes"):
            raise HTTPException(status_code=409, detail="Component is used by one or more processes and cannot be deleted.")
        store["components"] = [item for item in store.get("components", []) if item.get("id") != component_id]
        _write_store_unlocked(store)
    return {"ok": True, "deleted": component_id}


@router.post("/components/import")
async def import_component_manifest(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Component manifest must be an object.")
    raw_components = payload.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise HTTPException(status_code=400, detail="Component manifest must contain a components array.")
    if len(raw_components) > 250:
        raise HTTPException(status_code=400, detail="A component manifest can contain at most 250 components.")
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    manifest_provenance = {
        "source_application": _text(source.get("application"), 240),
        "source_repository": _text(source.get("repository"), 1_000),
        "source_commit": _text(source.get("commit"), 160),
        "manifest_version": _text(source.get("version"), 80),
        "verified_at": _text(source.get("verified_at"), 80),
    }
    created: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    with STORE_LOCK:
        store = _read_store()
        components = store.setdefault("components", [])
        for index, raw in enumerate(raw_components):
            if not isinstance(raw, dict):
                raise HTTPException(status_code=400, detail=f"Manifest component {index + 1} must be an object.")
            external_key = _text(raw.get("external_key") or raw.get("key"), 200)
            incoming_slug = _slug(raw.get("name"))
            existing_index = next(
                (
                    component_index
                    for component_index, item in enumerate(components)
                    if (external_key and item.get("external_key") == external_key)
                    or item.get("slug") == incoming_slug
                ),
                None,
            )
            prior = components[existing_index] if existing_index is not None else {}
            raw_provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
            merged = {
                **prior,
                **raw,
                "external_key": external_key or prior.get("external_key"),
                "provenance": {**(prior.get("provenance") or {}), **manifest_provenance, **raw_provenance},
            }
            component = _sanitize_component_payload(
                merged,
                component_id=prior.get("id", ""),
            )
            component["used_by_processes"] = prior.get("used_by_processes", [])
            if existing_index is None:
                components.append(component)
                created.append(copy.deepcopy(component))
            else:
                components[existing_index] = component
                updated.append(copy.deepcopy(component))
        _write_store_unlocked(store)
    ready = sum(_component_is_implemented(item) for item in [*created, *updated])
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "ready": ready,
        "count": len(created) + len(updated),
    }


@router.post("/components/{component_id}/build-spec")
async def generate_component_build_spec(component_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Build request must be an object.")
    store = _read_store()
    component = next((item for item in store.get("components", []) if item.get("id") == component_id), None)
    if not component:
        raise HTTPException(status_code=404, detail="Component not found.")
    supplied_spec = payload.get("build_spec") if isinstance(payload.get("build_spec"), dict) else None
    model = _ai_model()
    if supplied_spec is None:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise HTTPException(status_code=503, detail="AI component design needs OPENAI_API_KEY configured on the server.")
        context = {
            "component": {
                key: component.get(key)
                for key in (
                    "id", "name", "kind", "description", "capabilities", "supported_activities",
                    "dependencies", "configuration_keys", "code_refs", "api_endpoints", "mcp_tools", "test_refs",
                )
            },
            "business_context": _text(payload.get("business_context"), 12_000),
            "target_stack": _string_list(payload.get("target_stack"), limit=30, item_limit=500),
            "application_id": _text(payload.get("application_id"), 160),
        }
        try:
            supplied_spec = await asyncio.to_thread(
                _structured_ai_result,
                client=getOpenAPIClient(),
                model=model,
                instructions=COMPONENT_BUILD_INSTRUCTIONS,
                context=context,
                schema=COMPONENT_BUILD_SCHEMA,
                schema_name="aiready_component_build_spec",
                max_output_tokens=8_000,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"AI component design failed: {exc}") from exc
    now = _utc_now()
    with STORE_LOCK:
        store = _read_store()
        index = next(
            (index for index, item in enumerate(store.get("components", [])) if item.get("id") == component_id),
            None,
        )
        if index is None:
            raise HTTPException(status_code=404, detail="Component not found.")
        component = _sanitize_component_payload(
            {
                **store["components"][index],
                "status": "review",
                "implementation_status": "build-planned",
                "build_spec": {**supplied_spec, "generated_at": now, "model": model if payload.get("build_spec") is None else "provided"},
            },
            component_id=component_id,
        )
        component["used_by_processes"] = store["components"][index].get("used_by_processes", [])
        store["components"][index] = component
        _write_store_unlocked(store)
    return {"ok": True, "component": component, "build_spec": component["build_spec"]}


@router.post("/components/check")
async def check_components(request: Request) -> dict[str, Any]:
    payload = await request.json()
    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        raise HTTPException(status_code=400, detail="Provide an elements array.")
    store = _read_store()
    matches = []
    missing = []
    for index, raw in enumerate(elements[:MAX_PROCESS_ELEMENTS]):
        element = _sanitize_element(raw, index)
        if element.get("type") not in REUSABLE_NODE_TYPES or not element.get("name"):
            continue
        component = _find_component(store.get("components", []), element)
        row = {"element_id": element["id"], "element_name": element["name"]}
        if component:
            matches.append({**row, "component": component})
        else:
            missing.append(row)
    return {"ok": True, "checked": len(matches) + len(missing), "matches": matches, "missing": missing}


@router.post("/chat")
async def process_discovery_chat(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Chat payload must be an object.")
    message = _text(payload.get("message"), MAX_AI_MESSAGE_CHARS)
    if not message:
        raise HTTPException(status_code=400, detail="Describe the client's business flow.")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="AI intake needs OPENAI_API_KEY configured on the server.")
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    clean_history = []
    for item in history[-8:]:
        if not isinstance(item, dict):
            continue
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = _text(item.get("content"), 8_000)
        if content:
            clean_history.append({"role": role, "content": content})
    store = _read_store()
    context = {
        "latest_client_message": message,
        "conversation": clean_history,
        "interpretation_preferences": {
            "maximum_processes": MAX_AI_PROCESSES,
            "separate_explicit_phases": True,
            "require_cross_process_handoffs": True,
            "reuse_foundry_components_before_build": True,
            "removed_roles_must_be_reassigned_not_dropped": True,
        },
        "active_process": payload.get("active_process") if isinstance(payload.get("active_process"), dict) else {},
        "existing_portfolios": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "client_name": item.get("client_name"),
                "process_count": len(item.get("process_ids") or []),
            }
            for item in store.get("portfolios", [])[-20:]
        ],
        "existing_processes": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "client_name": item.get("client_name"),
                "purpose": item.get("purpose"),
            }
            for item in store.get("processes", [])[-20:]
        ],
        "foundry_components": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "aliases": item.get("aliases"),
            }
            for item in store.get("components", [])[:100]
        ],
    }
    model = _ai_model()
    try:
        client = getOpenAPIClient()
        plan = await asyncio.to_thread(
            _structured_ai_result,
            client=client,
            model=model,
            instructions=AI_PORTFOLIO_PLAN_INSTRUCTIONS,
            context=context,
            schema=AI_DISCOVERY_PLAN_SCHEMA,
            schema_name="devready_portfolio_plan",
            max_output_tokens=12_000,
        )
        phase_plans = plan.get("processes") if isinstance(plan.get("processes"), list) else []
        portfolio = plan.get("portfolio") if isinstance(plan.get("portfolio"), dict) else {}
        semaphore = asyncio.Semaphore(4)

        async def expand(index: int, phase_plan: dict[str, Any]) -> dict[str, Any]:
            adjacent = [
                {
                    key: item.get(key)
                    for key in ("temp_id", "phase_name", "phase_order", "entry_criteria", "exit_criteria")
                }
                for item in phase_plans[max(0, index - 1) : index + 2]
                if isinstance(item, dict) and item is not phase_plan
            ]
            async with semaphore:
                try:
                    return await asyncio.to_thread(
                        _expand_ai_phase,
                        client=client,
                        model=model,
                        message=message,
                        portfolio=portfolio,
                        phase_plan=phase_plan,
                        adjacent_phases=adjacent,
                    )
                except Exception as exc:
                    phase_name = _text(phase_plan.get("phase_name"), 240) or f"phase {index + 1}"
                    raise ValueError(f"Could not expand {phase_name}: {exc}") from exc

        expanded_processes = await asyncio.gather(
            *(expand(index, item) for index, item in enumerate(phase_plans) if isinstance(item, dict))
        )
        result = _normalize_ai_discovery({**plan, "processes": expanded_processes}, message)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI process discovery failed: {exc}") from exc
    return {
        "ok": True,
        "provider": "openai",
        "api": "responses",
        "model": model,
        "result": result,
    }


def _mcp_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "list_applications",
            "description": "List aiReady application blueprints with component gap and release-gate summaries.",
            "inputSchema": {
                "type": "object",
                "properties": {"client_name": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_application",
            "description": "Get one process-driven application blueprint, component requirements, assembly sequence, integrations, and release gates.",
            "inputSchema": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}},
                "required": ["application_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_portfolios",
            "description": "List connected DevReady client process portfolios and phase counts.",
            "inputSchema": {
                "type": "object",
                "properties": {"client_name": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_portfolio",
            "description": "Get an end-to-end portfolio, ordered phase processes, lanes, and cross-process handoffs.",
            "inputSchema": {
                "type": "object",
                "properties": {"portfolio_id": {"type": "string"}},
                "required": ["portfolio_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_processes",
            "description": "List DevReady client process-flow records.",
            "inputSchema": {
                "type": "object",
                "properties": {"client_name": {"type": "string"}, "domain": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_process",
            "description": "Get one process, its BPMN-linked activities, and validation result.",
            "inputSchema": {
                "type": "object",
                "properties": {"process_id": {"type": "string"}},
                "required": ["process_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "find_components",
            "description": "Search reusable components registered in AIReady Foundry.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_traceability",
            "description": "Get process-to-component, code, REST API, MCP tool, and link traceability.",
            "inputSchema": {
                "type": "object",
                "properties": {"process_id": {"type": "string"}},
                "required": ["process_id"],
                "additionalProperties": False,
            },
        },
    ]


@router.get("/mcp/manifest")
def mcp_manifest() -> dict[str, Any]:
    return {
        "name": "aiready-application-factory",
        "title": "aiReady Application Factory",
        "version": "2.0.0",
        "transport": "streamable-http-json-rpc",
        "endpoint": "/api/process-builder/mcp",
        "read_only": True,
        "tools": _mcp_tools(),
    }


@router.post("/mcp", response_model=None)
async def mcp_endpoint(request: Request) -> Any:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="MCP request must be a JSON-RPC object.")
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if method and method.startswith("notifications/"):
        return Response(status_code=204)
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "aiready-application-factory", "version": "2.0.0"},
            "instructions": "Read-only catalog for process-driven application blueprints, connected process portfolios, reusable components, implementation evidence, and release gates.",
        }
    elif method == "tools/list":
        result = {"tools": _mcp_tools()}
    elif method == "tools/call":
        tool_name = _text(params.get("name"), 120)
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        store = _read_store()
        if tool_name == "list_applications":
            rows = store.get("applications", [])
            if arguments.get("client_name"):
                needle = str(arguments["client_name"]).casefold()
                rows = [item for item in rows if needle in str(item.get("client_name", "")).casefold()]
            value = [
                {
                    key: copy.deepcopy(item.get(key))
                    for key in ("id", "name", "client_name", "portfolio_id", "status", "summary", "release_gates", "updated_at")
                }
                for item in rows
            ]
        elif tool_name == "get_application":
            value = next(
                (item for item in store.get("applications", []) if item.get("id") == arguments.get("application_id")),
                None,
            )
            if not value:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "Application blueprint not found."}], "isError": True},
                }
        elif tool_name == "list_portfolios":
            rows = store.get("portfolios", [])
            if arguments.get("client_name"):
                needle = str(arguments["client_name"]).casefold()
                rows = [item for item in rows if needle in str(item.get("client_name", "")).casefold()]
            value = [_portfolio_summary(item) for item in rows]
        elif tool_name == "get_portfolio":
            portfolio = next(
                (item for item in store.get("portfolios", []) if item.get("id") == arguments.get("portfolio_id")),
                None,
            )
            if not portfolio:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "Portfolio not found."}], "isError": True},
                }
            process_ids = set(portfolio.get("process_ids") or [])
            portfolio_processes = [
                copy.deepcopy(item)
                for item in store.get("processes", [])
                if item.get("id") in process_ids
            ]
            portfolio_processes.sort(key=lambda item: (item.get("phase_order") or 0, item.get("name") or ""))
            value = {"portfolio": copy.deepcopy(portfolio), "processes": portfolio_processes}
        elif tool_name == "list_processes":
            rows = store.get("processes", [])
            if arguments.get("client_name"):
                needle = str(arguments["client_name"]).casefold()
                rows = [item for item in rows if needle in str(item.get("client_name", "")).casefold()]
            if arguments.get("domain"):
                rows = [item for item in rows if item.get("domain") == arguments["domain"]]
            value = [_process_summary(item) for item in rows]
        elif tool_name == "get_process":
            value = next(
                (item for item in store.get("processes", []) if item.get("id") == arguments.get("process_id")),
                None,
            )
            if not value:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "Process not found."}], "isError": True},
                }
        elif tool_name == "find_components":
            needle = _text(arguments.get("query"), 240).casefold()
            value = [
                item
                for item in store.get("components", [])
                if not needle or needle in json.dumps(item, ensure_ascii=False).casefold()
            ]
        elif tool_name == "get_traceability":
            process = next(
                (item for item in store.get("processes", []) if item.get("id") == arguments.get("process_id")),
                None,
            )
            if not process:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "Process not found."}], "isError": True},
                }
            value = _traceability(store, process)
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown MCP tool: {tool_name}"},
            }
        result = {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
            "structuredContent": {"result": value},
            "isError": False,
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown MCP method: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.get("/reference")
def process_builder_reference() -> dict[str, Any]:
    store = _read_store()
    digest = hashlib.sha256(
        json.dumps(
            {
                "applications": [item.get("id") for item in store.get("applications", [])],
                "portfolios": [item.get("id") for item in store.get("portfolios", [])],
                "processes": [item.get("id") for item in store.get("processes", [])],
                "components": [item.get("id") for item in store.get("components", [])],
                "updated_at": store.get("updated_at"),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "product": "aiReady Application Factory",
        "process_builder": "DevReady Process Builder",
        "foundry": "AIReady Foundry",
        "ui": "/ui/pages/process-builder.html?domain=dev",
        "rest_base": "/api/process-builder",
        "portfolio_api": "/api/process-builder/portfolios",
        "application_api": "/api/process-builder/applications",
        "process_api": "/api/process-builder/processes",
        "component_api": "/api/process-builder/components",
        "component_manifest_api": "/api/process-builder/components/import",
        "mcp": "/api/process-builder/mcp",
        "mcp_manifest": "/api/process-builder/mcp/manifest",
        "code": [
            "backend/process_builder.py",
            "backend/ui/pages/process-builder.html",
            "backend/ui/pages/JS/processBuilder.js",
            "backend/ui/pages/CSS/processBuilder.css",
        ],
        "catalog_fingerprint": digest,
    }
