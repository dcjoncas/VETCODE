from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import storage  # noqa: E402


DOMAIN_DB_PATHS = {
    "dev": BACKEND / "devready.db",
    "engineer": BACKEND / "buildready.db",
    "law": BACKEND / "legalready.db",
}
RESUME_DIR = ROOT / "data" / "sample_resumes"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def profile(profile_id: str, domain: str, name: str, email: str, title: str, location: str, overview: str, skills: dict, experience: list[dict]) -> dict:
    return {
        "meta": {
            "profile_id": profile_id,
            "schema": "devready.profile.v1",
            "domain": domain,
            "created_at": now_iso(),
            "sample": True,
        },
        "contact": {
            "full_name": name,
            "email": email,
            "phone": "555-0100",
            "location": location,
            "linkedin": f"https://www.linkedin.com/in/sample-{name.lower().replace(' ', '-')}",
        },
        "summary": {
            "headline": title,
            "overview": overview,
        },
        "skills": skills,
        "experience": experience,
        "education": [
            {"school": "Sample State University", "degree": "B.S.", "field": "Professional Studies", "year": "2014"}
        ],
        "scores": {
            "overall_technical": {"score": 8, "rationale": "Sample profile with strong domain alignment."},
            "backend": {"score": 6, "rationale": "Relevant systems exposure."},
            "frontend": {"score": 4, "rationale": "Not primary focus."},
            "cloud_devops": {"score": 7, "rationale": "Cloud and delivery workflow exposure."},
            "data": {"score": 7, "rationale": "Comfortable with reporting, controls, and operational data."},
            "functional": {"score": 9, "rationale": "Strong business process and stakeholder experience."},
            "business": {"score": 9, "rationale": "Clear client-facing delivery experience."},
            "testing": {"score": 7, "rationale": "Structured validation and review experience."},
        },
        "debug": {"text_chars": 0, "text_lines": 0},
    }


SAMPLES = [
    profile(
        "SAMPLE-ENG-001",
        "engineer",
        "Riley Morgan",
        "riley.morgan.sample@buildready.example",
        "Controls and Automation Engineer",
        "Denver, CO",
        "Controls engineer focused on PLC upgrades, plant-floor reliability, commissioning, and practical operator adoption.",
        {
            "languages": ["Structured Text", "Python"],
            "frontend": [],
            "backend": ["Industrial APIs", "MES integration"],
            "cloud_devops": ["Azure IoT", "Git"],
            "data": ["OEE analysis", "SCADA trends", "Power BI"],
            "testing": ["FAT", "SAT", "Commissioning"],
            "security": ["OT network segmentation"],
            "other": ["PLC", "SCADA", "Rockwell", "Ignition", "Process controls"],
        },
        [
            {
                "company": "ForgeLine Fabrication",
                "title": "Senior Controls Engineer",
                "start": "2021-03",
                "end": "Present",
                "summary": "Led controls modernization for three production lines, improving uptime and standardizing commissioning playbooks.",
            }
        ],
    ),
    profile(
        "SAMPLE-ENG-002",
        "engineer",
        "Camila Torres",
        "camila.torres.sample@buildready.example",
        "Civil Infrastructure Project Manager",
        "Austin, TX",
        "Infrastructure PM with water, remediation, permitting, stakeholder coordination, and field delivery experience.",
        {
            "languages": [],
            "frontend": [],
            "backend": [],
            "cloud_devops": ["Procore", "MS Project"],
            "data": ["Cost forecasting", "Risk registers"],
            "testing": ["Inspection planning", "QA/QC"],
            "security": [],
            "other": ["Civil engineering", "Water systems", "Permitting", "EPC coordination", "Construction management"],
        },
        [
            {
                "company": "Canyon Water Authority",
                "title": "Infrastructure Project Manager",
                "start": "2019-07",
                "end": "Present",
                "summary": "Managed pump station remediation, design review, contractor coordination, and public-agency reporting.",
            }
        ],
    ),
    profile(
        "SAMPLE-ENG-003",
        "engineer",
        "Devon Price",
        "devon.price.sample@buildready.example",
        "Battery Storage Construction Manager",
        "Phoenix, AZ",
        "Field leader for renewable energy and battery storage projects with safety, quality, schedule recovery, and vendor management strength.",
        {
            "languages": [],
            "frontend": [],
            "backend": [],
            "cloud_devops": ["Primavera P6", "Procore"],
            "data": ["Earned value", "Schedule analytics"],
            "testing": ["Site acceptance", "QA documentation"],
            "security": ["Site safety"],
            "other": ["EPC", "Battery storage", "Renewables", "Construction management", "Quality assurance"],
        },
        [
            {
                "company": "Apex Grid Storage",
                "title": "Construction Manager",
                "start": "2020-01",
                "end": "Present",
                "summary": "Recovered schedule on a 120MW storage site by resequencing subcontractor work and tightening QA gates.",
            }
        ],
    ),
    profile(
        "SAMPLE-LAW-001",
        "law",
        "Avery Caldwell",
        "avery.caldwell.sample@legalready.example",
        "Legal Operations Manager",
        "Chicago, IL",
        "Legal ops leader specializing in intake, contract lifecycle operations, outside counsel spend, and KPI reporting.",
        {
            "languages": [],
            "frontend": [],
            "backend": ["CLM workflow configuration"],
            "cloud_devops": ["Ironclad", "DocuSign CLM", "SharePoint"],
            "data": ["Matter analytics", "Spend dashboards"],
            "testing": ["UAT", "Workflow validation"],
            "security": ["Access governance"],
            "other": ["Legal operations", "CLM", "Outside counsel management", "Process design"],
        },
        [
            {
                "company": "BrightPath Legal Ops",
                "title": "Legal Operations Manager",
                "start": "2018-05",
                "end": "Present",
                "summary": "Redesigned contract intake, approval, and reporting flows across sales, procurement, and legal teams.",
            }
        ],
    ),
    profile(
        "SAMPLE-LAW-002",
        "law",
        "Jordan Ellis",
        "jordan.ellis.sample@legalready.example",
        "Litigation Support Specialist",
        "Washington, DC",
        "Litigation support specialist with evidence review, eDiscovery coordination, privilege workflows, and case-team enablement experience.",
        {
            "languages": [],
            "frontend": [],
            "backend": ["Relativity workflows"],
            "cloud_devops": ["Relativity", "DISCO", "Casepoint"],
            "data": ["Review metrics", "Production logs"],
            "testing": ["QC sampling", "Privilege review"],
            "security": ["Chain of custody", "Confidentiality controls"],
            "other": ["eDiscovery", "Litigation support", "Evidence management", "Privilege logs"],
        },
        [
            {
                "company": "Civic Rights Alliance",
                "title": "Litigation Support Specialist",
                "start": "2020-09",
                "end": "Present",
                "summary": "Built repeatable evidence review workflows and quality checks for multi-party litigation teams.",
            }
        ],
    ),
    profile(
        "SAMPLE-LAW-003",
        "law",
        "Morgan Shah",
        "morgan.shah.sample@legalready.example",
        "AI Policy and Privacy Counsel",
        "San Francisco, CA",
        "Counsel focused on AI governance, privacy reviews, records retention, regulatory risk, and practical business policy rollout.",
        {
            "languages": [],
            "frontend": [],
            "backend": ["Policy control mapping"],
            "cloud_devops": ["OneTrust", "Microsoft Purview"],
            "data": ["Data inventory", "Risk scoring"],
            "testing": ["Policy attestation", "Control review"],
            "security": ["Privacy impact assessment", "Records governance"],
            "other": ["AI governance", "Privacy", "Compliance", "Records retention", "Policy drafting"],
        },
        [
            {
                "company": "Meridian Compliance Group",
                "title": "AI Policy and Privacy Counsel",
                "start": "2017-11",
                "end": "Present",
                "summary": "Created AI usage, privacy review, and records governance frameworks for product and legal stakeholders.",
            }
        ],
    ),
]


