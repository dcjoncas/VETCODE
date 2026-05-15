from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATA_DIR = BACKEND / "data"
RESUME_DIR = ROOT / "data" / "sample_resumes" / "devready_clients"
sys.path.insert(0, str(BACKEND))

import storage  # noqa: E402


DOMAIN_DB_PATHS = {
    "dev": BACKEND / "devready.db",
    "engineer": BACKEND / "buildready.db",
    "law": BACKEND / "legalready.db",
}

LOCAL_DOMAIN = {"dev": "technology", "engineer": "engineer", "law": "law"}

CLIENT_SETS = {
    "dev": {
        "label": "Technology",
        "email_domain": "devready.example",
        "roles": [
            ("Mara Ellison", "AI Platform Architect", "Northstar Health", "Healthcare AI", "Denver, CO", ["Python", "FastAPI", "PostgreSQL", "OpenAI API", "Azure", "Vector Search"], "Build an AI care coordination workflow with audit-ready model handoffs."),
            ("Theo Grant", "FinTech Data Engineer", "LedgerPeak Bank", "Financial Services", "Charlotte, NC", ["Python", "Spark", "Databricks", "dbt", "Azure Data Factory", "Data Quality"], "Modernize merchant risk pipelines and analytics governance."),
            ("Nina Patel", "Product Automation Lead", "BrightCart Retail", "Retail", "Austin, TX", ["TypeScript", "React", "Node.js", "Prompt Engineering", "API Integration", "Playwright"], "Automate catalog operations and support agent workflows."),
            ("Caleb Brooks", "Cybersecurity Cloud Engineer", "SentinelWorks", "Cybersecurity", "Tampa, FL", ["AWS", "Terraform", "SIEM", "Python", "Zero Trust", "Incident Response"], "Harden a managed detection platform for enterprise clients."),
            ("Iris Monroe", "Logistics Systems Analyst", "RouteForge Logistics", "Supply Chain", "Memphis, TN", ["SQL", "Power BI", "API Integration", "EDI", "Process Mining", "Python"], "Unify shipment exception signals across carrier systems."),
            ("Jules Kim", "EdTech AI Engineer", "LearnLoop", "Education", "Salt Lake City, UT", ["Python", "LLM Evaluation", "FastAPI", "React", "PostgreSQL", "Accessibility"], "Launch a tutor assistant with safe content review loops."),
            ("Ari Santos", "Energy Data Product Manager", "GridSpring Energy", "Energy", "Houston, TX", ["Product Strategy", "SQL", "Azure", "IoT Analytics", "Power BI", "Agile"], "Prioritize analytics for field asset performance and outage prevention."),
            ("Brielle Stone", "GovTech Integration Consultant", "CivicBridge", "Public Sector", "Arlington, VA", ["REST APIs", "SAML", "Python", "PostgreSQL", "Compliance", "UAT"], "Integrate citizen-service portals with legacy case systems."),
            ("Owen Mercer", "Manufacturing Analytics Engineer", "ForgeSight", "Manufacturing", "Milwaukee, WI", ["Python", "SQL", "Snowflake", "OEE", "Power BI", "ETL"], "Turn plant-floor telemetry into executive reliability dashboards."),
            ("Sana Reeves", "Media Workflow Engineer", "FrameCloud Media", "Media", "Los Angeles, CA", ["Node.js", "FFmpeg", "AWS", "Queue Workers", "React", "Observability"], "Scale video processing workflows for live campaign launches."),
        ],
    },
    "engineer": {
        "label": "Engineering",
        "email_domain": "buildready.example",
        "roles": [
            ("Lena Watkins", "Controls Commissioning Engineer", "ForgeLine Fabrication", "Industrial Automation", "Cleveland, OH", ["PLC", "SCADA", "Ignition", "Rockwell", "FAT", "SAT"], "Commission automated line upgrades without production disruption."),
            ("Marcus Vale", "Civil Infrastructure PM", "Canyon Water Authority", "Water Infrastructure", "Phoenix, AZ", ["Civil 3D", "Bluebeam", "Permitting", "QA/QC", "MS Project", "RFI Management"], "Recover schedule on pump station and stormwater improvements."),
            ("Priya Shah", "Battery Storage Construction Manager", "Apex Grid Storage", "Renewables", "Reno, NV", ["EPC", "Battery Storage", "Primavera P6", "Safety", "Commissioning", "Subcontractor Management"], "Lead fast-track BESS field execution and quality gates."),
            ("Damon Pierce", "Aerospace Manufacturing Engineer", "OrbitalWorks", "Aerospace", "Huntsville, AL", ["GD&T", "Lean Manufacturing", "Root Cause Analysis", "CATIA", "AS9100", "NPI"], "Stabilize first-article production and supplier corrective actions."),
            ("Mei Tan", "Semiconductor Facilities Engineer", "NanoFab Systems", "Semiconductor", "Boise, ID", ["Cleanroom", "HVAC", "UPW", "Tool Install", "AutoCAD", "Change Control"], "Coordinate fab utility expansion while maintaining uptime."),
            ("Reed Coleman", "Mining Reliability Engineer", "CopperRidge Mining", "Mining", "Tucson, AZ", ["Reliability", "FMEA", "CMMS", "Vibration Analysis", "Maintenance Planning", "Safety"], "Reduce critical equipment downtime and maintenance backlog."),
            ("Elena Cruz", "Automotive Quality Engineer", "Velocity Motors", "Automotive", "Detroit, MI", ["APQP", "PPAP", "8D", "IATF 16949", "Supplier Quality", "Metrology"], "Improve launch quality and supplier issue closure speed."),
            ("Noah Bennett", "Data Center MEP Coordinator", "CoreVault Data Centers", "Data Centers", "Dallas, TX", ["MEP", "Commissioning", "BIM", "Electrical Systems", "Cooling", "CxA Coordination"], "Coordinate MEP turnover for a hyperscale build."),
            ("Talia Warren", "Municipal Roadway Engineer", "MetroWorks Civil", "Municipal Infrastructure", "Columbus, OH", ["Roadway Design", "Drainage", "Civil 3D", "Permitting", "Public Meetings", "Cost Estimating"], "Deliver roadway and drainage redesign through public review."),
            ("Gabe Lin", "Process Improvement Engineer", "HarborFoods Manufacturing", "Food Manufacturing", "Portland, OR", ["Lean", "Six Sigma", "OEE", "Process Validation", "Packaging Lines", "CAPA"], "Improve packaging throughput while preserving food safety controls."),
        ],
    },
    "law": {
        "label": "Law",
        "email_domain": "legalready.example",
        "roles": [
            ("Avery Caldwell", "Legal Operations Manager", "BrightPath Legal Ops", "Legal Operations", "Chicago, IL", ["CLM", "DocuSign CLM", "Matter Analytics", "Outside Counsel", "Workflow Design", "UAT"], "Clean up contract intake, approval paths, and reporting."),
            ("Jordan Ellis", "Litigation Support Specialist", "Civic Rights Alliance", "Litigation", "Washington, DC", ["eDiscovery", "Relativity", "Privilege Review", "Production Logs", "QC Sampling", "Casepoint"], "Prepare review workflows for multi-party litigation."),
            ("Morgan Shah", "AI Policy and Privacy Counsel", "Meridian Compliance Group", "AI Governance", "San Francisco, CA", ["AI Governance", "Privacy", "OneTrust", "Policy Drafting", "Risk Scoring", "Records Retention"], "Create defensible AI usage and privacy review practices."),
            ("Elise Brennan", "Healthcare Compliance Counsel", "CareBridge Health", "Healthcare", "Boston, MA", ["HIPAA", "Compliance Audits", "Policy Review", "Investigations", "Training", "Risk Assessment"], "Support audit remediation and training rollout."),
            ("Ronan Price", "FinTech Regulatory Analyst", "LedgerPeak Bank", "Financial Services", "New York, NY", ["KYC", "AML", "Regulatory Change", "Controls Testing", "Issue Tracking", "Reporting"], "Track regulatory obligations and testing evidence."),
            ("Simone Park", "SaaS Commercial Contracts Lead", "FrameCloud Media", "SaaS", "Seattle, WA", ["MSA", "SaaS Agreements", "Redlining", "Salesforce", "Ironclad", "Negotiation"], "Speed up sales contract turnaround while reducing fallback risk."),
            ("Drew Kaplan", "Insurance Claims Legal Analyst", "HarborSure Insurance", "Insurance", "Hartford, CT", ["Claims Review", "Coverage Analysis", "Litigation Holds", "Legal Research", "Matter Management", "Excel"], "Prioritize claim files and preserve litigation evidence."),
            ("Nadia Flores", "Government Contracts Specialist", "CivicBridge", "Public Sector", "Arlington, VA", ["FAR", "DFARS", "Proposal Compliance", "Subcontracts", "Audit Support", "Records"], "Prepare compliant subcontract and proposal review packages."),
            ("Miles Chen", "Biotech IP Paralegal", "Genova BioLabs", "Biotech", "San Diego, CA", ["Patent Docketing", "IP Portfolio", "Prior Art", "USPTO", "Document Control", "Inventor Intake"], "Organize invention disclosures and patent docket hygiene."),
            ("Hannah Ortiz", "Employment Law HR Advisor", "PeopleFirst Services", "Workforce", "Minneapolis, MN", ["Employment Law", "Investigations", "Policy Updates", "Training", "ADA", "Employee Relations"], "Support policy rollout and sensitive workplace investigations."),
        ],
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def email_for(name: str, domain: str, target: str, index: int) -> str:
    local = slug(name).replace("-", ".")
    return f"{local}.{target}.{index:02d}@seed.{CLIENT_SETS[domain]['email_domain']}"


def split_location(location: str) -> tuple[str, str, str]:
    if "," in location:
        city, state = [part.strip() for part in location.split(",", 1)]
        return city, state, "United States"
    return location, "", "United States"


def skill_groups(domain: str, skills: list[str]) -> dict[str, list[str]]:
    if domain == "dev":
        return {
            "languages": [item for item in skills if item in {"Python", "TypeScript", "SQL", "Node.js"}],
            "frontend": [item for item in skills if item in {"React", "Accessibility", "Playwright"}],
            "backend": [item for item in skills if item in {"FastAPI", "PostgreSQL", "REST APIs", "API Integration", "Queue Workers"}],
            "cloud_devops": [item for item in skills if item in {"Azure", "AWS", "Terraform", "Observability", "SAML"}],
            "data": [item for item in skills if item not in {"Python", "TypeScript", "Node.js", "React", "FastAPI"}],
            "testing": ["UAT", "Production readiness"],
            "security": [item for item in skills if item in {"Zero Trust", "Incident Response", "Compliance"}],
            "other": skills,
        }
    if domain == "engineer":
        return {
            "languages": [],
            "frontend": [],
            "backend": [],
            "cloud_devops": [item for item in skills if item in {"Primavera P6", "Procore", "MS Project", "BIM"}],
            "data": [item for item in skills if item in {"OEE", "FMEA", "CMMS", "Cost Estimating"}],
            "testing": [item for item in skills if item in {"FAT", "SAT", "Commissioning", "QA/QC", "Metrology", "Process Validation"}],
            "security": ["Safety", "Quality controls"],
            "other": skills,
        }
    return {
        "languages": [],
        "frontend": [],
        "backend": [item for item in skills if item in {"CLM", "Relativity", "Ironclad", "Salesforce"}],
        "cloud_devops": [item for item in skills if item in {"DocuSign CLM", "OneTrust", "Casepoint"}],
        "data": [item for item in skills if item in {"Matter Analytics", "Risk Scoring", "Reporting", "Excel"}],
        "testing": [item for item in skills if item in {"UAT", "Controls Testing", "QC Sampling"}],
        "security": [item for item in skills if item in {"Privacy", "Records Retention", "HIPAA", "Litigation Holds"}],
        "other": skills,
    }


def profile_payload(domain: str, target: str, index: int, row: tuple[str, str, str, str, str, list[str], str], profile_id: str) -> dict[str, Any]:
    name, title, client, industry, location, skills, need = row
    email = email_for(name, domain, target, index)
    city, state, country = split_location(location)
    level = ["L1", "L2", "L3"][index % 3]
    cert_id = f"SEED-{target.upper()}-{domain.upper()}-{index:02d}-CERT"
    return {
        "meta": {
            "profile_id": profile_id,
            "schema": "devready.profile.v1",
            "domain": LOCAL_DOMAIN[domain],
            "created_at": now_iso(),
            "sample": True,
            "seed": "devready_client_examples",
            "seed_target": target,
        },
        "contact": {
            "full_name": name,
            "email": email,
            "phone": f"555-02{index:02d}",
            "location": location,
            "linkedin": f"https://www.linkedin.com/in/seed-{slug(name)}",
        },
        "summary": {
            "headline": title,
            "overview": f"Synthetic DevReady {CLIENT_SETS[domain]['label']} example for {industry}. {name} has client-facing experience helping {client} with: {need}",
        },
        "skills": skill_groups(domain, skills),
        "experience": [
            {
                "company": client,
                "title": title,
                "start": str(2020 + (index % 4)),
                "end": "Present",
                "summary": f"Led {industry.lower()} delivery work focused on {need.lower()}",
            }
        ],
        "education": [{"school": "DevReady Sample University", "degree": "B.S.", "field": "Applied Professional Studies", "year": "2014"}],
        "certifications": [
            {
                "title": f"AI / ML {level} - Foundations",
                "level": level,
                "status": "certified",
                "score": f"{86 + (index % 10)}%",
                "certificate_id": cert_id,
                "earned_at": now_iso(),
            }
        ],
        "scores": {
            "overall_technical": {"score": 8 + (index % 2), "rationale": "Synthetic profile with strong seed-data alignment."},
            "functional": {"score": 9, "rationale": "Role and industry context are complete for demo matching."},
            "business": {"score": 9, "rationale": "Client problem and next action are explicit."},
            "testing": {"score": 8, "rationale": "Seed workflow includes certification, interview, CRM, and time evidence."},
        },
        "debug": {"text_chars": 0, "text_lines": 0},
    }


def resume_text(profile: dict[str, Any]) -> str:
    contact = profile["contact"]
    summary = profile["summary"]
    skills = sorted({skill for values in profile["skills"].values() for skill in values})
    exp = profile["experience"][0]
    cert = profile["certifications"][0]
    return "\n".join(
        [
            contact["full_name"],
            summary["headline"],
            contact["email"],
            contact["location"],
            "",
            "SUMMARY",
            summary["overview"],
            "",
            "SKILLS",
            ", ".join(skills),
            "",
            "EXPERIENCE",
            f"{exp['title']} | {exp['company']} | {exp['start']} - {exp['end']}",
            exp["summary"],
            "",
            "CERTIFICATION",
            f"{cert['title']} | {cert['status']} | {cert['score']} | {cert['certificate_id']}",
            "",
            "NOTE",
            "Synthetic DevReady client example for development and demonstration only.",
        ]
    )


def jd_payload(domain: str, target: str, index: int, row: tuple[str, str, str, str, str, list[str], str]) -> dict[str, Any]:
    _name, title, client, industry, _location, skills, need = row
    jd_id = f"JD-SEED-{target.upper()}-{domain.upper()}-{index:02d}"
    jd_title = f"{title} - {industry} Client"
    text = (
        f"{client} needs a {title} for a {industry.lower()} initiative. "
        f"Primary business need: {need} Required skills include {', '.join(skills[:5])}. "
        "The role requires clear stakeholder communication, practical delivery judgment, documentation, and readiness for DevReady interview review."
    )
    return {
        "jd_id": jd_id,
        "company": client,
        "title": jd_title,
        "domain": LOCAL_DOMAIN[domain],
        "created_at": now_iso(),
        "jd_text": text,
        "skills": {"ai_extracted_skills": skills, "must_have": skills[:4], "industry": industry},
    }


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


def upsert_list(path: Path, records: list[dict[str, Any]], key: str = "id") -> None:
    current = read_json(path, [])
    by_id = {str(item.get(key)): item for item in current if isinstance(item, dict) and item.get(key)}
    for record in records:
        by_id[str(record[key])] = record
    write_json(path, list(by_id.values()))


def upsert_seed_files(target: str, refs: list[dict[str, Any]]) -> None:
    now = now_iso()
    badges = read_json(DATA_DIR / "profile_badges.json", {})
    onboarding = read_json(DATA_DIR / "onboarding_records.json", {})
    time_entries: list[dict[str, Any]] = []
    workflow_events: list[dict[str, Any]] = []
    interviews: list[dict[str, Any]] = []
    meetings: list[dict[str, Any]] = []
    crm: list[dict[str, Any]] = []

    for ref in refs:
        profile_id = str(ref["profile_id"])
        domain = ref["domain"]
        row = ref["row"]
        index = ref["index"]
        name, title, client, industry, location, skills, need = row
        email = email_for(name, domain, target, index)
        level = ["L1", "L2", "L3"][index % 3]
        cert_id = f"SEED-{target.upper()}-{domain.upper()}-{index:02d}-CERT"
        token = f"ONB-SEED-{target.upper()}-{domain.upper()}-{index:02d}"
        week_start = "2026-05-18"
        badges[profile_id] = {
            "aiCertification": {
                "certificateId": cert_id,
                "level": level,
                "notes": f"Seeded successful DevReady certification pass for {CLIENT_SETS[domain]['label']} demo data.",
                "score": f"{86 + (index % 10)}%",
                "status": "certified",
                "title": f"AI / ML {level} - Foundations",
                "updatedAt": now,
            }
        }
        onboarding[token] = {
            "candidate_name": name,
            "created_at": now,
            "domain": domain,
            "email": email,
            "profile_id": profile_id,
            "recipient": "Heidi at DevReady",
            "recipient_email": "heidi@devready.io",
            "source_record": {"seed": "devready_client_examples", "target": target, "client": client},
            "start_day": week_start,
            "status": "hire_started",
            "title": title,
            "token": token,
            "updated_at": now,
        }
        workflow_events.append(
            {
                "id": f"EVT-SEED-{target.upper()}-{domain.upper()}-{index:02d}",
                "profile_id": profile_id,
                "candidate_name": name,
                "email": email,
                "domain": domain,
                "event_type": "hire_onboarding_started",
                "status": "hire_started",
                "notes": "Seeded onboarding link and sample workflow history.",
                "payload": {"onboarding_token": token, "client": client, "industry": industry},
                "created_at": now,
                "updated_at": now,
            }
        )
        for day in range(5):
            work_date = (datetime(2026, 5, 18) + timedelta(days=day)).date().isoformat()
            time_entries.append(
                {
                    "id": f"TIM-SEED-{target.upper()}-{domain.upper()}-{index:02d}-{day + 1}",
                    "token": token,
                    "profile_id": profile_id,
                    "candidate_name": name,
                    "email": email,
                    "domain": domain,
                    "week_start": week_start,
                    "work_date": work_date,
                    "hours": [7.5, 8, 6.5, 8, 5.5][day],
                    "client": client,
                    "project": f"{industry} DevReady placement",
                    "summary": f"Seed time card entry for {client}: {need}",
                    "blockers": "" if day != 2 else "Awaiting client clarification on next milestone.",
                    "recipient": "Heidi at DevReady",
                    "recipient_email": "heidi@devready.io",
                    "status": ["submitted_to_devready", "approved", "processed"][index % 3],
                    "created_at": now,
                    "updated_at": now,
                }
            )
        interview_id = f"INT-SEED-{target.upper()}-{domain.upper()}-{index:02d}"
        meeting_url = f"https://meet.devready.example/{target}/{domain}/{index:02d}"
        interviews.append(
            {
                "id": interview_id,
                "archiveType": "interview",
                "archivedAt": now,
                "updatedAt": now,
                "createdAt": now,
                "domain": domain,
                "candidateId": profile_id,
                "candidateName": name,
                "candidateEmail": email,
                "interviewType": "client",
                "readyPurpose": "client",
                "role": title,
                "clientCompany": client,
                "clientContactName": f"{client} Hiring Team",
                "clientContactEmail": f"hiring@{slug(client)}.example",
                "candidateInterviewerName": "DevReady Interview Team",
                "candidateInterviewerEmail": "interviews@devready.io",
                "attendees": [email, f"hiring@{slug(client)}.example", "interviews@devready.io"],
                "location": "Microsoft Teams",
                "windowStart": "2026-05-21T10:00",
                "windowEnd": "2026-05-21T11:00",
                "subject": f"Client Interview - {title} - {client}",
                "message": f"Seed interview record for {name} with {client}. Focus: {need}",
                "nextAction": "Review interview history and prepare client follow-up.",
                "provider": "Seed",
                "status": "Completed",
            }
        )
        meetings.append(
            {
                "id": f"MTG-SEED-{target.upper()}-{domain.upper()}-{index:02d}",
                "domain": domain,
                "profileId": profile_id,
                "candidateId": profile_id,
                "candidateName": name,
                "clientCompany": client,
                "industry": industry,
                "meetingAt": "2026-05-21T10:00:00Z",
                "meetingUrl": meeting_url,
                "summary": f"Seed meeting summary: {client} needs {title}. Business driver: {need}",
                "decisions": ["Proceed to shortlist review", "Confirm role scorecard", "Prepare next client touch"],
                "actionItems": ["Send candidate profile", "Confirm interview availability", "Log CRM follow-up"],
                "createdAt": now,
                "updatedAt": now,
            }
        )
        crm.append(
            {
                "id": f"CRM-SEED-{target.upper()}-{domain.upper()}-{index:02d}",
                "domain": domain,
                "createdAt": now,
                "updatedAt": now,
                "customer": client,
                "contact": f"{client} Hiring Team",
                "email": f"hiring@{slug(client)}.example",
                "value": 65000 + (index * 18000),
                "owner": ["Darrin", "Heidi", "Alex", "Taylor"][index % 4],
                "lastTouched": (datetime(2026, 5, 15) - timedelta(days=index % 9)).strftime("%Y-%m-%dT%H:%M"),
                "when": (datetime(2026, 5, 15) - timedelta(days=index % 9)).strftime("%Y-%m-%dT%H:%M"),
                "where": "Microsoft Teams",
                "strength": 5 + (index % 5),
                "meetingUrl": meeting_url,
                "what": f"{industry} client needs {title}: {need}",
                "why": "Seeded client card showing industry breadth and concrete business urgency.",
                "contacts": f"{client} Hiring Team, Talent Sponsor, hiring@{slug(client)}.example",
                "history": f"Seeded from interview {interview_id}. Meeting summary and follow-up notes are available.",
                "nextStep": "Ask Numa which deals need attention, then send the next client follow-up.",
            }
        )

    write_json(DATA_DIR / "profile_badges.json", badges)
    write_json(DATA_DIR / "onboarding_records.json", onboarding)
    upsert_list(DATA_DIR / "time_entries.json", time_entries)
    upsert_list(DATA_DIR / "workflow_events.json", workflow_events)
    upsert_list(DATA_DIR / "interview_archive.json", interviews)
    upsert_list(DATA_DIR / "meeting_records.json", meetings)
    upsert_list(DATA_DIR / "crm_records.json", crm)


def seed_local() -> list[dict[str, Any]]:
    for db_path in DOMAIN_DB_PATHS.values():
        storage.init_db(str(db_path))
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    refs: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for domain, config in CLIENT_SETS.items():
        db_path = DOMAIN_DB_PATHS[domain]
        for index, row in enumerate(config["roles"], start=1):
            profile_id = f"SEED-LOCAL-{domain.upper()}-{index:02d}"
            profile = profile_payload(domain, "local", index, row, profile_id)
            storage.upsert_profile(str(db_path), profile)
            jd = jd_payload(domain, "local", index, row)
            storage.upsert_jd(str(db_path), jd["jd_id"], jd["company"], jd["title"], jd["domain"], jd["created_at"], jd["jd_text"], jd["skills"])
            resume_path = RESUME_DIR / f"local_{profile_id}_{slug(row[0])}.txt"
            resume_path.write_text(resume_text(profile), encoding="utf-8")
            refs.append({"target": "local", "domain": domain, "profile_id": profile_id, "index": index, "row": row})
            manifest.append(
                {
                    "target": "local",
                    "domain": domain,
                    "profile_id": profile_id,
                    "name": row[0],
                    "email": email_for(row[0], domain, "local", index),
                    "client": row[2],
                    "resume": str(resume_path.relative_to(ROOT)),
                    "db": str(db_path.relative_to(ROOT)),
                    "jd_id": jd["jd_id"],
                }
            )
    upsert_seed_files("local", refs)
    return manifest


def dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def assert_dev_database() -> None:
    env = dotenv_values(BACKEND / ".env")
    for key, value in env.items():
        os.environ.setdefault(key, value)
    parts = " ".join(
        os.getenv(key, "")
        for key in ("AZURE_DATABASE_HOST", "AZURE_DATABASE_NAME", "AZURE_DATABASE_USER", "RAILWAY_ENVIRONMENT")
    ).lower()
    if "prod" in parts or "production" in parts:
        raise RuntimeError("Refusing to seed because configured database looks like production.")
    if "dev" not in parts and "development" not in parts:
        raise RuntimeError("Refusing to seed remote database because it does not look like a dev database.")


def pg_conn():
    assert_dev_database()
    from azureUtils.storage import client  # noqa: WPS433

    return client.getConnection()


def sync_sequence(cur, table: str, column: str = "id") -> None:
    cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, column))
    row = cur.fetchone()
    sequence_name = row[0] if row else None
    if not sequence_name:
        return
    cur.execute(f"SELECT COALESCE(MAX({column}), 0) FROM {table}")
    max_id = cur.fetchone()[0] or 0
    cur.execute("SELECT setval(%s, %s, %s)", (sequence_name, max_id if max_id else 1, bool(max_id)))


