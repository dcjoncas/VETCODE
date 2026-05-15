from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATA_DIR = BACKEND / "data"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from scripts.seed_devready_client_examples import (  # noqa: E402
    assert_dev_database,
    dotenv_values,
    email_for,
    pg_conn,
    resolve_pg_skill,
    slug,
    split_location,
    sync_sequence,
)


DOMAIN_CONFIG = {
    "dev": {
        "name": "DevReady",
        "email_domain": "devready.example",
        "client": "Atlas AI Health",
        "industry": "Healthcare Technology",
        "title": "AI Workflow Engineer",
        "location": "Denver, CO",
        "skills": ["Python", "FastAPI", "OpenAI API", "PostgreSQL", "Azure", "Vector Search"],
    },
    "engineer": {
        "name": "BuildReady",
        "email_domain": "buildready.example",
        "client": "Summit Grid Works",
        "industry": "Renewable Infrastructure",
        "title": "Field Controls Engineer",
        "location": "Phoenix, AZ",
        "skills": ["PLC", "SCADA", "Commissioning", "Primavera P6", "Safety", "QA/QC"],
    },
    "law": {
        "name": "LegalReady",
        "email_domain": "legalready.example",
        "client": "Civic Harbor Legal",
        "industry": "Legal Operations",
        "title": "Legal Operations Analyst",
        "location": "Chicago, IL",
        "skills": ["CLM", "DocuSign CLM", "Matter Analytics", "UAT", "Policy Review", "Compliance"],
    },
}

