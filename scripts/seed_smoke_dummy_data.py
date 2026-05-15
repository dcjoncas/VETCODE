from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from azureUtils.storage import candidates, jobs  # noqa: E402


LOCAL_BASE_URL = "http://127.0.0.1:8000"


DOMAINS = {
    "dev": {
        "label": "Technology",
        "first": "QA Tech",
        "last": "Smoke",
        "title": "AI Platform Integration Engineer",
        "city": "Boulder",
        "state": "Colorado",
        "country": "United States",
        "skills": ["Python", "FastAPI", "PostgreSQL", "OpenAI API", "Vector search", "Azure"],
        "culture": ["AI Delivery", "Cloud Platform Engineering", "Product Automation"],
        "portfolio_company": "QA DevReady Labs",
        "portfolio_role": "AI Platform Integration Engineer",
        "portfolio_summary": "Built a local smoke-test assistant workflow, integrated profile search, and validated domain-safe candidate handoff flows.",
        "job_title": "QA AI Platform Engineer",
        "job_company": "QA DevReady Client",
        "jd": "Build AI workflow services with Python, FastAPI, PostgreSQL, Azure, OpenAI API, prompt engineering, and vector search. Own integration testing and production readiness.",
    },
    "engineer": {
        "label": "Engineering",
        "first": "QA Build",
        "last": "Smoke",
        "title": "Mechanical Systems Validation Engineer",
        "city": "Denver",
        "state": "Colorado",
        "country": "United States",
        "skills": ["Solidworks", "AutoCAD", "GD&T", "FEA (Basic)", "Root Cause Analysis", "Technical Documentation"],
        "culture": ["Mechanical Engineering", "Product Design", "Manufacturing Support"],
        "portfolio_company": "QA BuildReady Systems",
        "portfolio_role": "Mechanical Systems Validation Engineer",
        "portfolio_summary": "Validated mechanical assemblies, documented tolerance issues, and coordinated corrective actions across vendors and shop-floor teams.",
        "job_title": "QA Mechanical Validation Engineer",
        "job_company": "QA BuildReady Client",
        "jd": "Validate mechanical systems using Solidworks, AutoCAD, GD&T, FEA basics, root cause analysis, vendor coordination, and technical documentation.",
    },
    "law": {
        "label": "Law",
        "first": "QA Legal",
        "last": "Smoke",
        "title": "Legal Operations Analyst",
        "city": "Chicago",
        "state": "Illinois",
        "country": "United States",
        "skills": ["Legal Operations", "CLM", "DocuSign CLM", "Matter analytics", "UAT", "Access governance"],
        "culture": ["Legal Operations", "Contract Lifecycle Management", "Compliance Workflow"],
        "portfolio_company": "QA LegalReady Operations",
        "portfolio_role": "Legal Operations Analyst",
        "portfolio_summary": "Configured intake workflows, tested approval routing, and prepared matter analytics for legal and business stakeholders.",
        "job_title": "QA Legal Operations Analyst",
        "job_company": "QA LegalReady Client",
        "jd": "Support legal operations, CLM workflow configuration, DocuSign CLM, matter analytics, UAT, access governance, and compliance reporting.",
    },
}