def resolve_pg_skill(cur, title: str) -> int:
    cur.execute("SELECT id FROM skill WHERE LOWER(title) = LOWER(%s) ORDER BY id DESC LIMIT 1", (title,))
    row = cur.fetchone()
    if row:
        return int(row[0])
    sync_sequence(cur, "skill")
    cur.execute(
        "INSERT INTO skill (title, description, type, active) VALUES (%s, %s, %s, %s) RETURNING id",
        (title, title, 1, True),
    )
    return int(cur.fetchone()[0])


def get_pg_profile_by_email(cur, email: str) -> int | None:
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


def seed_pg_profile(row: tuple[str, str, str, str, str, list[str], str], domain: str, index: int) -> int:
    from azureUtils.storage import candidates  # noqa: WPS433

    name, title, client, industry, location, skills, need = row
    email = email_for(name, domain, "devdb", index)
    city, state, country = split_location(location)
    with pg_conn() as conn:
        with conn.cursor() as cur:
            existing = get_pg_profile_by_email(cur, email)
            if existing:
                return existing
    created = candidates.uploadProfile(
        skills=[{"title": skill, "years": 3 + (i % 7)} for i, skill in enumerate(skills)],
        fullName=name,
        candidateDescription=f"Synthetic DevReady development profile for {industry}. Client context: {client}. Need: {need}",
        domain=domain,
        email=email,
        linkedInUrl=f"https://www.linkedin.com/in/seed-{slug(name)}",
        culturalExperiences=[
            {"experience": industry, "level": 3},
            {"experience": "Client Delivery", "level": 4},
            {"experience": "DevReady Seed Data", "level": 5},
        ],
        candidateCity=city,
        candidateState=state,
        candidateCountry=country,
        candidateTitle=title,
        portfolioExperiences=[
            {
                "companyName": client,
                "mainRole": title,
                "description": f"Seed portfolio example: {need}",
                "startDate": 2022,
                "finishDate": None,
                "isPresent": True,
                "skills": skills,
                "features": ["Synthetic development data", "Client workflow", "Interview-ready evidence"],
            }
        ],
    )
    return int(created["personid"])


