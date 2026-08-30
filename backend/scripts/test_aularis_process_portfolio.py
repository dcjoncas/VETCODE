"""Exercise the DevReady AI intake with the eight-phase Aularis specification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = BACKEND / "tests" / "fixtures" / "aularis_tax_equity_eight_phase.txt"
EXPECTED_LANES = {
    "referring advisor & client",
    "business development & capital",
    "engagement, closing & systems",
    "project diligence & information",
    "compliance & client experience",
    "counsel, escrow & counterparties",
}
EXPECTED_ACTIVITIES = {
    1: [
        "Develop capital partnerships",
        "Onboard advisor as a VFO Hub member firm",
        "Produce education and program materials",
        "Conduct advisor education session",
        "Advisor presents program to client",
        "Source and pipeline projects",
    ],
    2: [
        "Build Aularis illustration",
        "Technical Q&A on the illustration",
        "Advisor confirms fit",
        "Film floors",
        "EOI addendum submitted",
    ],
    3: [
        "Engagement Agreement and version-locked T&Cs signed",
        "Client intake package",
        "Client engages panel counsel",
        "Match client to project",
        "Participation onboarding",
    ],
    4: [
        "Issue escrow instructions",
        "Client wires deposits",
        "Escrow agent receives and holds funds",
        "confirm funds and reconcile to EOI",
    ],
    5: [
        "Final diligence and closing checklist",
        "Panel counsel reviews for client",
        "Execute closing and admit Member/Managers",
        "Settlement statement and disbursement",
        "Compensation flows",
    ],
    6: [
        "Client performs and logs services",
        "Run activity program and hour tracking",
        "Description review and QC",
        "Keep other active participants below threshold",
        "Compliance design and code monitoring",
        "year-end participation certification",
    ],
    7: [
        "Advisor and investor technical desk",
        "Partnership return and K-1 preparation",
        "Advisor/CPA integrates K-1",
        "Advisor relations and program updates",
    ],
    8: [
        "Inclusive program gateway",
        "Renewable path",
        "Film path",
        "Annual entity administration",
        "Members continue engagement",
        "Renewal to next transaction",
    ],
}


def post_json(url: str, payload: dict, timeout: int) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"Could not reach {url}: {exc}") from exc
    except TimeoutError as exc:
        raise SystemExit(f"Timed out after {timeout} seconds waiting for {url}") from exc


def validate(result: dict) -> list[str]:
    errors: list[str] = []
    processes = result.get("processes") if isinstance(result.get("processes"), list) else []
    portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), dict) else {}
    orders = [int(item.get("phase_order") or 0) for item in processes if isinstance(item, dict)]
    lane_names = [
        str(item.get("name") or "")
        for item in portfolio.get("lanes") or []
        if isinstance(item, dict)
    ]
    if len(processes) != 8:
        errors.append(f"expected 8 processes, received {len(processes)}")
    if sorted(orders) != list(range(1, 9)):
        errors.append(f"phase orders are not 1-8: {orders}")
    if any("solution architect" in name.casefold() for name in lane_names):
        errors.append("Solution Architect remained as a business lane")
    normalized_lanes = {name.casefold().split(" — ", 1)[0].strip() for name in lane_names}
    if normalized_lanes != EXPECTED_LANES:
        errors.append(f"expected the six source lanes, received {lane_names}")
    if len(portfolio.get("handoffs") or []) < 7:
        errors.append("fewer than 7 cross-process handoffs were returned")
    if processes and not all(item.get("predecessor_temp_ids") or item.get("phase_order") == 1 for item in processes):
        errors.append("one or more non-first processes has no predecessor")
    if processes and not all(item.get("successor_temp_ids") or item.get("phase_order") == 8 for item in processes):
        errors.append("one or more non-final processes has no successor")
    if not result.get("discovery_complete"):
        errors.append("the AI marked discovery incomplete")
    for process in processes:
        order = int(process.get("phase_order") or 0)
        steps = process.get("steps") if isinstance(process.get("steps"), list) else []
        connections = process.get("connections") if isinstance(process.get("connections"), list) else []
        step_ids = {str(step.get("id") or "") for step in steps if isinstance(step, dict)}
        step_names = [str(step.get("name") or "") for step in steps if isinstance(step, dict)]
        step_types = {str(step.get("type") or "") for step in steps if isinstance(step, dict)}
        if "start_event" not in step_types or "end_event" not in step_types:
            errors.append(f"phase {order} is missing a start or end event")
        for connection in connections:
            if connection.get("from") not in step_ids or connection.get("to") not in step_ids:
                errors.append(f"phase {order} has a broken connection {connection.get('id')}")
        for expected in EXPECTED_ACTIVITIES.get(order, []):
            if not any(expected.casefold() in name.casefold() for name in step_names):
                errors.append(f"phase {order} omitted source activity: {expected}")
        if any(
            "solution architect" in str(step.get("owner") or "").casefold()
            for step in steps
            if isinstance(step, dict)
        ):
            errors.append(f"phase {order} reassigned work back to Solution Architect")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    prompt = args.prompt.read_text(encoding="utf-8")
    response = post_json(
        f"{args.base_url.rstrip('/')}/api/process-builder/chat",
        {"message": prompt, "history": [], "active_process": {}},
        args.timeout,
    )
    result = response.get("result") or {}
    errors = validate(result)
    processes = result.get("processes") or []
    portfolio = result.get("portfolio") or {}

    print(f"Portfolio: {portfolio.get('name')}")
    print(f"Processes: {len(processes)}")
    print(f"Lanes: {len(portfolio.get('lanes') or [])}")
    print(f"Handoffs: {len(portfolio.get('handoffs') or [])}")
    print(f"Model: {response.get('model')}")
    for process in processes:
        print(
            f"  Phase {int(process.get('phase_order') or 0):02d}: "
            f"{process.get('phase_name') or process.get('name')} "
            f"({len(process.get('steps') or [])} elements)"
        )
    if args.json_out:
        args.json_out.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {args.json_out}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("PASS: eight connected phases, ordered handoffs, and no Solution Architect business lane")


if __name__ == "__main__":
    main()
