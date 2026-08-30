# DevReady Process Builder and AIReady Foundry

## Purpose

The Process Builder captures a client's major business flows in two complementary ways:

1. A BPMN 2.0 modeler keeps direct manual creation, import, editing, undo/redo, and BPMN/SVG export.
2. The DevReady Process Agent accepts an ordinary business description, asks one clarification at a time, and stages up to five BPMN-oriented process drafts for human review.

The implementation is DevReady-specific. It does not include the source package's Syntax, SAP, SAP Cloud ALM, or HPCC branding/integrations.

## Routes

| Surface | Route |
| --- | --- |
| Process Builder | `/ui/pages/process-builder.html?domain=dev` |
| Embedded Builder | `/ui/pages/process-builder.html?domain=dev&embed=1` |
| Foundry view | `/ui/pages/process-builder.html?domain=dev#foundry` |
| Health | `GET /api/process-builder/health` |
| Process library | `GET/POST /api/process-builder/processes` |
| Process record | `GET/PUT/DELETE /api/process-builder/processes/{id}` |
| Validation | `POST /api/process-builder/validate` |
| Traceability | `GET /api/process-builder/processes/{id}/traceability` |
| Component catalog | `GET/POST /api/process-builder/components` |
| Component record | `PUT/DELETE /api/process-builder/components/{id}` |
| Existence check | `POST /api/process-builder/components/check` |
| AI discovery | `POST /api/process-builder/chat` |
| MCP manifest | `GET /api/process-builder/mcp/manifest` |
| MCP endpoint | `POST /api/process-builder/mcp` |
| Code/API reference | `GET /api/process-builder/reference` |

## Reuse-before-build contract

Saving a process performs a Foundry reconciliation before the record is written:

- Each task, activity, subprocess, and decision gateway is checked by explicit component ID, normalized name/alias, and exact implementation references.
- A single match reuses the existing component.
- No match creates a new `draft` component linked to the source process and BPMN element.
- Foundry usage is rebuilt from saved process links so the catalog shows every consuming process.
- Component deletion is blocked while a saved process uses it.

The check-only API never creates components. Creation occurs only when a user explicitly saves the process or submits the component form.

## Process-to-implementation traceability

Every BPMN activity can carry:

- Foundry component ID
- source-code paths
- REST/API endpoints
- MCP server or tool names
- external documentation or application links
- owner, system, control/evidence, SLA, and description

The canvas shows compact Foundry, code, API, and MCP badges. The Trace tab resolves activity metadata against the linked component and displays the final implementation map.

## AI behavior

The backend reuses the existing server-side `OPENAI_API_KEY`. No secret is sent to the browser or stored in a process record. `OPENAI_PROCESS_MODEL` is optional; the fallback order is:

1. `OPENAI_PROCESS_MODEL`
2. `OPENAI_AGENT_MODEL`
3. `OPENAI_MODEL`
4. `gpt-4o-mini`

The agent uses the OpenAI Responses API with a strict JSON schema. It must preserve client facts, leave unknown technical references empty, stage changes without saving, and never claim that a client approved a process.

## Validation

Automated validation checks start/end events, names, connectivity, broken references, Foundry links, process owner/purpose, and implementation evidence for integrations. A separate five-item checklist requires a person to confirm:

- boundaries and exclusions
- owners and handoffs
- decisions, exceptions, and rework
- systems, data, APIs, and MCP tools
- controls, evidence, SLAs, and success measures

A process cannot be marked `validated` until structural checks pass and every client checklist item is confirmed in the current review.

## Storage

When the existing Azure PostgreSQL variables are configured, the catalog is stored in `devready_process_builder_store` as a JSONB document and survives Railway restarts/deployments. The local JSON file is maintained as a fallback/cache. Set `PROCESS_BUILDER_DATABASE_ENABLED=false` for isolated local or test runs.

## MCP

The endpoint implements a read-only JSON-RPC MCP catalog with these tools:

- `list_processes`
- `get_process`
- `find_components`
- `get_traceability`

Write control stays in the reviewed Process Builder and REST workflows. MCP clients can inspect the architecture without silently modifying client processes or Foundry components.
