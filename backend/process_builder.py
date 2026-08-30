"""DevReady Process Builder and AIReady Foundry APIs.

The browser owns BPMN rendering and manual editing. This module owns durable
process records, reusable component reconciliation, deterministic validation,
AI-assisted discovery, traceability, and a read-only MCP catalog surface.
"""

from __future__ import annotations

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
        "schema_version": 1,
        "updated_at": now,
        "processes": [],
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
                    data.setdefault("schema_version", 1)
                    data.setdefault("processes", [])
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
        data.setdefault("schema_version", 1)
        data.setdefault("processes", [])
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
    return {
        "id": component_id or _text(payload.get("id"), 160) or _new_id("cmp"),
        "name": name,
        "slug": _slug(name),
        "kind": _text(payload.get("kind"), 80) or "activity",
        "status": _text(payload.get("status"), 40) or "draft",
        "version": _text(payload.get("version"), 40) or "0.1.0",
        "description": _text(payload.get("description"), 4_000),
        "aliases": _string_list(payload.get("aliases"), limit=20, item_limit=240),
        "implementation_status": _text(payload.get("implementation_status"), 80)
        or ("implemented" if any(refs.values()) else "design-only"),
        **refs,
        "used_by_processes": _string_list(payload.get("used_by_processes"), limit=500, item_limit=160),
        "created_at": _text(payload.get("created_at"), 80) or now,
        "updated_at": now,
    }


def _component_search_keys(component: dict[str, Any]) -> set[str]:
    values = [component.get("name"), component.get("slug"), *(component.get("aliases") or [])]
    return {_slug(value) for value in values if _text(value)}


def _find_component(components: list[dict[str, Any]], element: dict[str, Any]) -> dict[str, Any] | None:
    requested_id = _text(element.get("component_id"), 160)
    if requested_id:
        direct = next((component for component in components if component.get("id") == requested_id), None)
        if direct:
            return direct
    key = _slug(element.get("name"))
    if key == "component":
        return None
    exact = [component for component in components if key in _component_search_keys(component)]
    if len(exact) == 1:
        return exact[0]
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
        "steps": {"type": "array", "minItems": 2, "maxItems": 40, "items": AI_STEP_SCHEMA},
        "connections": {
            "type": "array",
            "maxItems": 80,
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

AI_DISCOVERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["assistant_message", "needs_clarification", "discovery_complete", "client_name", "processes"],
    "properties": {
        "assistant_message": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "discovery_complete": {"type": "boolean"},
        "client_name": {"type": "string"},
        "processes": {"type": "array", "maxItems": 5, "items": AI_PROCESS_SCHEMA},
    },
}

AI_DISCOVERY_INSTRUCTIONS = """You are the DevReady Process Discovery Agent for client business-flow intake.
Turn ordinary client descriptions into reviewable BPMN-oriented process drafts. This product is DevReady and AIReady Foundry; it is not SAP, HPCC, or Syntax.

Conversation rules:
- The client may have up to five major business flows. Identify all flows they described, but never invent a sixth.
- If essential facts are missing, ask exactly one concise, high-value question and preserve any already-built drafts.
- Essential facts are: process purpose, trigger, end outcome, major actors/owners, systems, decision paths, exceptions, controls, and success measures.
- Build a draft once the description is sufficient. Drafts still require human validation; never claim client approval.
- Include a start_event and at least one end_event. Every connection must reference a step ID in the same process.
- Use service_task for API/automation, call_activity for a reusable subprocess, user_task for system-assisted human work, manual_task for offline work, business_rule_task for a rule check, and decision for exclusive branching.
- Do not fabricate code paths, API routes, MCP tools, URLs, credentials, systems, controls, or owners. Leave unknown reference arrays empty and ask for the missing technical evidence later.
- When the client names code, an API, an MCP tool/server, or a link, attach it to the exact step that uses it.
- Each reusable activity will be checked against AIReady Foundry after the user accepts the draft. Do not decide that a component exists unless the supplied Foundry summary proves it.
- Return only the requested strict JSON object."""


def _ai_model() -> str:
    return (
        os.getenv("OPENAI_PROCESS_MODEL")
        or os.getenv("OPENAI_AGENT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-4o-mini"
    )


@router.get("/health")
def process_builder_health() -> dict[str, Any]:
    store = _read_store()
    return {
        "ok": True,
        "product": "DevReady Process Builder",
        "foundry": "AIReady Foundry",
        "processes": len(store.get("processes", [])),
        "components": len(store.get("components", [])),
        "ai_ready": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "model": _ai_model(),
        "rest_base": "/api/process-builder",
        "mcp_endpoint": "/api/process-builder/mcp",
        "storage": STORAGE_STATE,
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
    processes = sorted(processes, key=lambda item: item.get("updated_at", ""), reverse=True)
    return {"ok": True, "processes": [_process_summary(item) for item in processes], "count": len(processes)}


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
    return {"ok": True, "components": components, "count": len(components)}


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
    message = _text(payload.get("message"), 8_000)
    if not message:
        raise HTTPException(status_code=400, detail="Describe the client's business flow.")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="AI intake needs OPENAI_API_KEY configured on the server.")
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    clean_history = []
    for item in history[-12:]:
        if not isinstance(item, dict):
            continue
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = _text(item.get("content"), 4_000)
        if content:
            clean_history.append({"role": role, "content": content})
    store = _read_store()
    context = {
        "latest_client_message": message,
        "conversation": clean_history,
        "active_process": payload.get("active_process") if isinstance(payload.get("active_process"), dict) else {},
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
        response = getOpenAPIClient().responses.create(
            model=model,
            instructions=AI_DISCOVERY_INSTRUCTIONS,
            input=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "devready_process_discovery",
                    "strict": True,
                    "schema": AI_DISCOVERY_SCHEMA,
                }
            },
        )
        output = _output_text(response)
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="OpenAI returned an unreadable process draft.") from exc
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
        "name": "devready-aiready-foundry",
        "title": "DevReady AIReady Foundry",
        "version": "1.0.0",
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
            "serverInfo": {"name": "devready-aiready-foundry", "version": "1.0.0"},
            "instructions": "Read-only catalog for DevReady processes, reusable components, and implementation traceability.",
        }
    elif method == "tools/list":
        result = {"tools": _mcp_tools()}
    elif method == "tools/call":
        tool_name = _text(params.get("name"), 120)
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        store = _read_store()
        if tool_name == "list_processes":
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
                "processes": [item.get("id") for item in store.get("processes", [])],
                "components": [item.get("id") for item in store.get("components", [])],
                "updated_at": store.get("updated_at"),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "product": "DevReady Process Builder",
        "foundry": "AIReady Foundry",
        "ui": "/ui/pages/process-builder.html?domain=dev",
        "rest_base": "/api/process-builder",
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