def post_form(path: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{LOCAL_BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{LOCAL_BASE_URL}{path}", timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def create_profile(domain: str, config: dict, stamp: str) -> dict:
    full_name = f"{config['first']} {config['last']} {stamp}"
    email = f"qa.{domain}.{stamp.lower()}@devready.example"
    skill_rows = [{"title": skill, "years": 4 + (index % 5)} for index, skill in enumerate(config["skills"])]
    portfolio = [
        {
            "companyName": config["portfolio_company"],
            "mainRole": config["portfolio_role"],
            "description": config["portfolio_summary"],
            "startDate": 2022,
            "finishDate": None,
            "isPresent": True,
            "skills": config["skills"],
            "features": ["Smoke test data", "Domain isolation", "Portfolio rendering"],
        }
    ]
    culture = [{"experience": item, "level": 3} for item in config["culture"]]
    description = (
        f"QA dummy profile for {config['label']} domain smoke testing. "
        "This record is intentionally synthetic and safe to delete after validation."
    )
    created = candidates.uploadProfile(
        skills=skill_rows,
        fullName=full_name,
        candidateDescription=description,
        domain=domain,
        email=email,
        linkedInUrl="https://www.linkedin.com/in/qa-smoke-test",
        culturalExperiences=culture,
        candidateCity=config["city"],
        candidateState=config["state"],
        candidateCountry=config["country"],
        candidateTitle=config["title"],
        portfolioExperiences=portfolio,
    )
    created["email"] = email
    created["domain"] = domain
    created["title"] = config["title"]
    return created


def create_job(domain: str, config: dict, stamp: str) -> dict:
    title = f"{config['job_title']} {stamp}"
    company = config["job_company"]
    created = jobs.uploadJob(company, title, domain, config["jd"], config["skills"]) or {}
    return {
        "domain": domain,
        "jd_id": created.get("jd_id"),
        "company": company,
        "title": title,
    }


def create_onboarding_and_time(profile: dict, stamp: str) -> dict:
    onboarding = post_form(
        "/api/onboarding/start",
        {
            "profile_id": profile["personid"],
            "candidate_name": profile["name"],
            "email": profile["email"],
            "title": profile["title"],
            "domain": profile["domain"],
            "start_day": "2026-05-18",
            "source_record_json": json.dumps({"qa_stamp": stamp, "source": "seed_smoke_dummy_data.py"}),
        },
    )
    token = onboarding["record"]["token"]
    entries = [
        {"work_date": "2026-05-18", "hours": 4, "summary": "QA onboarding and domain smoke testing."},
        {"work_date": "2026-05-19", "hours": 3.5, "summary": "Reviewed profile, public link, and domain-specific navigation."},
        {"work_date": "2026-05-20", "hours": 2, "summary": "Verified dummy job and matching workflow data."},
    ]
    time_entry = post_form(
        "/api/time-entry",
        {
            "token": token,
            "profile_id": profile["personid"],
            "candidate_name": profile["name"],
            "email": profile["email"],
            "domain": profile["domain"],
            "week_start": "2026-05-18",
            "client": f"QA {profile['domain']} Client",
            "project": f"QA Smoke {stamp}",
            "entries_json": json.dumps(entries),
        },
    )
    return {
        "token": token,
        "time_entry_link": onboarding["time_entry_link"],
        "time_entries": len(time_entry.get("entries") or []),
    }


def verify_domain_isolation(profile: dict) -> dict:
    profile_id = str(profile["personid"])
    own_domain = profile["domain"]
    own = get_json(f"/api/azure/getProfile/{profile_id}?domain={urllib.parse.quote(own_domain)}")
    checks = {
        "own_domain_profile_ok": bool(own.get("profile")),
        "portfolio_count": len(own.get("portfolioExperience") or []),
        "cross_domain_blocked": {},
    }
    for domain in DOMAINS:
        if domain == own_domain:
            continue
        try:
            get_json(f"/api/azure/getProfile/{profile_id}?domain={urllib.parse.quote(domain)}")
            checks["cross_domain_blocked"][domain] = False
        except Exception:
            checks["cross_domain_blocked"][domain] = True
    return checks


def main() -> None:
    stamp = time.strftime("%Y%m%d%H%M")
    created_profiles = []
    created_jobs = []
    onboarding = []
    verification = {}

    for domain, config in DOMAINS.items():
        profile = create_profile(domain, config, stamp)
        created_profiles.append(profile)
        created_jobs.append(create_job(domain, config, stamp))
        onboarding.append(create_onboarding_and_time(profile, stamp))
        verification[domain] = verify_domain_isolation(profile)

    result = {
        "stamp": stamp,
        "profiles": created_profiles,
        "jobs": created_jobs,
        "onboarding_and_time": onboarding,
        "verification": verification,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