STAGES = [
    {
        "first": "Tom",
        "stage": "A-Z complete",
        "cert_status": "certified",
        "cert_level": "L3",
        "interview_status": "Completed",
        "interview_provider": "Google Calendar",
        "onboarding_status": "paperwork_submitted",
        "time_status": "processed_for_payment",
        "next_action": "Demo complete. Use as the clean end-to-end success story.",
    },
    {
        "first": "Tina",
        "stage": "Pending invite review",
        "cert_status": "started",
        "cert_level": "L1",
        "interview_status": "Draft generated",
        "interview_provider": "Draft",
        "onboarding_status": "hire_started",
        "time_status": "submitted_to_devready",
        "next_action": "Review and send certification plus interview invite.",
    },
    {
        "first": "Taylor",
        "stage": "Invite sent",
        "cert_status": "certified",
        "cert_level": "L1",
        "interview_status": "Calendar invite sent",
        "interview_provider": "Outlook Calendar",
        "onboarding_status": "hire_started",
        "time_status": "needs_review",
        "next_action": "Confirm attendance and review time entry note.",
    },
    {
        "first": "Terry",
        "stage": "Onboarding pending",
        "cert_status": "completed",
        "cert_level": "L2",
        "interview_status": "Client follow-up needed",
        "interview_provider": "Microsoft Teams",
        "onboarding_status": "hire_started",
        "time_status": "approved_for_payment",
        "next_action": "Collect onboarding paperwork and send client follow-up.",
    },
    {
        "first": "Jordan",
        "stage": "Exam retry",
        "cert_status": "failed",
        "cert_level": "L1",
        "interview_status": "Booking link opened",
        "interview_provider": "Calendly",
        "onboarding_status": "hire_started",
        "time_status": "submitted_to_devready",
        "next_action": "Send exam retry link and keep time active.",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(fallback)) else fallback
    except Exception:
        return fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def upsert_list(path: Path, records: list[dict[str, Any]]) -> None:
    current = read_json(path, [])
    by_id = {str(item.get("id")): item for item in current if isinstance(item, dict) and item.get("id")}
    for record in records:
        by_id[str(record["id"])] = record
    write_json(path, list(by_id.values()))


def demo_email(name: str, domain: str, index: int) -> str:
    return f"{slug(name).replace('-', '.')}.demo{index:02d}@seed.{DOMAIN_CONFIG[domain]['email_domain']}"


def get_profile_id_by_email(cur, email: str) -> int | None:
    cur.execute(
        """
        SELECT person.id
        FROM person
        JOIN professional ON person.id = professional.personid
        WHERE LOWER(professional.email) = LOWER(%s)
        ORDER BY person.id DESC
        LIMIT 1
        """,
        (email,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def ensure_profile(cur, domain: str, stage: dict[str, str], index: int) -> int:
    config = DOMAIN_CONFIG[domain]
    full_name = f"{stage['first']} {config['name']}"
    email = demo_email(full_name, domain, index)
    existing = get_profile_id_by_email(cur, email)
    if existing:
        return existing

    first, last = full_name.split(" ", 1)
    city, state, country = split_location(config["location"])
    description = (
        f"Demo lifecycle candidate for {config['name']}. Stage: {stage['stage']}. "
        f"Client context: {config['client']} in {config['industry']}."
    )
    sync_sequence(cur, "person")
    cur.execute(
        "INSERT INTO person (firstname, lastname, leadsource, domain) VALUES (%s, %s, %s, %s) RETURNING id",
        (first, last, 1, domain),
    )
    person_id = int(cur.fetchone()[0])
    sync_sequence(cur, "address")
    cur.execute("INSERT INTO address (personid, city, state, country) VALUES (%s, %s, %s, %s)", (person_id, city, state, country))
    sync_sequence(cur, "professional")
    cur.execute(
        "INSERT INTO professional (personid, email, linkedinurl, maindescription, status, url, title) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            person_id,
            email,
            f"https://www.linkedin.com/in/demo-{slug(full_name)}",
            description,
            1,
            f"demo-{slug(full_name)}-{person_id}",
            config["title"],
        ),
    )
    professional_id = int(cur.fetchone()[0])
    sync_sequence(cur, "professionalprofile")
    cur.execute("INSERT INTO professionalprofile (professionalid) VALUES (%s) RETURNING id", (professional_id,))
    profile_id = int(cur.fetchone()[0])
    sync_sequence(cur, "platformactivity")
    cur.execute(
        "INSERT INTO platformactivity (profileid, step, result, date, notes) VALUES (%s, %s, %s, NOW(), %s)",
        (profile_id, 1, 1, stage["stage"]),
    )
    for skill_index, skill in enumerate(config["skills"], start=1):
        skill_id = resolve_pg_skill(cur, skill)
        sync_sequence(cur, "resumeskill")
        cur.execute("INSERT INTO resumeskill (profileid, skillid) VALUES (%s, %s)", (profile_id, skill_id))
        sync_sequence(cur, "professionalskill")
        cur.execute("INSERT INTO professionalskill (profileid, skillid, years) VALUES (%s, %s, %s)", (profile_id, skill_id, 3 + skill_index))
    for title, level in [(config["industry"], 4), ("Client delivery", 4), (stage["stage"], 5)]:
        sync_sequence(cur, "professionalculturalexperience")
        cur.execute("INSERT INTO professionalculturalexperience (profileid, title, level) VALUES (%s, %s, %s)", (profile_id, title, level))
    sync_sequence(cur, "professionalexperience")
    cur.execute(
        "INSERT INTO professionalexperience (profileid, description, mainrole, companyname, startdate, ispresent) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (profile_id, f"Demo portfolio evidence for {stage['stage']}.", config["title"], config["client"], 2023, True),
    )
    experience_id = int(cur.fetchone()[0])
    for skill in config["skills"][:4]:
        skill_id = resolve_pg_skill(cur, skill)
        cur.execute("INSERT INTO portfolioskill (professionalexperienceid, skillid) VALUES (%s, %s)", (experience_id, skill_id))
    return person_id


def ensure_job(cur, domain: str, stage: dict[str, str], index: int) -> int:
    config = DOMAIN_CONFIG[domain]
    title = f"{config['title']} Demo Role {index:02d}"
    cur.execute(
        "SELECT id FROM jobdescription WHERE domain=%s AND company=%s AND jobtitle=%s ORDER BY id DESC LIMIT 1",
        (domain, config["client"], title),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    sync_sequence(cur, "jobdescription")
    cur.execute(
        "INSERT INTO jobdescription (domain, company, jobtitle, description) VALUES (%s, %s, %s, %s) RETURNING id",
        (
            domain,
            config["client"],
            title,
            f"Demo role for {config['client']}. Stage: {stage['stage']}. Needs {', '.join(config['skills'])}.",
        ),
    )
    job_id = int(cur.fetchone()[0])
    for skill in config["skills"]:
        skill_id = resolve_pg_skill(cur, skill)
        sync_sequence(cur, "jobskills")
        cur.execute("INSERT INTO jobskills (jobid, skillid) VALUES (%s, %s)", (job_id, skill_id))
    return job_id


def seed_profiles_and_jobs() -> list[dict[str, Any]]:
    env = dotenv_values(BACKEND / ".env")
    for key, value in env.items():
        os.environ.setdefault(key, value)
    assert_dev_database()
    refs: list[dict[str, Any]] = []
    with pg_conn() as conn:
        with conn.cursor() as cur:
            for domain in DOMAIN_CONFIG:
                for index, stage in enumerate(STAGES, start=1):
                    profile_id = ensure_profile(cur, domain, stage, index)
                    job_id = ensure_job(cur, domain, stage, index)
                    full_name = f"{stage['first']} {DOMAIN_CONFIG[domain]['name']}"
                    refs.append(
                        {
                            "domain": domain,
                            "index": index,
                            "stage": stage,
                            "profile_id": str(profile_id),
                            "job_id": str(job_id),
                            "name": full_name,
                            "email": demo_email(full_name, domain, index),
                        }
                    )
            conn.commit()
    return refs


def seed_status_records(refs: list[dict[str, Any]]) -> None:
    now = now_iso()
    badges = read_json(DATA_DIR / "profile_badges.json", {})
    onboarding = read_json(DATA_DIR / "onboarding_records.json", {})
    time_entries: list[dict[str, Any]] = []
    workflow_events: list[dict[str, Any]] = []
    interviews: list[dict[str, Any]] = []
    meetings: list[dict[str, Any]] = []
    crm_records: list[dict[str, Any]] = []

    for ref in refs:
        domain = ref["domain"]
        config = DOMAIN_CONFIG[domain]
        stage = ref["stage"]
        profile_id = ref["profile_id"]
        index = ref["index"]
        token = f"ONB-DEMO-{domain.upper()}-{index:02d}"
        week_start = "2026-05-18"
        cert_id = f"DEMO-{domain.upper()}-{index:02d}-{stage['cert_level']}"
        badges[profile_id] = {
            "aiCertification": {
                "certificateId": cert_id,
                "level": stage["cert_level"],
                "notes": f"Demo lifecycle: {stage['stage']}.",
                "score": "92%" if stage["cert_status"] != "failed" else "58%",
                "status": stage["cert_status"],
                "title": f"AI / ML {stage['cert_level']} - Foundations",
                "updatedAt": now,
            },
            "techChallenge": {
                "status": "passed" if stage["cert_status"] != "failed" else "completed",
                "score": "Strong demo evidence",
                "notes": f"Scenario stage: {stage['stage']}",
                "updatedAt": now,
            },
        }
        onboarding_record = {
            "candidate_name": ref["name"],
            "created_at": now,
            "domain": domain,
            "email": ref["email"],
            "profile_id": profile_id,
            "recipient": "Heidi at DevReady",
            "recipient_email": "heidi@devready.io",
            "source_record": {"demo": True, "stage": stage["stage"], "job_id": ref["job_id"]},
            "start_day": "2026-05-20",
            "status": stage["onboarding_status"],
            "title": config["title"],
            "token": token,
            "updated_at": now,
        }
        if stage["onboarding_status"] == "paperwork_submitted":
            onboarding_record.update(
                {
                    "legal_name": ref["name"],
                    "preferred_name": ref["name"].split()[0],
                    "phone": f"555-03{index:02d}",
                    "home_address": f"{100 + index} Demo Lane, {config['location']}",
                    "bank_name": "Demo Credit Union",
                    "account_type": "Checking",
                    "account_last4": f"{2400 + index}"[-4:],
                    "payroll_packet_confirmed": True,
                    "submitted_at": now,
                }
            )
        onboarding[token] = onboarding_record
        workflow_events.append(
            {
                "id": f"EVT-DEMO-{domain.upper()}-{index:02d}",
                "profile_id": profile_id,
                "candidate_name": ref["name"],
                "email": ref["email"],
                "domain": domain,
                "event_type": "demo_lifecycle",
                "status": stage["stage"],
                "notes": stage["next_action"],
                "payload": {"onboarding_token": token, "job_id": ref["job_id"], "cert_status": stage["cert_status"]},
                "created_at": now,
                "updated_at": now,
            }
        )
        interview_id = f"INT-DEMO-{domain.upper()}-{index:02d}"
        meeting_url = f"https://meet.devready.example/demo/{domain}/{index:02d}"
        interviews.append(
            {
                "id": interview_id,
                "archiveType": "interview",
                "archivedAt": now,
                "updatedAt": now,
                "createdAt": now,
                "domain": domain,
                "candidateId": profile_id,
                "candidateName": ref["name"],
                "candidateEmail": ref["email"],
                "interviewType": "client" if index % 2 else "ready",
                "readyPurpose": "client" if index % 2 else "role",
                "role": config["title"],
                "clientCompany": config["client"],
                "clientContactName": f"{config['client']} Sponsor",
                "clientContactEmail": f"sponsor@{slug(config['client'])}.example",
                "candidateInterviewerName": "DevReady Demo Team",
                "candidateInterviewerEmail": "demo@devready.io",
                "attendees": [ref["email"], f"sponsor@{slug(config['client'])}.example"],
                "location": "Microsoft Teams",
                "windowStart": "2026-05-22T10:00",
                "windowEnd": "2026-05-22T11:00",
                "provider": stage["interview_provider"],
                "status": stage["interview_status"],
                "subject": f"{stage['interview_status']} - {ref['name']} - {config['title']}",
                "message": f"Demo scheduling message for {ref['name']} at stage {stage['stage']}.",
                "nextAction": stage["next_action"],
                "eventLink": meeting_url if stage["interview_status"] in {"Calendar invite sent", "Completed"} else "",
            }
        )
        meetings.append(
            {
                "id": f"MTG-DEMO-{domain.upper()}-{index:02d}",
                "domain": domain,
                "profileId": profile_id,
                "candidateId": profile_id,
                "candidateName": ref["name"],
                "clientCompany": config["client"],
                "meetingAt": "2026-05-22T10:00:00Z",
                "meetingUrl": meeting_url,
                "summary": f"Demo meeting for {ref['name']}: {stage['stage']}.",
                "decisions": ["Keep demo workflow moving", stage["next_action"]],
                "actionItems": ["Update status tracker", "Review time entry", "Ask Numa for next focus"],
                "createdAt": now,
                "updatedAt": now,
            }
        )
        crm_records.append(
            {
                "id": f"CRM-DEMO-{domain.upper()}-{index:02d}",
                "domain": domain,
                "createdAt": now,
                "updatedAt": now,
                "customer": config["client"],
                "contact": f"{config['client']} Sponsor",
                "email": f"sponsor@{slug(config['client'])}.example",
                "value": 85000 + index * 25000,
                "owner": "Darrin",
                "lastTouched": (datetime(2026, 5, 15) - timedelta(days=index)).strftime("%Y-%m-%dT%H:%M"),
                "when": (datetime(2026, 5, 15) - timedelta(days=index)).strftime("%Y-%m-%dT%H:%M"),
                "where": "Microsoft Teams",
                "strength": 5 + index,
                "meetingUrl": meeting_url,
                "what": f"{config['industry']} demo relationship for {config['title']}.",
                "why": f"Scenario: {stage['stage']}.",
                "contacts": f"{config['client']} Sponsor, sponsor@{slug(config['client'])}.example",
                "history": f"Linked to {interview_id} and {ref['name']}.",
                "nextStep": stage["next_action"],
            }
        )
        for day in range(5):
            entry_id = f"TIM-DEMO-{domain.upper()}-{index:02d}-{day + 1}"
            entry = {
                "id": entry_id,
                "token": token,
                "profile_id": profile_id,
                "candidate_name": ref["name"],
                "email": ref["email"],
                "domain": domain,
                "week_start": week_start,
                "work_date": (datetime(2026, 5, 18) + timedelta(days=day)).date().isoformat(),
                "hours": [8, 7.5, 8, 6, 4][day],
                "client": config["client"],
                "project": f"{config['name']} demo lifecycle",
                "summary": f"{stage['stage']} time entry for {ref['name']}.",
                "blockers": "Needs manager review." if stage["time_status"] == "needs_review" and day == 2 else "",
                "status": stage["time_status"],
                "recipient": "Heidi at DevReady",
                "recipient_email": "heidi@devready.io",
                "created_at": now,
                "updated_at": now,
            }
            if stage["time_status"] == "processed_for_payment":
                entry.update(
                    {
                        "processed_at": now,
                        "processed_by": "Demo Payroll",
                        "processed_reference": f"PAY-DEMO-{domain.upper()}-{index:02d}",
                        "processed_note": "A-Z demo processed.",
                    }
                )
            time_entries.append(entry)

    write_json(DATA_DIR / "profile_badges.json", badges)
    write_json(DATA_DIR / "onboarding_records.json", onboarding)
    upsert_list(DATA_DIR / "time_entries.json", time_entries)
    upsert_list(DATA_DIR / "workflow_events.json", workflow_events)
    upsert_list(DATA_DIR / "interview_archive.json", interviews)
    upsert_list(DATA_DIR / "meeting_records.json", meetings)
    upsert_list(DATA_DIR / "crm_records.json", crm_records)
    write_json(DATA_DIR / "demo_lifecycle_manifest.json", refs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed named A-Z demo lifecycle scenarios.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    refs = seed_profiles_and_jobs()
    if not args.dry_run:
        seed_status_records(refs)
    summary: dict[str, int] = {}
    for ref in refs:
        summary[ref["domain"]] = summary.get(ref["domain"], 0) + 1
    print(json.dumps({"seeded": summary, "names": [ref["name"] for ref in refs]}, indent=2))


if __name__ == "__main__":
    main()