def resume_text(sample: dict) -> str:
    contact = sample["contact"]
    summary = sample["summary"]
    skills = sorted({skill for values in sample["skills"].values() for skill in values})
    experience = sample["experience"][0]
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
            f"{experience['title']} | {experience['company']} | {experience['start']} - {experience['end']}",
            experience["summary"],
            "",
            "NOTE",
            "Synthetic sample resume for VETCODE development/testing only.",
        ]
    )


def main() -> None:
    for db_path in DOMAIN_DB_PATHS.values():
        storage.init_db(str(db_path))
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    sample_ids = {sample["meta"]["profile_id"]: sample["meta"]["domain"] for sample in SAMPLES}

    for domain, db_path in DOMAIN_DB_PATHS.items():
        conn = sqlite3.connect(db_path)
        try:
            for profile_id, sample_domain in sample_ids.items():
                if sample_domain != domain:
                    conn.execute("DELETE FROM profiles WHERE profile_id=?", (profile_id,))
            conn.commit()
        finally:
            conn.close()

    for sample in SAMPLES:
        db_path = DOMAIN_DB_PATHS[sample["meta"]["domain"]]
        storage.upsert_profile(str(db_path), sample)
        file_name = f"{sample['meta']['profile_id']}_{sample['contact']['full_name'].replace(' ', '_')}.txt"
        resume_path = RESUME_DIR / file_name
        resume_path.write_text(resume_text(sample), encoding="utf-8")
        manifest.append(
            {
                "profile_id": sample["meta"]["profile_id"],
                "domain": sample["meta"]["domain"],
                "name": sample["contact"]["full_name"],
                "email": sample["contact"]["email"],
                "resume": str(resume_path.relative_to(ROOT)),
                "db": str(db_path.relative_to(ROOT)),
            }
        )

    manifest_path = RESUME_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Seeded {len(SAMPLES)} profiles into domain-specific DBs")
    print(f"Wrote resumes and manifest under {RESUME_DIR}")


if __name__ == "__main__":
    main()