def seed_pg_job(row: tuple[str, str, str, str, str, list[str], str], domain: str, index: int) -> int:
    _name, title, client, industry, _location, skills, need = row
    job_title = f"{title} - {industry} Client"
    description = (
        f"{client} needs a {title} for a {industry.lower()} initiative. "
        f"Primary business need: {need} Required skills: {', '.join(skills)}."
    )
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM jobdescription
                WHERE domain = %s AND company = %s AND jobtitle = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (domain, client, job_title),
            )
            row_existing = cur.fetchone()
            if row_existing:
                return int(row_existing[0])
            sync_sequence(cur, "jobdescription")
            cur.execute(
                "INSERT INTO jobdescription (domain, company, jobtitle, description) VALUES (%s, %s, %s, %s) RETURNING id",
                (domain, client, job_title, description),
            )
            job_id = int(cur.fetchone()[0])
            for skill in skills:
                skill_id = resolve_pg_skill(cur, skill)
                sync_sequence(cur, "jobskills")
                cur.execute("INSERT INTO jobskills (jobid, skillid) VALUES (%s, %s)", (job_id, skill_id))
            conn.commit()
            return job_id


def seed_dev_db() -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for domain, config in CLIENT_SETS.items():
        for index, row in enumerate(config["roles"], start=1):
            person_id = seed_pg_profile(row, domain, index)
            job_id = seed_pg_job(row, domain, index)
            refs.append({"target": "devdb", "domain": domain, "profile_id": str(person_id), "index": index, "row": row})
            manifest.append(
                {
                    "target": "devdb",
                    "domain": domain,
                    "profile_id": str(person_id),
                    "name": row[0],
                    "email": email_for(row[0], domain, "devdb", index),
                    "client": row[2],
                    "jd_id": str(job_id),
                }
            )
    upsert_seed_files("devdb", refs)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic DevReady client examples.")
    parser.add_argument("--target", choices=["local", "dev-db", "all"], default="local")
    args = parser.parse_args()

    manifest: list[dict[str, Any]] = []
    if args.target in {"local", "all"}:
        manifest.extend(seed_local())
    if args.target in {"dev-db", "all"}:
        manifest.extend(seed_dev_db())

    manifest_path = DATA_DIR / "seed_devready_clients_manifest.json"
    existing = read_json(manifest_path, [])
    key = lambda item: f"{item.get('target')}:{item.get('domain')}:{item.get('email')}"
    merged = {key(item): item for item in existing if isinstance(item, dict)}
    for item in manifest:
        merged[key(item)] = item
    write_json(manifest_path, list(merged.values()))

    summary: dict[str, dict[str, int]] = {}
    for item in manifest:
        summary.setdefault(item["target"], {}).setdefault(item["domain"], 0)
        summary[item["target"]][item["domain"]] += 1
    print(json.dumps({"seeded": summary, "manifest": str(manifest_path.relative_to(ROOT))}, indent=2))


if __name__ == "__main__":
    main()
