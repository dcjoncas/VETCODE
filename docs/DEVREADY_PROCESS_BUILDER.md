# DevReady Process Builder and AIReady Foundry

## Purpose

The Process Builder captures a client's major business flows in two complementary ways:

1. A BPMN 2.0 modeler keeps direct manual creation, import, editing, undo/redo, and BPMN/SVG export.
2. The DevReady Process Agent accepts up to 30,000 characters of ordinary business description, distinguishes phases from role lanes, and stages up to twelve connected BPMN-oriented process drafts for human review.

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
| Connected portfolios | `GET/POST /api/process-builder/portfolios` |
| Portfolio record | `GET /api/process-builder/portfolios/{id}` |
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

Saving a process performs a Foundry reconciliation before the record is written. Saving an AI-created portfolio does the same work atomically across every phase:

- Each task, activity, subprocess, and decision gateway is checked by explicit component ID, normalized name/alias, and exact implementation references.
- A single match reuses the existing component.
- No match creates a new `draft` component linked to the source process and BPMN element.
- Foundry usage is rebuilt from saved process links so the catalog shows every consuming process.
- Component deletion is blocked while a saved process uses it.
- A repeated save of the same client/portfolio/phase keys updates the existing records and reuses their component links instead of creating a duplicate portfolio.

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

The agent uses the OpenAI Responses API with strict JSON schemas. Intake is staged to keep large specifications reliable:

1. A portfolio-planning pass identifies the explicitly named phases, recurring role lanes, ordered activity coverage, variants, entry/exit criteria, and cross-process handoffs.
2. Bounded phase-expansion calls generate BPMN steps and internal connections for each planned phase.
3. Deterministic normalization enforces unique phase IDs, adjacent predecessor/successor links, requested role removal, and minimum structural completeness.

For GPT-5, the builder requests low reasoning effort so the response budget is used primarily for the strict structured artifact. Open owner/control assumptions remain clarification and validation items; they do not erase an otherwise complete draft. Client facts must be preserved, unknown technical references stay empty, drafts are staged without saving, and the agent never claims client approval.

The eight-phase Aularis regression prompt is stored at `backend/tests/fixtures/aularis_tax_equity_eight_phase.txt`. With a local server on port 8765, run:

```powershell
python backend/scripts/test_aularis_process_portfolio.py --base-url http://127.0.0.1:8765
```

The check requires eight ordered processes, the six retained source lanes, explicit cross-process handoffs, valid internal connection references, start/end events, all 41 named source activities, and no Solution Architecture business lane.

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

- `list_portfolios`
- `get_portfolio`
- `list_processes`
- `get_process`
- `find_components`
- `get_traceability`

Write control stays in the reviewed Process Builder and REST workflows. MCP clients can inspect the architecture without silently modifying client processes or Foundry components.
