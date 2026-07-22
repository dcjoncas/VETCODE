#!/usr/bin/env python3
"""Run the LegalReady training workflow without sending email or calendar invites.

The execute mode creates a training JD and candidate profile in the law domain,
runs matching, generates two OpenAI-backed email drafts, archives a candidate
review and a client interview, then reads the archived records back as proof.
It deliberately never calls /api/calendar/invite/create.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://vetcode-dev.up.railway.app"
DEFAULT_EMAIL = "mitch.blake@legalready.io"


class ApiError(RuntimeError):
    pass


def _decode_response(response: Any) -> Any:
    raw = response.read()
    text = raw.decode("utf-8", errors="replace")
    content_type = response.headers.get("Content-Type", "")
    if "json" in content_type or text[:1] in {"{", "["}:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def _open(req: request.Request, timeout: int = 90) -> Any:
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, _decode_response(response)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code} for {req.full_url}: {body[:1200]}") from exc
    except error.URLError as exc:
        raise ApiError(f"Connection failed for {req.full_url}: {exc.reason}") from exc


def get(base_url: str, path: str, timeout: int = 60) -> tuple[int, Any]:
    req = request.Request(f"{base_url.rstrip('/')}{path}", headers={"Cache-Control": "no-cache"})
    return _open(req, timeout=timeout)


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: int = 120) -> Any:
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    _, body = _open(req, timeout=timeout)
    return body


def post_form(base_url: str, path: str, fields: dict[str, Any], timeout: int = 120) -> Any:
    encoded = parse.urlencode({key: value if isinstance(value, str) else json.dumps(value) for key, value in fields.items()}).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    _, body = _open(req, timeout=timeout)
    return body


def post_multipart(
    base_url: str,
    path: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    timeout: int = 180,
) -> Any:
    boundary = f"----LegalReadyTraining{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
    )
    _, response_body = _open(req, timeout=timeout)
    return response_body


def iso_future(hour: int, minute: int = 0) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    candidate = (now + dt.timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    while candidate.weekday() >= 5:
        candidate += dt.timedelta(days=1)
    return candidate.isoformat().replace("+00:00", "Z")


def add_minutes(value: str, minutes: int) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def safe_value(value: Any, limit: int = 400) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text[:limit]


def html_report(report: dict[str, Any]) -> str:
    rows = []
    for step in report["steps"]:
        css = "pass" if step["ok"] else "fail"
        rows.append(
            f'<tr><td>{html.escape(step["name"])}</td><td class="{css}">{"PASS" if step["ok"] else "FAIL"}</td>'
            f'<td>{html.escape(step.get("detail", ""))}</td></tr>'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LegalReady E2E Training Evidence</title>
<style>
body{{font:15px/1.5 Arial,sans-serif;color:#17211b;margin:36px;max-width:1100px}}
h1,h2{{color:#134b2f}} .summary{{padding:14px 18px;border-left:5px solid #69a932;background:#f1f8ec}}
table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #cfd9d1;padding:9px;text-align:left;vertical-align:top}}
th{{background:#edf4ef}}.pass{{color:#176b38;font-weight:700}}.fail{{color:#a12622;font-weight:700}}
code{{background:#f3f5f4;padding:2px 5px}}small{{color:#58635c}}
</style></head><body>
<h1>LegalReady End-to-End Training Evidence</h1>
<div class="summary"><strong>{"PASS" if report["ok"] else "FAIL"}</strong> - {html.escape(report["run_id"])}<br>
Trainee routing: <code>{html.escape(report["email"])}</code><br>
No email or calendar invite was sent. Draft and archive endpoints only.</div>
<h2>Validated workflow</h2><table><thead><tr><th>Step</th><th>Result</th><th>Evidence</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Result IDs</h2><pre>{html.escape(json.dumps(report.get("artifacts", {}), indent=2))}</pre>
<small>Generated {html.escape(report["generated_at"])}</small>
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the LegalReady training workflow.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--domain", default="law")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--trainee-name", default="Mitch Blake")
    parser.add_argument("--resume", type=Path, default=ROOT / "Sample-Resume-Jordan-Ellis-Mitch.docx")
    parser.add_argument("--jd", type=Path, default=ROOT / "Sample-JD-Legal-Operations-eDiscovery-Analyst.docx")
    parser.add_argument("--execute", action="store_true", help="Create training records and AI drafts. Never sends invitations.")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    args.results_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"LR-TRAIN-{run_stamp}-{uuid.uuid4().hex[:6].upper()}"
    report: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "base_url": args.base_url,
        "domain": args.domain,
        "email": args.email,
        "trainee_name": args.trainee_name,
        "mode": "execute" if args.execute else "readiness-only",
        "no_send_guarantee": "The runner never calls /api/calendar/invite/create.",
        "steps": [],
        "artifacts": {},
    }

    def record(name: str, ok: bool, detail: str, **extra: Any) -> None:
        report["steps"].append({"name": name, "ok": bool(ok), "detail": detail, **extra})
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    pages = [
        "/",
        "/api/environment",
        f"/ui/pages/job-descriptions.html?domain={parse.quote(args.domain)}",
        f"/ui/pages/find-candidate.html?domain={parse.quote(args.domain)}",
        f"/ui/pages/match-role.html?domain={parse.quote(args.domain)}",
        f"/ui/pages/profile-preview.html?domain={parse.quote(args.domain)}",
        f"/ui/pages/client-comm.html?domain={parse.quote(args.domain)}",
        f"/ui/pages/schedule-interview.html?domain={parse.quote(args.domain)}&interview=ready",
        f"/ui/pages/schedule-interview.html?domain={parse.quote(args.domain)}&interview=client",
        f"/ui/pages/status-tracker.html?domain={parse.quote(args.domain)}",
    ]
    for path in pages:
        try:
            status, _ = get(args.base_url, path)
            record(f"Page/API reachable: {path}", status == 200, f"HTTP {status}")
        except Exception as exc:
            record(f"Page/API reachable: {path}", False, str(exc))

    try:
        _, health = get(args.base_url, "/api/calendar/health")
        record("Calendar and AI health", bool(health.get("ok")), safe_value(health))
        report["artifacts"]["calendar_health"] = health
    except Exception as exc:
        record("Calendar and AI health", False, str(exc))

    if not args.execute:
        report["ok"] = all(step["ok"] for step in report["steps"])
        return write_report(report, args.results_dir, run_stamp)

    if not args.jd.exists() or not args.resume.exists():
        missing = [str(path) for path in (args.jd, args.resume) if not path.exists()]
        record("Training files present", False, f"Missing: {', '.join(missing)}")
        report["ok"] = False
        return write_report(report, args.results_dir, run_stamp)
    record("Training files present", True, f"JD={args.jd.name}; resume={args.resume.name}")

    jd_id = ""
    profile_id = ""
    profile_name = "Jordan Ellis"
    try:
        jd = post_multipart(
            args.base_url,
            "/api/azureJobs/uploadJob",
            {
                "company": "Summit & Vale LLP - LegalReady Training",
                "title": "Legal Operations & eDiscovery Analyst - Training",
                "domain": args.domain,
            },
            "file",
            args.jd,
        )
        jd_id = str(jd.get("jd_id") or "")
        record("Create training job description", bool(jd_id), f"JD ID {jd_id}; extracted skills {len(jd.get('jd_skills') or [])}")
        report["artifacts"]["jd"] = {"jd_id": jd_id, "company": jd.get("company"), "title": jd.get("title")}
    except Exception as exc:
        record("Create training job description", False, str(exc))

    try:
        resume = post_multipart(
            args.base_url,
            "/api/azure/resume/upload",
            {"source_type": "docx", "domain": args.domain},
            "file",
            args.resume,
        )
        profile_id = str(resume.get("personid") or "")
        profile_name = resume.get("name") or profile_name
        record("Upload résumé and generate profile", bool(profile_id), f"Profile ID {profile_id}; name {profile_name}; existing={bool(resume.get('existing'))}")
        report["artifacts"]["profile"] = {"profile_id": profile_id, "name": profile_name, "email": args.email}
    except Exception as exc:
        record("Upload résumé and generate profile", False, str(exc))

    if profile_id:
        try:
            _, profile = get(args.base_url, f"/api/azure/getProfile/{parse.quote(profile_id)}?domain={parse.quote(args.domain)}")
            profile_data = profile.get("profile") or profile
            profile_email = profile_data.get("email") or ""
            record("Read generated profile", profile_email.lower() == args.email.lower(), f"Profile email {profile_email}; profile returned")
        except Exception as exc:
            record("Read generated profile", False, str(exc))

    if jd_id:
        try:
            match = post_form(
                args.base_url,
                "/api/azureJobs/match/run",
                {"domain": args.domain, "jd_id": jd_id, "top_k": "50", "external_source": "none"},
                timeout=180,
            )
            results = match.get("results") or []
            selected = next((row for row in results if str(row.get("profile_id")) == profile_id), None)
            if selected is None:
                selected = next((row for row in results if (row.get("email") or "").lower() == args.email.lower()), None)
            score = selected.get("score") if selected else None
            qualified = selected is not None and isinstance(score, (int, float)) and score >= 50
            record("Rank candidate against JD", qualified, f"Candidate found in {len(results)} results; score={score}; training threshold=50")
            report["artifacts"]["match"] = {
                "candidate_found": selected is not None,
                "score": score,
                "top_matches": (selected or {}).get("top_matches") or [],
            }
        except Exception as exc:
            record("Rank candidate against JD", False, str(exc))

    common_payload = {
        "candidate_email": args.email,
        "candidate_name": f"{profile_name} (Training Candidate)",
        "role": "Legal Operations & eDiscovery Analyst - Training",
        "company": "Summit & Vale LLP - LegalReady Training",
        "job_description": "Legal operations and eDiscovery support using Python, SQL, Power BI, Microsoft Excel, SharePoint, Microsoft 365, and Adobe Acrobat.",
        "ai_context": "Training candidate with legal operations and eDiscovery experience using Python, SQL, Power BI, Microsoft Excel, SharePoint, Microsoft 365, and Adobe Acrobat.",
        "duration_minutes": 45,
        "location": "Microsoft Teams - LegalReady training",
        "talking_points": ["Interest in the role", "Litigation workflow experience", "Availability", "Questions and next steps"],
    }
    candidate_draft: dict[str, Any] = {}
    client_draft: dict[str, Any] = {}
    try:
        candidate_draft = post_json(
            args.base_url,
            "/api/calendar/invite/draft",
            {
                **common_payload,
                "interview_type": "ready",
                "ready_purpose": "role",
                "interviewers": [{"name": args.trainee_name, "email": args.email}],
                "attendees": [],
            },
            timeout=180,
        )
        record("Generate candidate-review email", bool(candidate_draft.get("email_body")), candidate_draft.get("email_subject") or "Draft returned")
    except Exception as exc:
        record("Generate candidate-review email", False, str(exc))

    try:
        client_draft = post_json(
            args.base_url,
            "/api/calendar/invite/draft",
            {
                **common_payload,
                "interview_type": "client",
                "ready_purpose": "",
                "interviewers": [],
                "attendees": [{"name": "Avery Stone (Training Client)", "email": args.email}],
            },
            timeout=180,
        )
        record("Generate client-interview email", bool(client_draft.get("email_body")), client_draft.get("email_subject") or "Draft returned")
    except Exception as exc:
        record("Generate client-interview email", False, str(exc))

    candidate_start = iso_future(16)
    client_start = iso_future(19)
    archive_payloads = [
        {
            "id": f"{run_id}-CANDIDATE",
            "domain": args.domain,
            "status": "training-draft-validated",
            "interviewType": "ready",
            "readyPurpose": "role",
            "candidateId": profile_id,
            "candidateName": profile_name,
            "candidateEmail": args.email,
            "candidateInterviewerName": args.trainee_name,
            "candidateInterviewerEmail": args.email,
            "role": "Legal Operations & eDiscovery Analyst - Training",
            "company": "Summit & Vale LLP - LegalReady Training",
            "scheduledStart": candidate_start,
            "scheduledEnd": add_minutes(candidate_start, 45),
            "subject": candidate_draft.get("email_subject") or "Candidate Review - LegalReady Training",
            "message": candidate_draft.get("email_body") or "Candidate review training draft validated.",
            "provider": "training-no-send",
        },
        {
            "id": f"{run_id}-CLIENT",
            "domain": args.domain,
            "status": "training-draft-validated",
            "interviewType": "client",
            "candidateId": profile_id,
            "candidateName": profile_name,
            "candidateEmail": args.email,
            "clientCompany": "Summit & Vale LLP - LegalReady Training",
            "clientContactName": "Avery Stone (Training Client)",
            "clientContactEmail": args.email,
            "attendees": [args.email],
            "role": "Legal Operations & eDiscovery Analyst - Training",
            "company": "Summit & Vale LLP - LegalReady Training",
            "scheduledStart": client_start,
            "scheduledEnd": add_minutes(client_start, 45),
            "subject": client_draft.get("email_subject") or "Client Interview - LegalReady Training",
            "message": client_draft.get("email_body") or "Client interview training draft validated.",
            "provider": "training-no-send",
        },
    ]
    archived_ids: list[str] = []
    for payload in archive_payloads:
        label = "candidate review" if payload["interviewType"] == "ready" else "client interview"
        try:
            archived = post_form(
                args.base_url,
                "/api/interviews/archive",
                {"record_json": json.dumps(payload), "domain": args.domain},
            )
            archived_id = str((archived.get("record") or {}).get("id") or "")
            archived_ids.append(archived_id)
            record(f"Archive {label}", archived_id == payload["id"], f"Archive ID {archived_id}; provider training-no-send")
        except Exception as exc:
            record(f"Archive {label}", False, str(exc))

    for archived_id in archived_ids:
        try:
            _, verified = get(
                args.base_url,
                f"/api/interviews/archive?domain={parse.quote(args.domain)}&record_id={parse.quote(archived_id)}&limit=1",
            )
            records = verified.get("records") or []
            same_email = bool(records) and args.email.lower() in json.dumps(records[0]).lower()
            record(f"Verify archive {archived_id}", bool(records) and same_email, f"Read-back count={len(records)}; trainee email present={same_email}")
        except Exception as exc:
            record(f"Verify archive {archived_id}", False, str(exc))

    report["artifacts"]["archive_ids"] = archived_ids
    report["artifacts"]["candidate_review_time_utc"] = candidate_start
    report["artifacts"]["client_interview_time_utc"] = client_start
    report["ok"] = all(step["ok"] for step in report["steps"])
    return write_report(report, args.results_dir, run_stamp)


def write_report(report: dict[str, Any], results_dir: Path, run_stamp: str) -> int:
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / f"legalready-e2e-{run_stamp}.json"
    html_path = results_dir / f"legalready-e2e-{run_stamp}.html"
    latest_json = results_dir / "legalready-e2e-latest.json"
    latest_html = results_dir / "legalready-e2e-latest.html"
    rendered_json = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(rendered_json, encoding="utf-8")
    latest_json.write_text(rendered_json, encoding="utf-8")
    rendered_html = html_report(report)
    html_path.write_text(rendered_html, encoding="utf-8")
    latest_html.write_text(rendered_html, encoding="utf-8")
    print(f"RESULT  {json_path}")
    print(f"RESULT  {html_path}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
