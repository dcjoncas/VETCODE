# main.py
def top_matches_from_parts(parts: dict, limit: int = 8):
    """
    Bring the most relevant matched skills to the surface for the UI.
    Order by group weight (languages/backend/frontend/cloud_devops/data/testing/security).
    """
    if not parts:
        return []
    order = ["languages","backend","frontend","cloud_devops","data","testing","security"]
    out = []
    seen = set()
    for g in order:
        for s in (parts.get(g, {}) or {}).get("matched", []) or []:
            if s not in seen:
                out.append(s)
                seen.add(s)
            if len(out) >= limit:
                return out
    return out


from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import os, shutil, traceback, json, base64, hashlib, hmac, re, asyncio
import requests
import xml.etree.ElementTree as ET
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from openAI import pageAgents
from openAI.client import getOpenAPIClient
from azureUtils.storage import candidates, jobs, client as azure_client
from psycopg.types.json import Jsonb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")
DEVMEET_BASE_URL = os.getenv("DEVMEET_BASE_URL", "https://web-production-268c2.up.railway.app").rstrip("/")

# --- v2.5.0 helpers: scorecard + interview questions ---
VERTICAL_KEYWORDS = {
    "SAP / ERP": ["sap", "s/4", "s4hana", "s/4hana", "abap", "fiori", "bw", "hana", "idoc", "mm", "fi", "co", "sd", "pp"],
    "FinTech / Banking": ["bank", "banking", "fintech", "trading", "broker", "payment", "pci", "swift", "aml", "kyc"],
    "Healthcare": ["health", "clinical", "ehr", "emr", "hipaa", "hospital", "patient", "pharma"],
    "Retail / eCommerce": ["retail", "ecommerce", "shopify", "cart", "checkout", "order", "fulfillment"],
    "Telecom / ISP": ["telecom", "isp", "network", "carrier", "routing", "fiber"],
    "Construction": ["construction", "jobsite", "project controls", "cost codes", "subcontractor", "bid"],
    "SaaS / Product": ["saas", "multi-tenant", "subscription", "product", "roadmap"],
}

BUSINESS_SIGNALS = ["stakeholder", "client", "lead", "leadership", "roadmap", "strategy", "governance", "budget", "pmo", "presentation", "mentored", "managed"]
FUNCTIONAL_SIGNALS = ["requirements", "process", "workshop", "fit-to-standard", "fts", "user story", "backlog", "functional", "business process", "acceptance", "uAT", "SIT"]

def _flatten_profile_text(profile: dict) -> str:
    parts = []
    parts.append((profile.get("summary", {}) or {}).get("headline", "") or "")
    parts.append((profile.get("summary", {}) or {}).get("overview", "") or "")
    for ex in (profile.get("experience", []) or []):
        parts.append(ex.get("company","") or "")
        parts.append(ex.get("title","") or "")
        parts.append(ex.get("summary","") or "")
        for b in (ex.get("bullets", []) or []):
            parts.append(str(b))
    for ed in (profile.get("education", []) or []):
        parts.append(ed.get("school","") or "")
        parts.append(ed.get("degree","") or "")
    return " \n".join([p for p in parts if p]).lower()

def infer_vertical(profile: dict) -> dict:
    text = _flatten_profile_text(profile)
    scores = {}
    for vert, kws in VERTICAL_KEYWORDS.items():
        scores[vert] = sum(text.count(k) for k in kws)
    best = max(scores.items(), key=lambda x: x[1]) if scores else ("General", 0)
    if best[1] <= 0:
        return {"primary": "General Technology", "signals": []}
    signals = []
    for k in VERTICAL_KEYWORDS[best[0]]:
        if k in text:
            signals.append(k)
        if len(signals) >= 6:
            break
    return {"primary": best[0], "signals": signals}

def score_business_functional(profile: dict) -> dict:
    text = _flatten_profile_text(profile)
    biz = sum(1 for s in BUSINESS_SIGNALS if s in text)
    func = sum(1 for s in FUNCTIONAL_SIGNALS if s in text)
    # Map signal counts to 0-10 with soft cap
    biz_score = min(10, round(3 + biz * 1.2))
    func_score = min(10, round(3 + func * 1.2))
    return {
        "business": {"score": biz_score, "rationale": "Signals found: " + ", ".join([s for s in BUSINESS_SIGNALS if s in text][:6])},
        "functional": {"score": func_score, "rationale": "Signals found: " + ", ".join([s for s in FUNCTIONAL_SIGNALS if s in text][:6])}
    }

def build_scorecard(profile: dict, jd: dict, match_score: float, breakdown: dict) -> dict:
    # Technical score out of 10: scale the 0-100 match into 0-10
    technical = min(10, round((match_score / 100.0) * 10, 1))
    bf = score_business_functional(profile)
    vertical = infer_vertical(profile)

    # Pros: top matched buckets / skills
    pros = []
    for g in ["languages","backend","frontend","cloud_devops","data","testing"]:
        m = (breakdown.get(g, {}) or {}).get("matched", []) or []
        if m:
            pros.append(f"{g}: " + ", ".join(m[:8]))
    pros = pros[:5]

    # Gaps: top missing across weighted groups
    gaps = []
    for g in ["backend","frontend","cloud_devops","data","testing","languages"]:
        miss = (breakdown.get(g, {}) or {}).get("missing", []) or []
        if miss:
            gaps.append(f"{g}: " + ", ".join(miss[:8]))
    gaps = gaps[:5]

    # Differentiators: profile skills not required by JD (good-to-have)
    diffs = []
    pskills = profile.get("skills", {}) or {}
    jdskills = jd.get("jd_skills", {}) or {}
    for g in ["cloud_devops","testing","security","data","backend","frontend","languages"]:
        ps = set(pskills.get(g, []) or [])
        js = set(jdskills.get(g, []) or [])
        extra = sorted(list(ps - js))
        if extra:
            diffs.append(f"{g}: " + ", ".join(extra[:8]))
    diffs = diffs[:4]

    # Cons: use gaps summary (brief)
    cons = []
    for item in gaps[:3]:
        cons.append("Missing/unclear: " + item)

    return {
        "profile_id": profile.get("meta", {}).get("profile_id",""),
        "candidate": {
            "full_name": profile.get("contact", {}).get("full_name",""),
            "email": profile.get("contact", {}).get("email",""),
            "location": profile.get("contact", {}).get("location",""),
            "headline": profile.get("summary", {}).get("headline","")
        },
        "jd": {
            "jd_id": jd.get("jd_id",""),
            "company": jd.get("company",""),
            "title": jd.get("title","")
        },
        "scores_out_of_10": {
            "technical": {"score": technical, "rationale": "Derived from JD coverage match score."},
            "business": bf["business"],
            "functional": bf["functional"]
        },
        "vertical": vertical,
        "pros": pros,
        "cons": cons,
        "differentiators": diffs,
        "gaps": gaps
    }

def build_interview_questions(profile: dict, jd: dict, breakdown: dict) -> list[str]:
    name = profile.get("contact", {}).get("full_name","the candidate")
    jd_title = jd.get("title","this role")
    # Pick one strong area
    strong = None
    for g in ["backend","frontend","cloud_devops","data","testing","languages"]:
        m = (breakdown.get(g, {}) or {}).get("matched", []) or []
        if len(m) >= 2:
            strong = (g, m[:3])
            break
    # Pick one gap
    gap = None
    for g in ["backend","frontend","cloud_devops","data","testing","languages"]:
        miss = (breakdown.get(g, {}) or {}).get("missing", []) or []
        if miss:
            gap = (g, miss[:3])
            break

    q1 = f"Deep dive: For {jd_title}, walk me through a recent project where {name} used {', '.join((strong[1] if strong else ['a key technology']))}. What design trade-offs did you make and why?"
    q2 = f"Gap check: The JD mentions {', '.join((gap[1] if gap else ['a requirement area']))}. What is your experience with this, and how would you ramp up quickly if needed?"
    q3 = "Collaboration: Describe a time you translated ambiguous requirements into an executable plan (stories, acceptance criteria, risks). How did you align stakeholders and measure success?"
    return [q1, q2, q3]


from resume_ingest import ingest
from deterministic_profile import build_profile_from_text
from jd_match import normalize_jd, match, azureMatch, normalize_all_skills
from profile_schema import new_id, empty_devready_profile
import storage
from renderers import profile_to_html, profile_to_docx, jd_to_html, jd_to_docx, match_report_to_html, match_report_to_docx

VERSION = "v2.8.6"
def _local_db_path(env_name: str, filename: str) -> str:
    configured = os.getenv(env_name, "").strip()
    if configured:
        return configured
    return os.path.join(BASE_DIR, filename)


DOMAIN_DB_PATHS = {
    "dev": _local_db_path("DEVREADY_DB_PATH", "devready.db"),
    "engineer": _local_db_path("BUILDREADY_DB_PATH", "buildready.db"),
    "law": _local_db_path("LEGALREADY_DB_PATH", "legalready.db"),
    "dental": _local_db_path("DENTALREADY_DB_PATH", "dentalready.db"),
}
DB_PATH = DOMAIN_DB_PATHS["dev"]
UPLOAD_DIR = "uploads"
EXPORT_DIR = "exports"
DATA_DIR = "data"
DEMO_FIXTURE_DIR = os.path.join(os.path.dirname(BASE_DIR), "data", "demo_lifecycle_fixtures")
PROFILE_BADGES_PATH = os.path.join(DATA_DIR, "profile_badges.json")
PROFILE_NOTES_PATH = os.path.join(DATA_DIR, "profile_notes.json")
ONBOARDING_RECORDS_PATH = os.path.join(DATA_DIR, "onboarding_records.json")
TIME_ENTRIES_PATH = os.path.join(DATA_DIR, "time_entries.json")
ACCOUNTING_RECORDS_PATH = os.path.join(DATA_DIR, "accounting_records.json")
WORKFLOW_EVENTS_PATH = os.path.join(DATA_DIR, "workflow_events.json")
EGERIA_PROCESS_LOG_PATH = os.path.join(DATA_DIR, "egeria_process_log.json")
AI_TECH_DEBT_ASSESSMENTS_PATH = os.path.join(DATA_DIR, "ai_tech_debt_assessments.json")
AI_TECH_DEBT_LINKS_PATH = os.path.join(DATA_DIR, "ai_tech_debt_links.json")
INTERVIEW_ARCHIVE_PATH = os.path.join(DATA_DIR, "interview_archive.json")
CRM_RECORDS_PATH = os.path.join(DATA_DIR, "crm_records.json")
PROSPECT_REFERENCE_RECORDS_PATH = os.path.join(DATA_DIR, "prospect_reference_records.json")
MEETING_RECORDS_PATH = os.path.join(DATA_DIR, "meeting_records.json")
CALL_INTAKE_RECORDS_PATH = os.path.join(DATA_DIR, "call_intake_records.json")
CALL_INTAKE_SESSIONS_PATH = os.path.join(DATA_DIR, "call_intake_sessions.json")
CALL_INTAKE_ARCHIVE_PATH = os.path.join(DATA_DIR, "call_intake_archive.json")
CALL_INTAKE_DELETED_PATH = os.path.join(DATA_DIR, "call_intake_deleted.json")
CALL_INTAKE_QUESTIONS_PATH = os.path.join(DATA_DIR, "call_intake_questions.json")
ACCESS_USERS_PATH = os.path.join(DATA_DIR, "access_users.json")
ACCESS_CANDIDATES_PATH = os.path.join(DATA_DIR, "access_candidates.json")
CHANNEL_MESSAGES_PATH = os.path.join(DATA_DIR, "channel_messages.json")
CHANNEL_CONVERSATIONS_PATH = os.path.join(DATA_DIR, "channel_conversations.json")
ADMIN_SESSION_TOKENS = {}


def _crm_record_archived(record: dict) -> bool:
    return bool((record or {}).get("archived") or (record or {}).get("archivedAt"))

MENU_ITEMS = [
    {"key": "talent", "label": "Talent", "href": "find-candidate.html"},
    {"key": "find_in", "label": "Find Candidates (In)", "href": "match-role.html"},
    {"key": "find_out", "label": "Find Candidates (Out)", "href": "mine-candidate-external.html"},
    {"key": "profiles", "label": "Profiles", "href": "profile-preview.html"},
    {"key": "job_descriptions", "label": "Job Descriptions", "href": "job-descriptions.html"},
    {"key": "call", "label": "Call", "href": "call.html"},
    {"key": "channels", "label": "Poolside", "href": "channels.html"},
    {"key": "meet", "label": "Meet", "href": "meet.html"},
    {"key": "interviews", "label": "Interviews", "href": "schedule-interview.html?interview=ready"},
    {"key": "onboarding", "label": "Onboarding", "href": "onboarding-admin.html"},
    {"key": "time_link", "label": "Time", "href": "time-admin.html"},
    {"key": "status", "label": "Status", "href": "status-tracker.html"},
    {"key": "crm", "label": "Atlas", "href": "crm.html"},
    {"key": "prospects", "label": "Prospects", "href": "prospect-reference.html"},
    {"key": "ai_tech_debt", "label": "AI Tech Debt", "href": "ai-tech-debt.html"},
    {"key": "reports", "label": "Reports", "href": "reports.html"},
    {"key": "accounting", "label": "Accounting", "href": "accounting.html"},
    {"key": "invoices", "label": "Invoices", "href": "invoices.html"},
    {"key": "test_challenge", "label": "Test Challenge", "href": "test-challenge.html"},
    {"key": "ai_cert", "label": "Certification", "href": "ai-cert.html"},
    {"key": "badges", "label": "View Badges", "href": "badge-catalog.html"},
    {"key": "meridian", "label": "Meridian", "href": "https://meridian-mvp-production.up.railway.app/"},
    {"key": "admin", "label": "Admin", "href": "admin.html"},
    {"key": "agents", "label": "Agents", "href": "agents.html"},
]
DEFAULT_INTERNAL_MENU = [
    "talent",
    "find_in",
    "find_out",
    "profiles",
    "job_descriptions",
    "call",
    "channels",
    "meet",
    "interviews",
    "onboarding",
    "time_link",
    "status",
    "crm",
    "prospects",
    "ai_tech_debt",
    "reports",
    "accounting",
    "invoices",
    "test_challenge",
    "ai_cert",
    "badges",
    "meridian",
    "admin",
    "agents",
]
DEFAULT_CANDIDATE_MENU = ["profiles", "channels", "interviews", "time_link", "status"]
SUPER_MENU = [item["key"] for item in MENU_ITEMS]
DOMAIN_HIDDEN_MENU = {
    "law": {"test_challenge", "ai_cert", "badges"},
    "dental": {"test_challenge", "ai_cert", "badges"},
}

DOMAIN_ALIASES = {
    "dev": {
        "dev",
        "technology",
        "tech",
        "devready",
        "devready technology",
        "devready tech",
        "technology domain",
    },
    "engineer": {
        "engineer",
        "engineering",
        "build",
        "buildready",
        "buildready engineer",
        "buildready engineering",
        "engineer domain",
        "engineering domain",
    },
    "law": {
        "law",
        "legal",
        "legalready",
        "legal ready",
        "legayready",
        "legalready law",
        "legal ready law",
        "law domain",
    },
    "dental": {
        "dental",
        "dentalready",
        "dental ready",
        "dentalready dental",
        "dental ready dental",
        "dental domain",
        "dental assistant",
        "dental assistants",
        "dental hygiene",
        "dental hygienist",
        "hygienist",
    },
}


def _domain_key(domain: str = "dev") -> str:
    value = re.sub(r"[\s_-]+", " ", (domain or "dev").strip().lower())
    if value in {"all", "*"}:
        return "all"
    for canonical, aliases in DOMAIN_ALIASES.items():
        if value in aliases:
            return canonical
    return "dev"


def _storage_domain(domain: str = "dev") -> str:
    key = _domain_key(domain)
    if key == "dev":
        return "technology"
    if key in {"engineer", "law", "dental"}:
        return key
    return ""


def _domain_db_path(domain: str = "dev") -> str:
    return DOMAIN_DB_PATHS.get(_domain_key(domain), DOMAIN_DB_PATHS["dev"])


def _domain_db_items(domain: str = "dev"):
    key = _domain_key(domain)
    if key == "all":
        return list(DOMAIN_DB_PATHS.items())
    return [(key, DOMAIN_DB_PATHS.get(key, DOMAIN_DB_PATHS["dev"]))]


def _devmeet_theme(domain: str = "dev") -> dict:
    key = _domain_key(domain)
    themes = {
        "dev": {
            "primary": "#7fbf3f",
            "primary_2": "#2f7d4b",
            "primary_3": "#eef8e8",
            "primary_rgb": "127, 191, 63",
            "on_primary": "#101722",
        },
        "engineer": {
            "primary": "#2f80ed",
            "primary_2": "#145db2",
            "primary_3": "#e8f2ff",
            "primary_rgb": "47, 128, 237",
            "on_primary": "#ffffff",
        },
        "law": {
            "primary": "#a06b39",
            "primary_2": "#754f2b",
            "primary_3": "#fbf4ea",
            "primary_rgb": "160, 107, 57",
            "on_primary": "#ffffff",
        },
        "dental": {
            "primary": "#111111",
            "primary_2": "#ff5ca8",
            "primary_3": "#fff0f7",
            "primary_rgb": "17, 17, 17",
            "on_primary": "#ffffff",
        },
    }
    return themes.get(key, themes["dev"])


def _devmeet_theme_css(domain: str = "dev") -> str:
    theme = _devmeet_theme(domain)
    return f"""
      :root {{
        --green: {theme["primary"]};
        --green-dark: {theme["primary_2"]};
        --blue: {theme["primary"]};
        --meet-primary: {theme["primary"]};
        --meet-primary-2: {theme["primary_2"]};
        --meet-primary-3: {theme["primary_3"]};
        --meet-primary-rgb: {theme["primary_rgb"]};
        --meet-on-primary: {theme["on_primary"]};
      }}
      .hero-band {{
        background: linear-gradient(135deg, #ffffff 0%, #f5f8fa 58%, rgba(var(--meet-primary-rgb), 0.1) 100%) !important;
      }}
      .eyebrow,
      .hero-metrics strong,
      .empty-state span,
      .meeting-receipt > span,
      .receipt-grid strong,
      .processing-state span:first-child {{
        color: var(--meet-primary-2) !important;
      }}
      .step-strip span {{
        border-color: rgba(var(--meet-primary-rgb), 0.3) !important;
        color: var(--meet-primary-2) !important;
        background: rgba(var(--meet-primary-rgb), 0.09) !important;
      }}
      .step-strip span.current-step {{
        border-color: var(--meet-primary) !important;
        color: var(--meet-primary-2) !important;
        background: rgba(var(--meet-primary-rgb), 0.2) !important;
        box-shadow: 0 0 0 3px rgba(var(--meet-primary-rgb), 0.1) !important;
      }}
      input:focus,
      select:focus,
      textarea:focus {{
        outline-color: rgba(var(--meet-primary-rgb), 0.18) !important;
        border-color: var(--meet-primary) !important;
      }}
      .record-button,
      .generate-all-action {{
        background: var(--meet-primary) !important;
        color: var(--meet-on-primary) !important;
      }}
      .record-button:hover:not(:disabled),
      .generate-all-action:hover:not(:disabled) {{
        background: var(--meet-primary-2) !important;
        color: #fff !important;
      }}
      .saved-item-transcript {{
        background: rgba(var(--meet-primary-rgb), 0.08) !important;
        border-left-color: var(--meet-primary) !important;
      }}
      .saved-item-transcript:hover {{
        background: rgba(var(--meet-primary-rgb), 0.13) !important;
      }}
      .result-doc h2 {{
        border-left-color: var(--meet-primary) !important;
      }}
      .meeting-receipt {{
        background: linear-gradient(180deg, #ffffff, var(--meet-primary-3)) !important;
      }}
      .meeting-receipt > span {{
        border-color: rgba(var(--meet-primary-rgb), 0.3) !important;
        background: rgba(var(--meet-primary-rgb), 0.1) !important;
      }}
      .progress-fill {{
        background: linear-gradient(90deg, var(--meet-primary), var(--meet-primary-2)) !important;
      }}
    """


def _devmeet_rewrite_html(html: str, css: str, domain: str = "dev") -> str:
    themed_css = css + "\n" + _devmeet_theme_css(domain)
    html = html.replace(
        '<link rel="stylesheet" href="/static/styles.css" />',
        f"<style>{themed_css}</style>",
    )
    html = re.sub(r'(["\'`])/api/(?!devmeet/proxy/api/)', r'\1/api/devmeet/proxy/api/', html)
    return html


def _profile_db_path(profile_id: str, domain: str = "") -> str:
    clean_domain = _storage_domain(domain) if domain else None
    for key, db_path in _domain_db_items(domain or "all"):
        expected_domain = clean_domain if clean_domain is not None else _storage_domain(key)
        if storage.get_profile(db_path, profile_id, domain=expected_domain):
            return db_path
    return _domain_db_path(domain or "dev")


def _jd_db_path(jd_id: str, domain: str = "") -> str:
    clean_domain = _storage_domain(domain) if domain else None
    for key, db_path in _domain_db_items(domain or "all"):
        expected_domain = clean_domain if clean_domain is not None else _storage_domain(key)
        if storage.get_jd(db_path, jd_id, domain=expected_domain):
            return db_path
    return _domain_db_path(domain or "dev")


def _read_json_store(path: str, fallback):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, type(fallback)) else fallback
    except Exception:
        traceback.print_exc()
    return fallback


def _write_json_store(path: str, data) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    try:
        os.replace(tmp_path, path)
    except PermissionError:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _read_demo_fixture(filename: str, fallback):
    path = os.path.join(DEMO_FIXTURE_DIR, filename)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, type(fallback)) else fallback
    except Exception:
        traceback.print_exc()
    return fallback


def _read_json_store_with_demo(path: str, fallback):
    data = _read_json_store(path, fallback)
    fixture = _read_demo_fixture(os.path.basename(path), fallback)
    if isinstance(fallback, dict):
        merged = {}
        if isinstance(fixture, dict):
            merged.update(fixture)
        if isinstance(data, dict):
            merged.update(data)
        return merged
    if isinstance(fallback, list):
        merged = {}
        for item in fixture if isinstance(fixture, list) else []:
            if isinstance(item, dict):
                merged[str(item.get("id") or item.get("token") or json.dumps(item, sort_keys=True))] = item
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                merged[str(item.get("id") or item.get("token") or json.dumps(item, sort_keys=True))] = item
        return list(merged.values())
    return data


def _now_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_token(prefix: str = "ONB") -> str:
    return new_id(prefix).replace(" ", "").replace("/", "-")


_BUSINESS_TABLES_READY = False


def _business_data_connection():
    return azure_client.getConnection()


def _ensure_business_data_tables() -> bool:
    global _BUSINESS_TABLES_READY
    if _BUSINESS_TABLES_READY:
        return True
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS atlas_crm_records (
              id TEXT PRIMARY KEY,
              domain TEXT NOT NULL DEFAULT 'dev',
              customer TEXT,
              owner TEXT,
              source_import_batch TEXT,
              source_prospect_id TEXT,
              archived BOOLEAN NOT NULL DEFAULT FALSE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              data JSONB NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atlas_crm_records_domain ON atlas_crm_records(domain)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atlas_crm_records_owner ON atlas_crm_records(owner)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atlas_crm_records_archived ON atlas_crm_records(archived)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_atlas_crm_records_updated ON atlas_crm_records(updated_at DESC)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prospect_reference_records (
              id TEXT PRIMARY KEY,
              domain TEXT NOT NULL DEFAULT 'dev',
              company TEXT,
              domain_name TEXT,
              website TEXT,
              industry TEXT,
              source_import_batch TEXT,
              promoted_crm_id TEXT,
              archived BOOLEAN NOT NULL DEFAULT FALSE,
              search_text TEXT NOT NULL DEFAULT '',
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              data JSONB NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prospect_reference_domain ON prospect_reference_records(domain)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prospect_reference_company ON prospect_reference_records(company)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prospect_reference_industry ON prospect_reference_records(industry)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prospect_reference_archived ON prospect_reference_records(archived)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prospect_reference_search ON prospect_reference_records USING gin(to_tsvector('simple', search_text))")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounting_resources (
              id TEXT PRIMARY KEY,
              domain TEXT NOT NULL DEFAULT 'dev',
              crm_customer_id TEXT,
              client TEXT,
              name TEXT,
              email TEXT,
              status TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              data JSONB NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_resources_domain ON accounting_resources(domain)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_resources_customer ON accounting_resources(crm_customer_id)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounting_invoices (
              id TEXT PRIMARY KEY,
              domain TEXT NOT NULL DEFAULT 'dev',
              crm_customer_id TEXT,
              client TEXT,
              invoice_number TEXT,
              status TEXT,
              invoice_date DATE,
              due_date DATE,
              total NUMERIC(12, 2) NOT NULL DEFAULT 0,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              data JSONB NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_invoices_domain ON accounting_invoices(domain)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_invoices_customer ON accounting_invoices(crm_customer_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_invoices_status ON accounting_invoices(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_invoices_date ON accounting_invoices(invoice_date)")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounting_expenses (
              id TEXT PRIMARY KEY,
              domain TEXT NOT NULL DEFAULT 'dev',
              category TEXT,
              expense_date DATE,
              amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              data JSONB NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_expenses_domain ON accounting_expenses(domain)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_accounting_expenses_date ON accounting_expenses(expense_date)")
        conn.commit()
        _BUSINESS_TABLES_READY = True
        return True
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def _json_record_date(value: str):
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except Exception:
        return None


def _prospect_search_text(record: dict) -> str:
    contacts = record.get("contacts") if isinstance(record.get("contacts"), list) else []
    contact_text = " ".join(
        " ".join([_safe_action_text(contact.get("name"), 180), _safe_action_text(contact.get("email"), 240)])
        for contact in contacts
        if isinstance(contact, dict)
    )
    return " ".join(
        [
            _safe_action_text(record.get("company"), 240),
            _safe_action_text(record.get("domain_name"), 240),
            _safe_action_text(record.get("website"), 240),
            _safe_action_text(record.get("industry"), 240),
            _safe_action_text(record.get("city"), 120),
            _safe_action_text(record.get("state"), 120),
            _safe_action_text(record.get("country"), 120),
            _safe_action_text(record.get("description"), 1600),
            _safe_action_text(record.get("web_technologies"), 1600),
            _safe_action_text(record.get("phone"), 120),
            contact_text,
        ]
    )


def _upsert_atlas_crm_db(record: dict) -> bool:
    if not isinstance(record, dict) or not record.get("id") or not _ensure_business_data_tables():
        return False
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        now = datetime.utcnow()
        created_at = _json_record_date(record.get("createdAt")) or now
        updated_at = _json_record_date(record.get("updatedAt")) or _json_record_date(record.get("salesPortalUpdatedAt")) or now
        cur.execute(
            """
            INSERT INTO atlas_crm_records
              (id, domain, customer, owner, source_import_batch, source_prospect_id, archived, created_at, updated_at, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              domain = EXCLUDED.domain,
              customer = EXCLUDED.customer,
              owner = EXCLUDED.owner,
              source_import_batch = EXCLUDED.source_import_batch,
              source_prospect_id = EXCLUDED.source_prospect_id,
              archived = EXCLUDED.archived,
              updated_at = EXCLUDED.updated_at,
              data = EXCLUDED.data
            """,
            (
                _safe_action_text(record.get("id"), 180),
                _domain_key(record.get("domain", "dev")),
                _safe_action_text(record.get("customer"), 240),
                _safe_action_text(record.get("owner"), 160),
                _safe_action_text(record.get("sourceImportBatch") or record.get("source_import_batch"), 180),
                _safe_action_text(record.get("sourceProspectId"), 180),
                _crm_record_archived(record),
                created_at,
                updated_at,
                Jsonb(record),
            ),
        )
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def _seed_dental_atlas_demo_records() -> None:
    fixture = _read_demo_fixture(os.path.basename(CRM_RECORDS_PATH), [])
    samples = [
        item for item in fixture
        if isinstance(item, dict)
        and _domain_key(item.get("domain", "")) == "dental"
        and str(item.get("id", "")).startswith("CRM-DEMO-DENTAL-")
    ]
    if not samples or not _ensure_business_data_tables():
        return
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        for record in samples:
            clean_id = _safe_action_text(record.get("id"), 120)
            if not clean_id:
                continue
            cur.execute("SELECT 1 FROM atlas_crm_records WHERE id = %s LIMIT 1", (clean_id,))
            if cur.fetchone():
                continue
            cur.execute(
                """
                INSERT INTO atlas_crm_records
                  (id, domain, customer, owner, source_import_batch, source_prospect_id, archived, created_at, updated_at, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()), COALESCE(%s::timestamptz, NOW()), %s)
                """,
                (
                    clean_id,
                    "dental",
                    _safe_action_text(record.get("customer"), 240),
                    _safe_action_text(record.get("owner"), 120),
                    "dentalready-sample-atlas-clients-20260813",
                    "",
                    False,
                    record.get("createdAt") or _now_utc(),
                    record.get("updatedAt") or _now_utc(),
                    Jsonb(record),
                ),
            )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
    finally:
        if conn:
            conn.close()


def _upsert_prospect_reference_db(record: dict) -> bool:
    if not isinstance(record, dict) or not record.get("id") or not _ensure_business_data_tables():
        return False
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        now = datetime.utcnow()
        created_at = _json_record_date(record.get("importedAt") or record.get("create_date")) or now
        updated_at = _json_record_date(record.get("updatedAt")) or now
        cur.execute(
            """
            INSERT INTO prospect_reference_records
              (id, domain, company, domain_name, website, industry, source_import_batch, promoted_crm_id, archived, search_text, created_at, updated_at, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              domain = EXCLUDED.domain,
              company = EXCLUDED.company,
              domain_name = EXCLUDED.domain_name,
              website = EXCLUDED.website,
              industry = EXCLUDED.industry,
              source_import_batch = EXCLUDED.source_import_batch,
              promoted_crm_id = EXCLUDED.promoted_crm_id,
              archived = EXCLUDED.archived,
              search_text = EXCLUDED.search_text,
              updated_at = EXCLUDED.updated_at,
              data = EXCLUDED.data
            """,
            (
                _safe_action_text(record.get("id"), 180),
                _domain_key(record.get("domain", "dev")),
                _safe_action_text(record.get("company"), 240),
                _safe_action_text(record.get("domain_name"), 240),
                _safe_action_text(record.get("website"), 240),
                _safe_action_text(record.get("industry"), 240),
                _safe_action_text(record.get("source_import_batch") or record.get("sourceImportBatch"), 180),
                _safe_action_text(record.get("promotedCrmId"), 180),
                bool(record.get("archived") or record.get("archivedAt")),
                _prospect_search_text(record),
                created_at,
                updated_at,
                Jsonb(record),
            ),
        )
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def _atlas_crm_records_db(domain: str = "dev", include_archived: bool = False, limit: int = 500):
    if not _ensure_business_data_tables():
        return None
    conn = None
    try:
        clean_domain = _domain_key(domain)
        clauses = []
        params = []
        if clean_domain != "all":
            clauses.append("domain = %s")
            params.append(clean_domain)
        if not include_archived:
            clauses.append("archived = FALSE")
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 2000)))
        conn = _business_data_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT data FROM atlas_crm_records{where_sql} ORDER BY updated_at DESC LIMIT %s", tuple(params))
        return [row[0] for row in cur.fetchall() if isinstance(row[0], dict)]
    except Exception:
        traceback.print_exc()
        return None
    finally:
        if conn:
            conn.close()


def _prospect_reference_rows_db(domain: str = "dev", q: str = "", industry: str = "", limit: int = 500, offset: int = 0):
    if not _ensure_business_data_tables():
        return None
    conn = None
    try:
        clean_domain = _domain_key(domain)
        clean_query = _safe_action_text(q, 240).lower()
        clean_industry = _safe_action_text(industry, 180).lower()
        clauses = ["archived = FALSE"]
        params = []
        if clean_domain != "all":
            clauses.append("domain = %s")
            params.append(clean_domain)
        if clean_query:
            clauses.append("LOWER(search_text) LIKE %s")
            params.append(f"%{clean_query}%")
        if clean_industry:
            clauses.append("LOWER(COALESCE(industry, '')) LIKE %s")
            params.append(f"%{clean_industry}%")
        where_sql = " WHERE " + " AND ".join(clauses)
        safe_limit = max(1, min(limit, 500))
        safe_offset = max(0, offset)
        conn = _business_data_connection()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM prospect_reference_records{where_sql}", tuple(params))
        count = cur.fetchone()[0] or 0
        cur.execute(
            f"SELECT data FROM prospect_reference_records{where_sql} ORDER BY LOWER(COALESCE(company, '')) LIMIT %s OFFSET %s",
            tuple(params + [safe_limit, safe_offset]),
        )
        return [row[0] for row in cur.fetchall() if isinstance(row[0], dict)], count
    except Exception:
        traceback.print_exc()
        return None
    finally:
        if conn:
            conn.close()


def _prospect_reference_by_id_db(prospect_id: str):
    if not _ensure_business_data_tables():
        return None
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        cur.execute("SELECT data FROM prospect_reference_records WHERE id = %s LIMIT 1", (_safe_action_text(prospect_id, 180),))
        row = cur.fetchone()
        return row[0] if row and isinstance(row[0], dict) else None
    except Exception:
        traceback.print_exc()
        return None
    finally:
        if conn:
            conn.close()


def _db_date(value: str):
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except Exception:
        return None


def _upsert_accounting_resource_db(record: dict) -> bool:
    if not isinstance(record, dict) or not record.get("id") or not _ensure_business_data_tables():
        return False
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        now = datetime.utcnow()
        cur.execute(
            """
            INSERT INTO accounting_resources
              (id, domain, crm_customer_id, client, name, email, status, created_at, updated_at, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              domain = EXCLUDED.domain,
              crm_customer_id = EXCLUDED.crm_customer_id,
              client = EXCLUDED.client,
              name = EXCLUDED.name,
              email = EXCLUDED.email,
              status = EXCLUDED.status,
              updated_at = EXCLUDED.updated_at,
              data = EXCLUDED.data
            """,
            (
                _safe_action_text(record.get("id"), 180),
                _domain_key(record.get("domain", "dev")),
                _safe_action_text(record.get("crm_customer_id"), 180),
                _safe_action_text(record.get("client"), 240),
                _safe_action_text(record.get("name"), 240),
                _safe_action_text(record.get("email"), 240),
                _safe_action_text(record.get("status"), 80),
                _json_record_date(record.get("created_at")) or now,
                _json_record_date(record.get("updated_at")) or now,
                Jsonb(record),
            ),
        )
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def _upsert_accounting_invoice_db(record: dict) -> bool:
    if not isinstance(record, dict) or not record.get("id") or not _ensure_business_data_tables():
        return False
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        now = datetime.utcnow()
        cur.execute(
            """
            INSERT INTO accounting_invoices
              (id, domain, crm_customer_id, client, invoice_number, status, invoice_date, due_date, total, created_at, updated_at, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              domain = EXCLUDED.domain,
              crm_customer_id = EXCLUDED.crm_customer_id,
              client = EXCLUDED.client,
              invoice_number = EXCLUDED.invoice_number,
              status = EXCLUDED.status,
              invoice_date = EXCLUDED.invoice_date,
              due_date = EXCLUDED.due_date,
              total = EXCLUDED.total,
              updated_at = EXCLUDED.updated_at,
              data = EXCLUDED.data
            """,
            (
                _safe_action_text(record.get("id"), 180),
                _domain_key(record.get("domain", "dev")),
                _safe_action_text(record.get("crm_customer_id"), 180),
                _safe_action_text(record.get("client"), 240),
                _safe_action_text(record.get("invoice_number"), 120),
                _safe_action_text(record.get("status"), 80),
                _db_date(record.get("invoice_date")),
                _db_date(record.get("due_date")),
                _money_float(record.get("total")),
                _json_record_date(record.get("created_at")) or now,
                _json_record_date(record.get("updated_at")) or now,
                Jsonb(record),
            ),
        )
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def _upsert_accounting_expense_db(record: dict) -> bool:
    if not isinstance(record, dict) or not record.get("id") or not _ensure_business_data_tables():
        return False
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        now = datetime.utcnow()
        cur.execute(
            """
            INSERT INTO accounting_expenses
              (id, domain, category, expense_date, amount, created_at, updated_at, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              domain = EXCLUDED.domain,
              category = EXCLUDED.category,
              expense_date = EXCLUDED.expense_date,
              amount = EXCLUDED.amount,
              updated_at = EXCLUDED.updated_at,
              data = EXCLUDED.data
            """,
            (
                _safe_action_text(record.get("id"), 180),
                _domain_key(record.get("domain", "dev")),
                _safe_action_text(record.get("category"), 160),
                _db_date(record.get("date")),
                _money_float(record.get("amount")),
                _json_record_date(record.get("created_at")) or now,
                _json_record_date(record.get("updated_at")) or now,
                Jsonb(record),
            ),
        )
        conn.commit()
        return True
    except Exception:
        if conn:
            conn.rollback()
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


def _accounting_store_db():
    if not _ensure_business_data_tables():
        return None
    conn = None
    try:
        conn = _business_data_connection()
        cur = conn.cursor()
        store = {}
        for key, table in [
            ("resources", "accounting_resources"),
            ("invoices", "accounting_invoices"),
            ("expenses", "accounting_expenses"),
        ]:
            cur.execute(f"SELECT data FROM {table} ORDER BY updated_at DESC")
            store[key] = [row[0] for row in cur.fetchall() if isinstance(row[0], dict)]
        return store
    except Exception:
        traceback.print_exc()
        return None
    finally:
        if conn:
            conn.close()


def _read_profile_notes_store() -> dict:
    data = _read_json_store(PROFILE_NOTES_PATH, {"profiles": {}, "links": {}})
    data.setdefault("profiles", {})
    data.setdefault("links", {})
    return data


def _write_profile_notes_store(data: dict) -> None:
    data.setdefault("profiles", {})
    data.setdefault("links", {})
    _write_json_store(PROFILE_NOTES_PATH, data)


def _profile_notes_key(profile_id: str, domain: str = "dev") -> str:
    return f"{_domain_key(domain)}:{str(profile_id or '').strip()}"


def _trim_note_text(value: str, limit: int = 5000) -> str:
    return re.sub(r"\s+\n", "\n", str(value or "").strip())[:limit]


def _profile_notes_record(data: dict, profile_id: str, domain: str = "dev") -> dict:
    key = _profile_notes_key(profile_id, domain)
    record = data.setdefault("profiles", {}).setdefault(
        key,
        {
            "profile_id": str(profile_id or "").strip(),
            "domain": _domain_key(domain),
            "notes": [],
        },
    )
    record["profile_id"] = str(profile_id or "").strip()
    record["domain"] = _domain_key(domain)
    record.setdefault("notes", [])
    return record


def _normalize_user_key(value: str) -> str:
    return (value or "").strip().lower()


def _password_hash(password: str, salt: str = "") -> str:
    password = password or ""
    if not salt:
        salt = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 150000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')}"


def _verify_password(password: str, stored_hash: str = "") -> bool:
    try:
        scheme, salt, expected = (stored_hash or "").split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        actual = _password_hash(password, salt).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _default_menu_for_user(role: str, email: str = "") -> list[str]:
    if role == "channel_guest":
        return ["channels"]
    if role == "candidate":
        return DEFAULT_CANDIDATE_MENU
    if role == "sales":
        return [key for key in ["crm", "prospects", "call", "meet", "reports"] if key in {item["key"] for item in MENU_ITEMS}]
    if role == "admin":
        return SUPER_MENU
    if role == "super_user" or _normalize_user_key(email).endswith("@devready.io"):
        return SUPER_MENU
    return DEFAULT_INTERNAL_MENU


def _domain_menu_keys(keys: list[str], domain: str = "dev") -> list[str]:
    hidden = DOMAIN_HIDDEN_MENU.get(_domain_key(domain), set())
    return [key for key in keys if key not in hidden]


def _domain_menu_items(domain: str = "dev") -> list[dict]:
    hidden = DOMAIN_HIDDEN_MENU.get(_domain_key(domain), set())
    return [item for item in MENU_ITEMS if item.get("key") not in hidden]


def _seed_access_users() -> dict:
    users = _read_json_store(ACCESS_USERS_PATH, {})
    now = _now_utc()
    email = os.getenv("DEVREADY_ADMIN_EMAIL", "Darrin.Joncas@gmail.com")
    username = os.getenv("DEVREADY_ADMIN_USERNAME", "DJ")
    password = os.getenv("DEVREADY_ADMIN_PASSWORD", "DevReady2026!")
    changed = False

    def ensure_super_user(stable_id: str, account_username: str, display_name: str, account_email: str, account_password: str):
        nonlocal changed
        existing_id = ""
        for candidate_id, user in users.items():
            if (
                _normalize_user_key(user.get("username", "")) == _normalize_user_key(account_username)
                or (account_email and _normalize_user_key(user.get("email", "")) == _normalize_user_key(account_email))
            ):
                existing_id = candidate_id
                break

        user_id = existing_id or stable_id
        existing = users.get(user_id, {})
        desired = {
            **existing,
            "id": user_id,
            "username": account_username,
            "display_name": display_name,
            "email": account_email,
            "role": "super_user",
            "status": "active",
            "allowed_menu": SUPER_MENU,
            "password_hash": _password_hash(account_password),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        if users.get(user_id) != desired:
            users[user_id] = desired
            changed = True

    ensure_super_user("USR-ADMINISTRATOR", "Administrator", "Administrator", "", "Red12345##")

    if not users:
        ensure_super_user(_safe_token("USR"), username, "Darrin Joncas", email, password)
    elif not _find_access_user(users, username=username, email=email):
        ensure_super_user(_safe_token("USR"), username, "Darrin Joncas", email, password)

    if changed:
        _write_json_store(ACCESS_USERS_PATH, users)
    return users


def _public_user(user: dict) -> dict:
    return {
        "id": user.get("id", ""),
        "username": user.get("username", ""),
        "display_name": user.get("display_name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", "internal"),
        "status": user.get("status", "active"),
        "allowed_menu": user.get("allowed_menu", []),
        "domain": _domain_key(user.get("domain", "dev")),
        "profile_id": user.get("profile_id", ""),
        "candidate_profile_id": user.get("candidate_profile_id", user.get("profile_id", "")),
        "created_at": user.get("created_at", ""),
        "updated_at": user.get("updated_at", ""),
    }


def _sales_owner_user(user: dict) -> dict:
    display_name = user.get("display_name") or user.get("username") or user.get("email") or ""
    return {
        "id": user.get("id", ""),
        "name": display_name,
        "email": user.get("email", ""),
        "username": user.get("username", ""),
        "role": user.get("role", "internal"),
        "status": user.get("status", "active"),
        "domain": _domain_key(user.get("domain", "dev")),
    }


def _candidate_profile_for_login(email: str = "", username: str = "", domain: str = "dev") -> dict:
    lookup = (email or username or "").strip()
    if not lookup:
        return {}
    clean_domain = _storage_domain(_domain_key(domain))
    try:
        rows = candidates.searchCandidatesByNameEmail(lookup, limit=8, domain=clean_domain)
    except Exception:
        return {}
    target_email = _normalize_user_key(email or lookup)
    for row in rows or []:
        row_email = _normalize_user_key(row.get("email", ""))
        if target_email and row_email == target_email:
            return row
    return rows[0] if rows else {}


def _refresh_candidate_user_link(user: dict, domain: str = "dev") -> dict:
    if user.get("role") != "candidate":
        return user
    clean_domain = _domain_key(domain or user.get("domain", "dev"))
    user["domain"] = clean_domain
    if not user.get("profile_id"):
        profile = _candidate_profile_for_login(user.get("email", ""), user.get("username", ""), clean_domain)
        if profile.get("id"):
            user["profile_id"] = str(profile.get("id"))
            user["candidate_profile_id"] = str(profile.get("id"))
    user["allowed_menu"] = [key for key in user.get("allowed_menu", DEFAULT_CANDIDATE_MENU) if key in DEFAULT_CANDIDATE_MENU]
    if not user["allowed_menu"]:
        user["allowed_menu"] = list(DEFAULT_CANDIDATE_MENU)
    return user


def _administrator_user(users: dict, username: str = "Administrator") -> dict | None:
    user = _find_access_user(users, username=username, email="")
    if user and _normalize_user_key(user.get("username", "")) == "administrator":
        return user
    return None


def _create_admin_token(user: dict) -> str:
    token_seed = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    token = hashlib.sha256(f"{token_seed}:{datetime.utcnow().isoformat()}".encode("utf-8")).hexdigest()
    ADMIN_SESSION_TOKENS[token] = {
        "user_id": user.get("id", ""),
        "username": user.get("username", ""),
        "created_at": _now_utc(),
    }
    return token


def _require_admin_token(token: str):
    token = (token or "").strip()
    session = ADMIN_SESSION_TOKENS.get(token)
    if not session or _normalize_user_key(session.get("username", "")) != "administrator":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return session


def _find_access_user(users: dict, username: str = "", email: str = "") -> dict | None:
    username_key = _normalize_user_key(username)
    email_key = _normalize_user_key(email)
    for user in users.values():
        if username_key and _normalize_user_key(user.get("username", "")) == username_key:
            return user
        if email_key and _normalize_user_key(user.get("email", "")) == email_key:
            return user
    return None


def _read_profile_badges() -> dict:
    fixture = _read_demo_fixture(os.path.basename(PROFILE_BADGES_PATH), {})
    try:
        if os.path.exists(PROFILE_BADGES_PATH):
            with open(PROFILE_BADGES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {**fixture, **data}
    except Exception:
        traceback.print_exc()
    return fixture


def _write_profile_badges(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = PROFILE_BADGES_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, PROFILE_BADGES_PATH)


def _normalize_cert_title(level: str, certificate_id: str = "") -> str:
    cleaned_level = (level or "").strip()
    if cleaned_level and cleaned_level.lower() not in {"ai certified", "certified"}:
        return f"{cleaned_level} AI Certification"
    if certificate_id:
        return "AI Certification Earned"
    return "AI Certified"


def _profile_stage_matches_person(record: dict, profile_id: str, email: str, domain: str) -> bool:
    if not isinstance(record, dict):
        return False
    clean_domain = _domain_key(domain)
    record_domain = _domain_key(record.get("domain", clean_domain))
    if clean_domain != "all" and record_domain != clean_domain:
        return False
    if profile_id and str(record.get("profile_id") or record.get("profileId") or record.get("candidateId") or "") == str(profile_id):
        return True
    if email and _normalize_user_key(record.get("email") or record.get("candidate_email") or "") == _normalize_user_key(email):
        return True
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    if profile_id and str(context.get("candidateId") or context.get("selectedProfileId") or "") == str(profile_id):
        return True
    if email and _normalize_user_key(context.get("candidateEmail") or "") == _normalize_user_key(email):
        return True
    return False


def _profile_process_stage_status(profile_id: str, domain: str = "dev") -> dict:
    clean_domain = _domain_key(domain)
    profile_data: dict = {}
    profile_found = False
    actual_domain = clean_domain
    try:
        if profile_id:
            actual_domain = _domain_key(candidates.getCandidateDomain(profile_id) or clean_domain)
            if clean_domain not in {"all", actual_domain}:
                raise HTTPException(status_code=403, detail="Candidate does not belong to this domain.")
            profile_data = candidates.getProfile(profile_id) or {}
            profile_found = True
    except HTTPException:
        raise
    except Exception:
        profile_data = {}

    core_profile = profile_data.get("profile") if isinstance(profile_data.get("profile"), dict) else {}
    email = core_profile.get("email") or ""
    completion = _profile_completion_status_for_onboarding(profile_id, profile_data) if profile_found else {
        "state": "missing",
        "complete": False,
        "hasRegularProfile": False,
        "hasPersonality": False,
        "hasCulture": False,
        "missing": ["profile"],
    }

    badges = _read_profile_badges().get(str(profile_id), {}) if profile_id else {}
    tech = badges.get("techChallenge") if isinstance(badges.get("techChallenge"), dict) else {}
    ai_cert = badges.get("aiCertification") if isinstance(badges.get("aiCertification"), dict) else {}

    onboarding_rows = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {})
    onboarding_values = onboarding_rows.values() if isinstance(onboarding_rows, dict) else onboarding_rows if isinstance(onboarding_rows, list) else []
    onboarding_matches = [
        row for row in onboarding_values
        if _profile_stage_matches_person(row, profile_id, email, actual_domain)
    ]
    onboarding_matches.sort(key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)
    onboarding_record = onboarding_matches[0] if onboarding_matches else {}
    onboarding_status = _safe_action_text(onboarding_record.get("status"), 80).lower()

    workflow_rows = _read_json_store_with_demo(WORKFLOW_EVENTS_PATH, [])
    if not isinstance(workflow_rows, list):
        workflow_rows = []
    workflow_matches = [
        row for row in workflow_rows
        if _profile_stage_matches_person(row, profile_id, email, actual_domain)
    ]
    egeria_rows = _egeria_log_rows()
    egeria_matches = [
        row for row in egeria_rows
        if _profile_stage_matches_person(row, profile_id, email, actual_domain)
    ]

    workflow_blob = " ".join(
        _safe_action_text(value, 500).lower()
        for row in workflow_matches
        for value in [
            row.get("event_type"),
            row.get("status"),
            row.get("notes"),
            json.dumps(row.get("payload") or {}, default=str),
        ]
    )
    egeria_blob = " ".join(
        _safe_action_text(value, 500).lower()
        for row in egeria_matches
        for value in [
            row.get("event_type"),
            row.get("message"),
            json.dumps(row.get("context") or {}, default=str),
            json.dumps(row.get("after") or {}, default=str),
        ]
    )
    activity_blob = " ".join(
        _safe_action_text(value, 500).lower()
        for value in [
            (profile_data.get("platformActivity") or {}).get("step") if isinstance(profile_data.get("platformActivity"), dict) else "",
            core_profile.get("status"),
            workflow_blob,
            egeria_blob,
        ]
    )

    screened_done = bool(completion.get("hasRegularProfile")) and completion.get("state") in {"partial", "complete"}
    screened_current = profile_found and not screened_done
    vetted_done = (
        _safe_action_text(tech.get("status"), 80).lower() in {"passed", "completed"}
        or "candidate_review_complete" in workflow_blob
        or "candidate review complete" in workflow_blob
        or "review complete" in workflow_blob
        or "shortlist" in activity_blob
        or "vetted" in activity_blob
    )
    onboarded_done = onboarding_status in {"paperwork_submitted", "completed", "complete", "onboarded", "active"}
    onboarded_current = bool(onboarding_record) and not onboarded_done
    certified_done = _safe_action_text(ai_cert.get("status"), 80).lower() in {"certified", "completed"}

    raw_stage_signals = [
        {
            "key": "identified",
            "label": "Identified",
            "icon": "fingerprint",
            "done": profile_found,
            "current": not profile_found,
            "detail": "Permanent profile exists in this domain." if profile_found else "Profile has not been loaded into this domain yet.",
        },
        {
            "key": "screened",
            "label": "Screened",
            "icon": "clipboard",
            "done": screened_done,
            "current": screened_current,
            "detail": "Resume/profile screening data exists." if screened_done else f"Screening needs {', '.join(completion.get('missing') or ['profile data'])}.",
        },
        {
            "key": "vetted",
            "label": "Vetted",
            "icon": "shield",
            "done": vetted_done,
            "current": screened_done and not vetted_done,
            "detail": "Candidate has vetting or review evidence." if vetted_done else "Run candidate review or mark the tech challenge/review complete.",
        },
        {
            "key": "onboarded",
            "label": "Onboarded",
            "icon": "briefcase",
            "done": onboarded_done,
            "current": onboarded_current,
            "detail": (
                f"Onboarding record is {onboarding_record.get('status')}."
                if onboarding_record
                else "No onboarding packet has been started for this profile."
            ),
        },
        {
            "key": "certified",
            "label": "Certified",
            "icon": "award",
            "done": certified_done,
            "current": onboarded_done and not certified_done,
            "detail": "AI certification is complete." if certified_done else "AI certification has not been completed.",
        },
    ]

    first_open_index = next((index for index, stage in enumerate(raw_stage_signals) if not stage["done"]), len(raw_stage_signals))
    has_later_progress = [
        any(later["done"] or later["current"] for later in raw_stage_signals[index + 1 :])
        for index, _stage in enumerate(raw_stage_signals)
    ]
    stages = []
    for index, stage in enumerate(raw_stage_signals):
        status = "done" if stage["done"] else "pending"
        if not stage["done"] and (stage["current"] or index == first_open_index):
            status = "current"
        if not stage["done"] and has_later_progress[index]:
            status = "attention"
        stages.append({
            "key": stage["key"],
            "label": stage["label"],
            "icon": stage["icon"],
            "status": status,
            "detail": stage["detail"],
        })

    current_stage = next((stage for stage in stages if stage["status"] in {"current", "attention"}), stages[-1])
    return {
        "ok": True,
        "profile_id": str(profile_id),
        "domain": actual_domain,
        "current_stage": current_stage.get("key"),
        "current_stage_label": current_stage.get("label"),
        "stages": stages,
        "signals": {
            "profileFound": profile_found,
            "completion": completion,
            "techChallenge": tech,
            "aiCertification": ai_cert,
            "onboarding": onboarding_record,
            "workflowEvents": len(workflow_matches),
            "egeriaEvents": len(egeria_matches),
        },
    }


def _ensure_profile_for_certification(
    profile_id: str = "",
    candidate_name: str = "",
    email: str = "",
    title: str = "",
    level: str = "",
    score: str = "",
    certificate_id: str = "",
    domain: str = "dev",
) -> tuple[str, bool]:
    profile_id = (profile_id or "").strip()
    candidate_name = (candidate_name or "").strip()
    email = (email or "").strip()
    title = (title or "").strip() or _normalize_cert_title(level, certificate_id)
    domain = (domain or "dev").strip() or "dev"

    if profile_id:
        try:
            candidate_domain = candidates.getCandidateDomain(profile_id)
            if candidate_domain and candidate_domain != domain:
                raise HTTPException(status_code=403, detail="Candidate does not belong to this domain.")
        except HTTPException:
            raise
        except Exception:
            traceback.print_exc()

    if not profile_id:
        try:
            existing_candidates = candidates.searchCandidatesByNameEmail(email, limit=1, domain=domain) if email else []
            for row in existing_candidates:
                if (row.get("email") or "").strip().lower() == email.lower():
                    return str(row.get("id")), False
            cross_domain_candidates = candidates.searchCandidatesByNameEmail(email, limit=5, domain="all") if email else []
            for row in cross_domain_candidates:
                if (row.get("email") or "").strip().lower() == email.lower():
                    existing_domain = candidates.getCandidateDomain(row.get("id"))
                    if existing_domain and existing_domain != domain:
                        raise HTTPException(
                            status_code=409,
                            detail="This certification email belongs to a profile in another domain. Switch domains or use the matching profile.",
                        )
        except HTTPException:
            raise
        except Exception:
            traceback.print_exc()

        try:
            description = (
                f"AI certification profile. Certificate earned: {level or title}. "
                f"Score: {score or 'Not provided'}. Certificate ID: {certificate_id or 'Not provided'}."
            )
            created_candidate = candidates.uploadProfile(
                skills=[],
                fullName=candidate_name or email or "AI Certified Candidate",
                candidateDescription=description,
                domain=domain,
                email=email,
                linkedInUrl="",
                culturalExperiences=[],
                candidateTitle=title,
            )
            created_id = str(created_candidate.get("personid") or "")
            if created_id:
                return created_id, True
        except Exception:
            traceback.print_exc()

    cert_db_path = _domain_db_path(domain)
    profile = storage.get_profile(_profile_db_path(profile_id, domain), profile_id) if profile_id else None
    if not profile and email:
        profile = storage.get_profile_by_email(cert_db_path, email)
        profile_id = (profile.get("meta", {}) or {}).get("profile_id", "") if profile else profile_id

    created = False
    now = datetime.utcnow().isoformat() + "Z"
    if not profile:
        profile = empty_devready_profile()
        profile.setdefault("meta", {})["profile_id"] = profile_id or new_id("DRP")
        profile["meta"]["domain"] = _storage_domain(domain)
        profile["meta"]["source"] = "ai_certification"
        profile.setdefault("contact", {})["full_name"] = candidate_name or email or "AI Certified Candidate"
        profile["contact"]["email"] = email
        profile.setdefault("summary", {})["headline"] = title
        profile["summary"]["overview"] = (
            f"Profile auto-created from AI certification handoff. Certificate earned: {level or title}."
        )
        created = True
    else:
        profile.setdefault("meta", {})["domain"] = profile.get("meta", {}).get("domain") or _storage_domain(domain)
        profile.setdefault("contact", {})
        if candidate_name and not profile["contact"].get("full_name"):
            profile["contact"]["full_name"] = candidate_name
        if email and not profile["contact"].get("email"):
            profile["contact"]["email"] = email
        profile.setdefault("summary", {})
        if title and not profile["summary"].get("headline"):
            profile["summary"]["headline"] = title

    profile_id = profile.setdefault("meta", {}).get("profile_id") or new_id("DRP")
    profile["meta"]["profile_id"] = profile_id
    profile["meta"]["updated_from_certification_at"] = now
    profile["meta"]["has_ai_certification"] = True

    certification = {
        "title": title,
        "level": level or "AI Certified",
        "score": score,
        "certificate_id": certificate_id,
        "earned_at": now,
        "source": "AICERT by DevReady",
    }
    existing_certs = profile.get("certifications")
    if not isinstance(existing_certs, list):
        existing_certs = []
    existing_certs = [
        cert for cert in existing_certs
        if not (isinstance(cert, dict) and cert.get("certificate_id") and cert.get("certificate_id") == certificate_id)
    ]
    existing_certs.append(certification)
    profile["certifications"] = existing_certs
    storage.upsert_profile(cert_db_path, profile)
    return profile_id, created


from starlette.middleware.base import BaseHTTPMiddleware
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/ui") or request.url.path.endswith(".html") or request.url.path.endswith(".css") or request.url.path.endswith(".js"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app = FastAPI(title="DevReady Vetting", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500", "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(NoCacheMiddleware)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
for _db_path in DOMAIN_DB_PATHS.values():
    storage.init_db(_db_path)


@app.on_event("startup")
def seed_dentalready_atlas_samples_on_startup():
    _seed_dental_atlas_demo_records()

app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


@app.get("/api/devmeet/frame", response_class=HTMLResponse)
def devmeet_frame(domain: str = "dev"):
    try:
        html_response = requests.get(f"{DEVMEET_BASE_URL}/", timeout=20)
        html_response.raise_for_status()
        css_response = requests.get(f"{DEVMEET_BASE_URL}/static/styles.css", timeout=20)
        css_response.raise_for_status()
        html = _devmeet_rewrite_html(html_response.text, css_response.text, domain)
        return HTMLResponse(html)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=f"DevMeet frame could not be loaded: {exc}")


@app.api_route(
    "/api/devmeet/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def devmeet_proxy(path: str, request: Request):
    target = f"{DEVMEET_BASE_URL}/{path.lstrip('/')}"
    excluded_headers = {
        "host",
        "content-length",
        "connection",
        "accept-encoding",
        "origin",
        "referer",
    }
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded_headers
    }
    try:
        proxied = requests.request(
            request.method,
            target,
            params=list(request.query_params.multi_items()),
            data=await request.body(),
            headers=headers,
            timeout=120,
        )
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=f"DevMeet request failed: {exc}")

    response_headers = {}
    for header in ("content-disposition", "cache-control"):
        if header in proxied.headers:
            response_headers[header] = proxied.headers[header]
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        media_type=proxied.headers.get("content-type"),
        headers=response_headers,
    )


@app.get("/api/debug/dbinfo")
def dbinfo():
    try:
        domains = {}
        for key, db_path in DOMAIN_DB_PATHS.items():
            storage_name = _storage_domain(key)
            jds = storage.list_jds(db_path, domain=storage_name)
            profs = storage.list_profiles(db_path, domain=storage_name, limit=1000)
            domains[key] = {
                "db_path": db_path,
                "storage_domain": storage_name,
                "job_descriptions": len(jds),
                "profiles": len(profs),
                "jd_domains": sorted({(x.get("domain") or "") for x in jds}),
                "profile_domains": sorted({(x.get("domain") or "") for x in profs}),
            }
        return {
            "db_paths": DOMAIN_DB_PATHS,
            "domain_databases": domains,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": VERSION}

@app.get("/api/environment")
def environment():
    railway_env = (
        os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or ""
    ).strip()
    public_domain = (
        os.getenv("RAILWAY_PUBLIC_DOMAIN")
        or os.getenv("RAILWAY_SERVICE_VETCODE_URL")
        or os.getenv("RAILWAY_STATIC_URL")
        or ""
    ).strip()
    db_host = (os.getenv("AZURE_DATABASE_HOST") or "").strip()
    db_name = (os.getenv("AZURE_DATABASE_NAME") or "").strip()
    storage_container = (os.getenv("AZURE_STORAGE_CONTAINER_NAME") or "").strip()

    env_source = f"{railway_env} {public_domain} {db_host}".lower()
    if "prod" in env_source and "dev" not in env_source:
        environment_name = "Production"
        source_name = "Production Railway"
        badge_color = "rgba(190, 38, 51, 0.86)"
    elif "dev" in env_source or "development" in env_source:
        environment_name = "Development"
        source_name = "Development Railway"
        badge_color = "rgba(18, 91, 54, 0.9)"
    else:
        environment_name = "Local"
        source_name = "Local source"
        badge_color = "rgba(255, 255, 255, 0.16)"

    return {
        "status": "ok",
        "version": VERSION,
        "environment": environment_name,
        "railway_environment": railway_env or "local",
        "public_domain": public_domain or "localhost",
        "database_name": db_name or "local",
        "database_host": db_host or "local",
        "storage_container": storage_container or "local",
        "source": source_name,
        "badge_color": badge_color,
    }


@app.get("/api/access/menu")
def access_menu(domain: str = "dev"):
    return {
        "items": _domain_menu_items(domain),
        "default_internal_menu": _domain_menu_keys(DEFAULT_INTERNAL_MENU, domain),
        "default_candidate_menu": DEFAULT_CANDIDATE_MENU,
        "super_menu": _domain_menu_keys(SUPER_MENU, domain),
    }


def _channel_key(value: str = "") -> str:
    key = re.sub(r"[^a-z0-9_-]+", "-", str(value or "general").strip().lower()).strip("-")
    return key[:48] or "general"


def _channel_user_name(name: str = "", email: str = "") -> str:
    clean_name = re.sub(r"\s+", " ", str(name or "").strip())
    if clean_name:
        return clean_name[:80]
    clean_email = str(email or "").strip()
    return clean_email[:80] if clean_email else "DevReady User"


def _channel_invite_email(value: str = "", required: bool = True) -> str:
    email = str(value or "").strip().lower()
    if not email and not required:
        return ""
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return email


def _channel_email_name(email: str) -> str:
    local_part = str(email or "").split("@", 1)[0]
    words = re.sub(r"[._+-]+", " ", local_part).strip()
    return words.title()[:80] or email[:80]


def _channel_people(clean_domain: str) -> list[dict]:
    users = _seed_access_users()
    people = []
    seen = set()

    def add_person(name: str = "", email: str = "", role: str = "user", source: str = "user", profile_id: str = ""):
        clean_email = str(email or "").strip()
        key = clean_email.lower() or f"{source}:{profile_id or name}".lower()
        if not key or key in seen:
            return
        seen.add(key)
        people.append({
            "name": _channel_user_name(name, clean_email),
            "email": clean_email,
            "role": role or "user",
            "source": source,
            "profile_id": str(profile_id or ""),
        })

    for user in users.values():
        if user.get("status") == "blocked":
            continue
        add_person(user.get("display_name") or user.get("username"), user.get("email"), user.get("role") or "user", "access")

    try:
        discovery = candidates.profileDiscovery(clean_domain, 5000)
        for profile in discovery.get("profiles", []):
            add_person(profile.get("name"), profile.get("email"), "candidate", "profile", profile.get("id"))
    except Exception as exc:
        print(f"Failed to load channel candidate audience: {exc}")

    return sorted(people, key=lambda row: (row.get("role") != "candidate", row.get("name", "").lower()))


def _channel_participants_from_json(participants_json: str, clean_domain: str) -> list[dict]:
    try:
        raw = json.loads(participants_json or "[]")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Participant emails are invalid.") from exc
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="Participant emails must be a list.")
    people_by_email = {str(row.get("email") or "").strip().lower(): row for row in _channel_people(clean_domain)}
    participants = []
    seen = set()
    for item in raw[:5000]:
        if isinstance(item, dict):
            email = _channel_invite_email(item.get("email") or "")
            name = str(item.get("name") or "").strip()
        else:
            email = _channel_invite_email(item)
            name = ""
        key = email.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        known = people_by_email.get(email.lower(), {})
        participants.append({
            "name": _channel_user_name(name or known.get("name") or _channel_email_name(email), email),
            "email": email,
            "role": known.get("role") or "member",
            "source": known.get("source") or "manual",
            "profile_id": known.get("profile_id") or "",
        })
    return participants


def _egeria_participant() -> dict:
    return {
        "name": "Egeria",
        "email": "egeria@devready.ai",
        "role": "conversation helper",
        "source": "system",
        "profile_id": "",
        "system": True,
    }


def _ensure_egeria_participant(participants: list[dict] | None) -> list[dict]:
    egeria = _egeria_participant()
    normalized = []
    seen = {egeria["email"].lower()}
    for participant in participants or []:
        if not isinstance(participant, dict):
            continue
        key = str(participant.get("email") or participant.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(participant)
    return [egeria, *normalized][:5000]


def _default_channel_conversations(clean_domain: str) -> list[dict]:
    now = _now_utc()
    defaults = [
        ("general", "General", "Company-wide team updates and quick coordination."),
        ("candidates", "Candidates", "Candidate-facing and recruiter coordination."),
        ("interviews", "Interviews", "Interview scheduling, prep, and follow-up."),
        ("jobs", "Jobs", "Role, JD, and shortlist conversations."),
    ]
    return [
        {
            "id": key,
            "domain": clean_domain,
            "title": title,
            "topic": topic,
            "participants": [_egeria_participant()],
            "created_by": "System",
            "created_by_email": "",
            "created_at": now,
            "updated_at": now,
            "kind": "channel",
        }
        for key, title, topic in defaults
    ]


def _read_channel_conversations(clean_domain: str) -> tuple[dict, list[dict]]:
    store = _read_json_store(CHANNEL_CONVERSATIONS_PATH, {})
    conversations = store.get(clean_domain)
    changed = False
    if not isinstance(conversations, list):
        conversations = _default_channel_conversations(clean_domain)
        store[clean_domain] = conversations
        changed = True
    else:
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            before = json.dumps(conversation.get("participants") or [], sort_keys=True)
            conversation["participants"] = _ensure_egeria_participant(
                conversation.get("participants") if isinstance(conversation.get("participants"), list) else []
            )
            after = json.dumps(conversation.get("participants") or [], sort_keys=True)
            if before != after:
                changed = True
    if changed:
        _write_json_store(CHANNEL_CONVERSATIONS_PATH, store)
    return store, conversations


def _channel_conversation_is_archived(conversation: dict | None) -> bool:
    return bool((conversation or {}).get("archived_at"))


def _require_active_channel_conversation(conversation: dict | None):
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if _channel_conversation_is_archived(conversation):
        raise HTTPException(status_code=409, detail="Restore this conversation before making changes.")


def _conversation_message_key(clean_domain: str, conversation_id: str = "", channel: str = "") -> str:
    clean_conversation = _channel_key(conversation_id) if str(conversation_id or "").strip() else ""
    if clean_conversation:
        return f"{clean_domain}:conversation:{clean_conversation}"
    return f"{clean_domain}:{_channel_key(channel)}"


@app.get("/api/channels/conversations")
def channel_conversations(domain: str = "dev"):
    clean_domain = _domain_key(domain)
    _, conversations = _read_channel_conversations(clean_domain)
    return {"ok": True, "domain": clean_domain, "conversations": conversations}


@app.post("/api/channels/conversations")
def create_channel_conversation(
    domain: str = Form(default="dev"),
    title: str = Form(default=""),
    topic: str = Form(default=""),
    participants_json: str = Form(default="[]"),
    created_by_name: str = Form(default=""),
    created_by_email: str = Form(default=""),
):
    clean_domain = _domain_key(domain)
    clean_title = _safe_action_text(title, 120) or "New conversation"
    invited_participants = _channel_participants_from_json(participants_json, clean_domain)
    if not invited_participants:
        raise HTTPException(status_code=400, detail="Add at least one recipient email.")
    creator_email = _channel_invite_email(created_by_email, required=False)
    if creator_email:
        invited_participants.append({
            "name": _channel_user_name(created_by_name or _channel_email_name(creator_email), creator_email),
            "email": creator_email,
            "role": "owner",
            "source": "access",
            "profile_id": "",
        })
    store, conversations = _read_channel_conversations(clean_domain)
    base_id = _channel_key(clean_title)
    existing_ids = {str(row.get("id") or "") for row in conversations}
    conversation_id = base_id
    if conversation_id in existing_ids:
        conversation_id = _channel_key(f"{base_id}-{_safe_token('CVN').lower()}")
    now = _now_utc()
    conversation = {
        "id": conversation_id,
        "domain": clean_domain,
        "title": clean_title,
        "topic": _safe_action_text(topic, 300),
        "participants": _ensure_egeria_participant(invited_participants),
        "created_by": _channel_user_name(created_by_name, created_by_email),
        "created_by_email": str(created_by_email or "").strip()[:160],
        "created_at": now,
        "updated_at": now,
        "kind": "conversation",
    }
    conversations.insert(0, conversation)
    store[clean_domain] = conversations[:250]
    _write_json_store(CHANNEL_CONVERSATIONS_PATH, store)
    return {"ok": True, "conversation": conversation}


@app.post("/api/channels/conversations/{conversation_id}/participants")
def add_channel_conversation_participants(
    conversation_id: str,
    domain: str = Form(default="dev"),
    participants_json: str = Form(default="[]"),
):
    clean_domain = _domain_key(domain)
    clean_id = _channel_key(conversation_id)
    store, conversations = _read_channel_conversations(clean_domain)
    conversation = next((row for row in conversations if str(row.get("id") or "") == clean_id), None)
    _require_active_channel_conversation(conversation)
    existing = conversation.get("participants") if isinstance(conversation.get("participants"), list) else []
    additions = _channel_participants_from_json(participants_json, clean_domain)
    by_key = {}
    for participant in [*existing, *additions]:
        key = str(participant.get("email") or participant.get("name") or "").strip().lower()
        if key:
            by_key[key] = participant
    conversation["participants"] = _ensure_egeria_participant(list(by_key.values()))
    conversation["updated_at"] = _now_utc()
    store[clean_domain] = conversations
    _write_json_store(CHANNEL_CONVERSATIONS_PATH, store)
    return {"ok": True, "conversation": conversation}


@app.delete("/api/channels/conversations/{conversation_id}/participants")
def remove_channel_conversation_participant(
    conversation_id: str,
    domain: str = Form(default="dev"),
    email: str = Form(default=""),
):
    clean_domain = _domain_key(domain)
    clean_id = _channel_key(conversation_id)
    clean_email = _channel_invite_email(email)
    if clean_email == _egeria_participant()["email"]:
        raise HTTPException(status_code=400, detail="Egeria cannot be removed from a conversation.")
    store, conversations = _read_channel_conversations(clean_domain)
    conversation = next((row for row in conversations if str(row.get("id") or "") == clean_id), None)
    _require_active_channel_conversation(conversation)
    existing = conversation.get("participants") if isinstance(conversation.get("participants"), list) else []
    remaining = [
        participant
        for participant in existing
        if participant.get("system")
        or _normalize_user_key(participant.get("email", "")) != clean_email
    ]
    if len(remaining) == len(existing):
        raise HTTPException(status_code=404, detail="That email is not in this conversation.")
    conversation["participants"] = _ensure_egeria_participant(remaining)
    conversation["updated_at"] = _now_utc()
    store[clean_domain] = conversations
    _write_json_store(CHANNEL_CONVERSATIONS_PATH, store)
    return {"ok": True, "conversation": conversation, "removed_email": clean_email}


@app.post("/api/channels/conversations/{conversation_id}/archive")
def archive_channel_conversation(
    conversation_id: str,
    domain: str = Form(default="dev"),
    archived: bool = Form(default=True),
    archived_by_name: str = Form(default=""),
    archived_by_email: str = Form(default=""),
):
    clean_domain = _domain_key(domain)
    clean_id = _channel_key(conversation_id)
    store, conversations = _read_channel_conversations(clean_domain)
    conversation = next((row for row in conversations if str(row.get("id") or "") == clean_id), None)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    was_archived = _channel_conversation_is_archived(conversation)
    now = _now_utc()
    actor_email = _channel_invite_email(archived_by_email, required=False)
    actor_name = _channel_user_name(archived_by_name, actor_email)
    history = conversation.get("archive_history") if isinstance(conversation.get("archive_history"), list) else []
    if archived and not was_archived:
        conversation["archived_at"] = now
        conversation["archived_by"] = actor_name
        conversation["archived_by_email"] = actor_email
        history.append({"action": "archived", "at": now, "by": actor_name, "by_email": actor_email})
    elif not archived and was_archived:
        conversation["restored_at"] = now
        conversation.pop("archived_at", None)
        conversation.pop("archived_by", None)
        conversation.pop("archived_by_email", None)
        history.append({"action": "restored", "at": now, "by": actor_name, "by_email": actor_email})
    conversation["archive_history"] = history[-100:]
    conversation["updated_at"] = now
    store[clean_domain] = conversations
    _write_json_store(CHANNEL_CONVERSATIONS_PATH, store)
    return {
        "ok": True,
        "conversation": conversation,
        "archived": _channel_conversation_is_archived(conversation),
        "messages_preserved": True,
    }


def _channel_viewer_allowed(conversation: dict, viewer_email: str = "", profile_id: str = "") -> bool:
    email_key = _normalize_user_key(viewer_email)
    profile_key = str(profile_id or "").strip()
    participants = conversation.get("participants") if isinstance(conversation.get("participants"), list) else []
    human_participants = [row for row in participants if isinstance(row, dict) and not row.get("system")]
    if not human_participants:
        return False
    for participant in human_participants:
        participant_email = _normalize_user_key(participant.get("email", ""))
        participant_profile = str(participant.get("profile_id") or "").strip()
        if email_key and participant_email and email_key == participant_email:
            return True
        if profile_key and participant_profile and profile_key == participant_profile:
            return True
    return False


@app.get("/api/channels/conversations/{conversation_id}/talent")
@app.get("/api/channels/conversations/{conversation_id}/access")
def channel_conversation_access(
    conversation_id: str,
    domain: str = "dev",
    viewer_email: str = "",
    profile_id: str = "",
):
    clean_domain = _domain_key(domain)
    clean_id = _channel_key(conversation_id)
    _, conversations = _read_channel_conversations(clean_domain)
    conversation = next((row for row in conversations if str(row.get("id") or "") == clean_id), None)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if not _channel_viewer_allowed(conversation, viewer_email, profile_id):
        raise HTTPException(status_code=403, detail="This account is not invited to this conversation.")
    return {"ok": True, "domain": clean_domain, "conversation": conversation}


def _channel_egeria_requested(message: str = "", explicit: bool = False) -> bool:
    if explicit:
        return True
    return bool(re.search(r"(?:^|[\s(])@?egeria\b", str(message or ""), re.IGNORECASE))


def _channel_egeria_response_text(
    conversation: dict,
    messages: list[dict],
    prompt: str,
    requester: str,
) -> str:
    clean_prompt = str(prompt or "").strip()[:1200]
    if not clean_prompt:
        raise HTTPException(status_code=400, detail="Type what you want Egeria to help with.")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail="Egeria live replies need OPENAI_API_KEY to be configured.",
        )

    title = _safe_action_text(conversation.get("title"), 160) or "Poolside conversation"
    topic = _safe_action_text(conversation.get("topic"), 400) or "No topic has been set."
    participant_names = [
        _safe_action_text(row.get("name") or row.get("email"), 120)
        for row in conversation.get("participants", [])
        if isinstance(row, dict) and not row.get("system")
    ]
    thread_lines = []
    for row in messages[-12:]:
        if not isinstance(row, dict):
            continue
        author = _safe_action_text(row.get("author_name") or "Participant", 100)
        text = str(row.get("message") or "").strip()[:1200]
        if text:
            thread_lines.append(f"{author}: {text}")
    thread_context = "\n".join(thread_lines) or "No earlier messages."
    user_context = (
        f"Conversation: {title}\n"
        f"Topic: {topic}\n"
        f"Participants: {', '.join(filter(None, participant_names)) or 'Not named'}\n"
        f"Recent thread:\n{thread_context}\n\n"
        f"Current request from {requester}: {clean_prompt}"
    )

    try:
        client = getOpenAPIClient()
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Egeria, an AI participant in a private DevReady Poolside text conversation. "
                        "Respond directly to the latest request using only the supplied conversation context. "
                        "Help participants draft or improve a text, answer a question, summarize the thread, clarify a decision, "
                        "or propose the next message. When asked to write text, provide a concise send-ready draft. "
                        "Do not invent people, facts, commitments, prices, schedules, or completed actions. "
                        "Do not claim you sent a message or contacted anyone. Keep normal replies under 140 words unless detail is requested."
                    ),
                },
                {"role": "user", "content": user_context},
            ],
            temperature=0.25,
            max_tokens=420,
        )
        content = str(response.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Empty Egeria response")
        return content[:2400]
    except HTTPException:
        raise
    except Exception as exc:
        print(f"Poolside Egeria response failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail="Egeria could not reply right now. Your message was still saved.",
        ) from exc


def _channel_egeria_message(
    conversation: dict,
    messages: list[dict],
    prompt: str,
    requester: str,
    clean_domain: str,
    clean_id: str,
) -> dict:
    reply = _channel_egeria_response_text(conversation, messages, prompt, requester)
    return {
        "id": _safe_token("EGR"),
        "channel": clean_id,
        "conversation_id": clean_id,
        "domain": clean_domain,
        "author_name": "Egeria",
        "author_email": "egeria@devready.ai",
        "audience": "conversation",
        "message": reply,
        "created_at": _now_utc(),
        "helper": True,
        "provider": "openai",
    }


@app.post("/api/channels/conversations/{conversation_id}/egeria")
def ask_egeria_channel_helper(
    conversation_id: str,
    domain: str = Form(default="dev"),
    prompt: str = Form(default=""),
    requested_by_name: str = Form(default=""),
    requested_by_email: str = Form(default=""),
):
    clean_domain = _domain_key(domain)
    clean_id = _channel_key(conversation_id)
    conv_store, conversations = _read_channel_conversations(clean_domain)
    conversation = next((row for row in conversations if str(row.get("id") or "") == clean_id), None)
    _require_active_channel_conversation(conversation)

    message_store = _read_json_store(CHANNEL_MESSAGES_PATH, {})
    room_key = _conversation_message_key(clean_domain, clean_id, clean_id)
    messages = message_store.get(room_key, [])
    if not isinstance(messages, list):
        messages = []
    clean_prompt = str(prompt or "").strip()[:1200]
    requester = _channel_user_name(requested_by_name, requested_by_email)
    item = _channel_egeria_message(
        conversation,
        messages,
        clean_prompt,
        requester,
        clean_domain,
        clean_id,
    )
    messages.append(item)
    message_store[room_key] = messages[-500:]
    _write_json_store(CHANNEL_MESSAGES_PATH, message_store)

    conversation["participants"] = _ensure_egeria_participant(
        conversation.get("participants") if isinstance(conversation.get("participants"), list) else []
    )
    conversation["updated_at"] = item["created_at"]
    conversation["last_message"] = item["message"][:180]
    conversation["last_author"] = "Egeria"
    conv_store[clean_domain] = conversations
    _write_json_store(CHANNEL_CONVERSATIONS_PATH, conv_store)
    return {"ok": True, "message": item, "conversation": conversation}


@app.get("/api/channels/messages")
def channel_messages(channel: str = "general", domain: str = "dev", conversation_id: str = ""):
    clean_channel = _channel_key(channel)
    clean_domain = _domain_key(domain)
    store = _read_json_store(CHANNEL_MESSAGES_PATH, {})
    room_key = _conversation_message_key(clean_domain, conversation_id, clean_channel)
    messages = store.get(room_key, [])
    return {
        "ok": True,
        "channel": clean_channel,
        "conversation_id": _channel_key(conversation_id) if str(conversation_id or "").strip() else "",
        "domain": clean_domain,
        "messages": messages[-200:] if isinstance(messages, list) else [],
    }


@app.post("/api/channels/messages")
def post_channel_message(
    channel: str = Form(default="general"),
    conversation_id: str = Form(default=""),
    domain: str = Form(default="dev"),
    message: str = Form(default=""),
    author_name: str = Form(default=""),
    author_email: str = Form(default=""),
    audience: str = Form(default="all"),
    ask_egeria: bool = Form(default=False),
):
    clean_message = str(message or "").strip()
    if not clean_message:
        raise HTTPException(status_code=400, detail="Message is required.")
    clean_channel = _channel_key(channel)
    clean_domain = _domain_key(domain)
    clean_conversation_id = _channel_key(conversation_id) if str(conversation_id or "").strip() else ""
    conv_store = None
    conversations = []
    conversation = None
    if clean_conversation_id:
        conv_store, conversations = _read_channel_conversations(clean_domain)
        conversation = next(
            (row for row in conversations if str(row.get("id") or "") == clean_conversation_id),
            None,
        )
        _require_active_channel_conversation(conversation)
    store = _read_json_store(CHANNEL_MESSAGES_PATH, {})
    room_key = _conversation_message_key(clean_domain, clean_conversation_id, clean_channel)
    messages = store.get(room_key, [])
    if not isinstance(messages, list):
        messages = []
    now = _now_utc()
    item = {
        "id": _safe_token("MSG"),
        "channel": clean_channel,
        "conversation_id": clean_conversation_id,
        "domain": clean_domain,
        "author_name": _channel_user_name(author_name, author_email),
        "author_email": str(author_email or "").strip()[:160],
        "audience": str(audience or "all").strip()[:32] or "all",
        "message": clean_message[:2400],
        "created_at": now,
    }
    messages.append(item)
    store[room_key] = messages[-500:]
    _write_json_store(CHANNEL_MESSAGES_PATH, store)
    if conversation is not None and conv_store is not None:
        conversation["updated_at"] = item["created_at"]
        conversation["last_message"] = clean_message[:180]
        conversation["last_author"] = item["author_name"]
        conv_store[clean_domain] = conversations
        _write_json_store(CHANNEL_CONVERSATIONS_PATH, conv_store)

    result = {"ok": True, "message": item, "egeria_requested": False}
    author_is_egeria = (
        _normalize_user_key(item.get("author_email")) == _egeria_participant()["email"]
        or _normalize_user_key(item.get("author_name")) == "egeria"
    )
    should_ask_egeria = (
        conversation is not None
        and not author_is_egeria
        and _channel_egeria_requested(clean_message, ask_egeria)
    )
    if should_ask_egeria:
        result["egeria_requested"] = True
        try:
            egeria_item = _channel_egeria_message(
                conversation,
                messages,
                clean_message,
                item["author_name"],
                clean_domain,
                clean_conversation_id,
            )
            messages.append(egeria_item)
            store[room_key] = messages[-500:]
            _write_json_store(CHANNEL_MESSAGES_PATH, store)
            conversation["updated_at"] = egeria_item["created_at"]
            conversation["last_message"] = egeria_item["message"][:180]
            conversation["last_author"] = "Egeria"
            conv_store[clean_domain] = conversations
            _write_json_store(CHANNEL_CONVERSATIONS_PATH, conv_store)
            result["egeria_message"] = egeria_item
        except HTTPException as exc:
            result["egeria_error"] = str(exc.detail)
    return result


@app.get("/api/channels/audience")
def channel_audience(domain: str = "dev"):
    clean_domain = _domain_key(domain)
    people = _channel_people(clean_domain)

    return {
        "ok": True,
        "domain": clean_domain,
        "count": len(people),
        "people": people,
    }


CALL_INTAKE_QUESTIONS = [
    {
        "key": "practice",
        "label": "Practice area",
        "prompt": "Is this for DevReady technology and AI, LegalReady legal, or BuildReady construction and engineering?",
        "captures": ["practice", "domain", "brand"],
    },
    {
        "key": "role",
        "label": "Role target",
        "prompt": "Tell me about the role or business need in your own words. What outcome are you trying to create?",
        "captures": ["job_title", "business_outcome"],
    },
    {
        "key": "client",
        "label": "Client context",
        "prompt": "Who is the client or team, and what problem should this person solve?",
        "captures": ["company", "team", "problem_statement"],
    },
    {
        "key": "skills",
        "label": "Required skills",
        "prompt": "What skills, tools, or platforms are must-haves on day one?",
        "captures": ["required_skills", "domain_stack"],
    },
    {
        "key": "seniority",
        "label": "Seniority",
        "prompt": "What seniority level and years of experience feel right?",
        "captures": ["seniority", "leadership_scope", "years_experience"],
    },
    {
        "key": "delivery",
        "label": "Delivery model",
        "prompt": "Is the role remote, hybrid, onsite, contract, or full time?",
        "captures": ["location", "work_model", "engagement_type"],
    },
    {
        "key": "constraints",
        "label": "Constraints",
        "prompt": "Any timing, budget, compliance, clearance, or deal-breaker constraints?",
        "captures": ["start_date", "rate_range", "compliance", "exclusions"],
    },
    {
        "key": "success",
        "label": "Success profile",
        "prompt": "What would success look like in the first 30 to 90 days?",
        "captures": ["success_metrics", "deliverables"],
    },
    {
        "key": "caller_email",
        "label": "Caller email",
        "prompt": "What email should I save on this request?",
        "captures": ["caller_email"],
    },
    {
        "key": "caller_phone",
        "label": "Caller phone",
        "prompt": "What phone number should I keep on the request?",
        "captures": ["caller_phone"],
    },
    {
        "key": "callback_permission",
        "label": "Follow-up preference",
        "prompt": "Would you like a quick confirmation email with what I captured, and is a callback okay once the strongest match is confirmed?",
        "captures": ["callback_permission", "confirmation_email", "delivery_preference"],
    },
]


def _default_call_intake_questions() -> list[dict]:
    return json.loads(json.dumps(CALL_INTAKE_QUESTIONS))


def _normalize_call_intake_question(question: dict, index: int) -> dict | None:
    if not isinstance(question, dict):
        return None
    label = _safe_action_text(question.get("label"), 80) or f"Question {index + 1}"
    prompt = _safe_action_text(question.get("prompt"), 500)
    if not prompt:
        return None
    key = _safe_action_text(question.get("key"), 80).lower()
    key = re.sub(r"[^a-z0-9_]+", "_", key).strip("_") or re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")
    key = key or f"question_{index + 1}"
    captures = question.get("captures")
    if not isinstance(captures, list):
        captures = [key]
    clean_captures = [
        _safe_action_text(item, 80)
        for item in captures
        if _safe_action_text(item, 80)
    ][:8]
    return {
        "key": key,
        "label": label,
        "prompt": prompt,
        "captures": clean_captures or [key],
    }


def _call_intake_question_store() -> dict:
    data = _read_json_store(CALL_INTAKE_QUESTIONS_PATH, {})
    return data if isinstance(data, dict) else {}


def _call_intake_questions(domain: str = "dev") -> list[dict]:
    clean_domain = _domain_key(domain)
    store = _call_intake_question_store()
    custom = store.get(clean_domain)
    if not isinstance(custom, list):
        return _default_call_intake_questions()
    questions = [
        clean
        for index, question in enumerate(custom[:20])
        if (clean := _normalize_call_intake_question(question, index))
    ]
    return questions or _default_call_intake_questions()


def _save_call_intake_questions(domain: str, questions: list[dict]) -> list[dict]:
    clean_domain = _domain_key(domain)
    cleaned = [
        clean
        for index, question in enumerate((questions or [])[:20])
        if (clean := _normalize_call_intake_question(question, index))
    ]
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one question needs a prompt.")
    store = _call_intake_question_store()
    store[clean_domain] = cleaned
    _write_json_store(CALL_INTAKE_QUESTIONS_PATH, store)
    return cleaned


def _call_intake_public_base(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or ""
    configured = configured.strip().rstrip("/")
    if configured:
        if configured.startswith("http"):
            return configured
        return f"https://{configured}"
    return str(request.base_url).rstrip("/")


def _call_intake_env_status(request: Request) -> dict:
    base_url = _call_intake_public_base(request)
    provider = os.getenv("CALL_INTAKE_PROVIDER", "twilio").strip().lower() or "twilio"
    twilio_ready = all(os.getenv(key) for key in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"])
    retell_api_key = bool(os.getenv("RETELL_API_KEY"))
    retell_agent_id = bool(os.getenv("RETELL_AGENT_ID"))
    retell_ready = bool(retell_api_key and retell_agent_id)
    vapi_ready = bool(os.getenv("VAPI_API_KEY") or os.getenv("VAPI_ASSISTANT_ID"))
    realtime_ready = bool(os.getenv("OPENAI_API_KEY"))
    webhook_ready = bool(os.getenv("CALL_INTAKE_WEBHOOK_SECRET"))
    return {
        "provider": provider,
        "phone_number": os.getenv("RETELL_PHONE_NUMBER") or os.getenv("VAPI_PHONE_NUMBER") or os.getenv("TWILIO_PHONE_NUMBER", ""),
        "twilio": {
            "configured": twilio_ready,
            "account_sid": bool(os.getenv("TWILIO_ACCOUNT_SID")),
            "auth_token": bool(os.getenv("TWILIO_AUTH_TOKEN")),
            "phone_number": bool(os.getenv("TWILIO_PHONE_NUMBER")),
        },
        "retell": {
            "configured": retell_ready,
            "api_key": retell_api_key,
            "agent_id": retell_agent_id,
            "webhook_verification": "retell_signature" if retell_api_key else "call_intake_secret",
        },
        "vapi": {
            "configured": vapi_ready,
            "api_key": bool(os.getenv("VAPI_API_KEY")),
            "assistant_id": bool(os.getenv("VAPI_ASSISTANT_ID")),
        },
        "openai_realtime": {
            "configured": realtime_ready,
            "api_key": realtime_ready,
            "model": os.getenv("CALL_INTAKE_REALTIME_MODEL", "gpt-realtime"),
        },
        "webhooks": {
            "configured": webhook_ready,
            "voice": f"{base_url}/api/call-intake/voice",
            "status": f"{base_url}/api/call-intake/status",
            "provider": f"{base_url}/api/call-intake/provider-webhook",
            "retell": f"{base_url}/api/call-intake/provider-webhook?provider=retell",
            "vapi": f"{base_url}/api/call-intake/provider-webhook?provider=vapi",
            "media_stream": f"{base_url.replace('https://', 'wss://').replace('http://', 'ws://')}/api/call-intake/media",
        },
        "storage": {
            "jobs": True,
            "profiles": True,
            "matching": True,
        },
    }


def _call_intake_records() -> list[dict]:
    rows = _read_json_store(CALL_INTAKE_RECORDS_PATH, [])
    return rows if isinstance(rows, list) else []


def _append_call_intake_record(record: dict) -> dict:
    rows = _call_intake_records()
    clean = record if isinstance(record, dict) else {}
    clean["id"] = clean.get("id") or _safe_token("CALL")
    clean["created_at"] = clean.get("created_at") or _now_utc()
    rows.insert(0, clean)
    _write_json_store(CALL_INTAKE_RECORDS_PATH, rows[:500])
    return clean


def _call_intake_archive_rows() -> list[dict]:
    rows = _read_json_store(CALL_INTAKE_ARCHIVE_PATH, [])
    return rows if isinstance(rows, list) else []


def _call_intake_page_hidden_table_ready(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS callask_page_hidden (
            delete_key TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            call_sid TEXT,
            jd_id TEXT,
            role TEXT,
            company TEXT,
            delete_keys TEXT,
            deleted_at TIMESTAMPTZ,
            note TEXT
        )
        """
    )


def _call_intake_db_deleted_rows() -> list[dict]:
    rows = []
    try:
        conn = azure_client.getConnection()
        cur = conn.cursor()
        try:
            _call_intake_page_hidden_table_ready(cur)
            cur.execute(
                """
                SELECT delete_key, domain, call_sid, jd_id, role, company, delete_keys, deleted_at, note
                FROM callask_page_hidden
                ORDER BY deleted_at DESC NULLS LAST
                LIMIT 1000
                """
            )
            for row in cur.fetchall():
                try:
                    aliases = json.loads(row[6] or "[]")
                except Exception:
                    aliases = []
                rows.append(
                    {
                        "delete_key": row[0] or "",
                        "delete_keys": aliases if isinstance(aliases, list) else [],
                        "domain": row[1] or "",
                        "call_sid": row[2] or "",
                        "jd_id": row[3] or "",
                        "role": row[4] or "",
                        "company": row[5] or "",
                        "deleted_at": row[7].isoformat() if row[7] else "",
                        "note": row[8] or "",
                    }
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return []
    return rows


def _call_intake_deleted_rows() -> list[dict]:
    rows = _read_json_store(CALL_INTAKE_DELETED_PATH, [])
    json_rows = rows if isinstance(rows, list) else []
    return [*_call_intake_db_deleted_rows(), *json_rows]


def _call_intake_save_deleted_row(row: dict) -> None:
    if not isinstance(row, dict):
        return
    try:
        conn = azure_client.getConnection()
        cur = conn.cursor()
        try:
            _call_intake_page_hidden_table_ready(cur)
            cur.execute(
                """
                INSERT INTO callask_page_hidden (
                    delete_key, domain, call_sid, jd_id, role, company, delete_keys, deleted_at, note
                )
                VALUES (
                    %(delete_key)s, %(domain)s, %(call_sid)s, %(jd_id)s, %(role)s, %(company)s,
                    %(delete_keys)s, %(deleted_at)s, %(note)s
                )
                ON CONFLICT (delete_key) DO UPDATE SET
                    domain = EXCLUDED.domain,
                    call_sid = EXCLUDED.call_sid,
                    jd_id = EXCLUDED.jd_id,
                    role = EXCLUDED.role,
                    company = EXCLUDED.company,
                    delete_keys = EXCLUDED.delete_keys,
                    deleted_at = EXCLUDED.deleted_at,
                    note = EXCLUDED.note
                """,
                {
                    "delete_key": _safe_action_text(row.get("delete_key"), 260),
                    "domain": _domain_key(row.get("domain") or "dev"),
                    "call_sid": _safe_action_text(row.get("call_sid"), 120),
                    "jd_id": _safe_action_text(row.get("jd_id"), 80),
                    "role": _safe_action_text(row.get("role"), 220),
                    "company": _safe_action_text(row.get("company"), 220),
                    "delete_keys": json.dumps(row.get("delete_keys") or []),
                    "deleted_at": row.get("deleted_at") or _now_utc(),
                    "note": _safe_action_text(row.get("note"), 500),
                },
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _call_intake_archive_key(domain: str, call_sid: str = "", jd_id: str = "") -> str:
    clean_domain = _domain_key(domain)
    clean_call = _safe_action_text(call_sid, 120)
    clean_jd = _safe_action_text(jd_id, 80)
    if clean_call:
        return f"{clean_domain}:call:{clean_call}"
    if clean_jd:
        return f"{clean_domain}:jd:{clean_jd}"
    return ""


def _call_intake_archive_keys(domain: str) -> set[str]:
    clean_domain = _domain_key(domain)
    keys = set()
    for row in _call_intake_archive_rows():
        if not isinstance(row, dict) or _domain_key(row.get("domain") or "") != clean_domain:
            continue
        key = row.get("archive_key") or _call_intake_archive_key(clean_domain, row.get("call_sid"), row.get("jd_id"))
        if key:
            keys.add(key)
        for alias in row.get("archive_keys") or []:
            alias_key = _safe_action_text(alias, 260)
            if alias_key:
                keys.add(alias_key)
        call_key = _call_intake_archive_key(clean_domain, row.get("call_sid"), "")
        jd_key = _call_intake_archive_key(clean_domain, "", row.get("jd_id"))
        if call_key:
            keys.add(call_key)
        if jd_key:
            keys.add(jd_key)
    return keys


def _call_intake_is_archived(domain: str, call_sid: str = "", jd_id: str = "", keys: set[str] | None = None) -> bool:
    archive_keys = keys if keys is not None else _call_intake_archive_keys(domain)
    return bool(
        _call_intake_archive_key(domain, call_sid, "") in archive_keys
        or _call_intake_archive_key(domain, "", jd_id) in archive_keys
    )


def _call_intake_deleted_keys(domain: str) -> set[str]:
    clean_domain = _domain_key(domain)
    keys = set()
    for row in _call_intake_deleted_rows():
        if not isinstance(row, dict) or _domain_key(row.get("domain") or "") != clean_domain:
            continue
        for key in [
            row.get("delete_key"),
            row.get("archive_key"),
            _call_intake_archive_key(clean_domain, row.get("call_sid"), ""),
            _call_intake_archive_key(clean_domain, "", row.get("jd_id")),
        ]:
            clean_key = _safe_action_text(key, 260)
            if clean_key:
                keys.add(clean_key)
        for alias in row.get("delete_keys") or row.get("archive_keys") or []:
            clean_key = _safe_action_text(alias, 260)
            if clean_key:
                keys.add(clean_key)
    return keys


def _call_intake_is_deleted(domain: str, call_sid: str = "", jd_id: str = "", keys: set[str] | None = None) -> bool:
    deleted_keys = keys if keys is not None else _call_intake_deleted_keys(domain)
    return bool(
        _call_intake_archive_key(domain, call_sid, "") in deleted_keys
        or _call_intake_archive_key(domain, "", jd_id) in deleted_keys
    )


def _call_intake_sessions() -> dict:
    rows = _read_json_store(CALL_INTAKE_SESSIONS_PATH, {})
    return rows if isinstance(rows, dict) else {}


def _call_intake_session_key(call_sid: str) -> str:
    return _safe_action_text(call_sid, 120) or _safe_token("CALL")


def _save_call_intake_session(call_sid: str, session: dict) -> dict:
    sessions = _call_intake_sessions()
    key = _call_intake_session_key(call_sid)
    session["call_sid"] = key
    session["updated_at"] = _now_utc()
    sessions[key] = session
    _write_json_store(CALL_INTAKE_SESSIONS_PATH, sessions)
    return session


def _delete_call_intake_session(call_sid: str) -> bool:
    key = _call_intake_session_key(call_sid)
    sessions = _call_intake_sessions()
    existed = key in sessions
    if existed:
        sessions.pop(key, None)
        _write_json_store(CALL_INTAKE_SESSIONS_PATH, sessions)
    return existed


def _get_call_intake_session(call_sid: str, domain: str = "dev", from_number: str = "") -> dict:
    key = _call_intake_session_key(call_sid)
    sessions = _call_intake_sessions()
    session = sessions.get(key)
    if not isinstance(session, dict):
        session = {
            "call_sid": key,
            "domain": _domain_key(domain),
            "from": _safe_action_text(from_number, 80),
            "answers": {},
            "transcript": [],
            "status": "in_progress",
            "created_at": _now_utc(),
        }
    session.setdefault("answers", {})
    session.setdefault("transcript", [])
    if domain:
        session["domain"] = _domain_key(session.get("domain") or domain)
    if from_number and not session.get("from"):
        session["from"] = _safe_action_text(from_number, 80)
    return session


def _xml_escape(value) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _call_intake_speech_text(text: str) -> str:
    rate = os.getenv("CALL_INTAKE_TWILIO_RATE", "112%").strip() or "112%"
    escaped = _xml_escape(text)
    return f'<prosody rate="{_xml_escape(rate)}">{escaped}</prosody>'


def _call_intake_say(text: str) -> str:
    voice = os.getenv("CALL_INTAKE_TWILIO_VOICE", "Polly.Joanna-Neural")
    return f'<Say voice="{_xml_escape(voice)}" language="en-US">{_call_intake_speech_text(text)}</Say>'


def _call_intake_gather_twiml(request: Request, domain: str, call_sid: str, step: int, lead_in: str = "", retry: int = 0) -> str:
    clean_domain = _domain_key(domain)
    questions = _call_intake_questions(clean_domain)
    step = max(0, min(int(step or 0), len(questions) - 1))
    question = questions[step]
    base_url = _call_intake_public_base(request)
    retry = max(0, min(int(retry or 0), 2))
    action = f"{base_url}/api/call-intake/gather?domain={quote_plus(clean_domain)}&callSid={quote_plus(call_sid)}&step={step}"
    no_answer_action = f"{action}&retry={retry + 1}"
    prompt = question["prompt"]
    intro = lead_in or ("Hi, this is Egeria with DevReady. Happy to help. I will grab the key details and look for a strong match. " if step == 0 else "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather input="speech" action="{_xml_escape(action)}" method="POST" speechTimeout="auto" timeout="5" enhanced="true" speechModel="phone_call">
    {_call_intake_say(intro + prompt)}
  </Gather>
  <Redirect method="POST">{_xml_escape(no_answer_action)}</Redirect>
</Response>"""


def _call_intake_extract_skills(text: str) -> list[str]:
    try:
        normalized = normalize_all_skills(text or "")
    except Exception:
        normalized = []
    if normalized:
        return list(dict.fromkeys([str(skill).strip() for skill in normalized if str(skill).strip()]))[:12]
    parts = re.split(r",|;|\band\b|\bor\b", text or "", flags=re.I)
    return list(dict.fromkeys([_safe_action_text(part, 80) for part in parts if _safe_action_text(part, 80)]))[:10]


def _call_intake_profile_skills(skills: list[str]) -> list[dict]:
    clean = []
    for skill in skills or []:
        title = _safe_action_text(skill, 80)
        if title:
            clean.append({"title": title, "years": 1})
    return clean[:12]


def _call_intake_clean_spoken_email(value: str) -> str:
    original = _safe_action_text(value, 260)
    text = original.lower()
    if not text:
        return ""
    text = re.sub(r"\bquestion mark\b", "", text)
    text = re.sub(r"\bunderscore\b", "_", text)
    text = re.sub(r"\bdash\b|\bhyphen\b", "-", text)
    text = re.sub(r"\bplus\b", "+", text)
    replacements = [
        (r"\bat\s+", "@"),
        (r"\s+at\b", "@"),
        (r"\s*@\s*", "@"),
        (r"\bdot\b", "."),
        (r"\bperiod\b", "."),
        (r"\bpoint\b", "."),
        (r"\s*\.\s*", "."),
    ]
    text = text.replace("?", "")
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?<=\b[a-z])\.\s*(?=[a-z]\b)", "", text)
    text = re.sub(r"(?<=\b[a-z])\s+(?=[a-z]\b)", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("atevready", "@devready").replace("atdevready", "@devready").replace("dev-ready", "devready")
    text = text.replace("devready?", "devready").replace("ready?", "ready")
    match = re.search(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", text)
    if not match:
        return original[:240]
    email = match.group(0)
    local, _, domain = email.partition("@")
    local = local.replace("..", ".").strip(".")
    if re.match(r"^[a-z]\.[a-z]{3,}$", local) or re.match(r"^[a-z](?:\.[a-z]){2,}$", local):
        local = local.replace(".", "")
    return f"{local}@{domain}"[:240]


def _call_intake_clean_phone(value: str) -> str:
    text = _safe_action_text(value, 160)
    digits = re.sub(r"\D+", "", text)
    if len(digits) >= 7:
        return digits[:18]
    return text


def _call_intake_clean_answers(answers: dict) -> dict:
    clean = dict(answers or {})
    if clean.get("caller_email"):
        clean["caller_email"] = _call_intake_clean_spoken_email(clean.get("caller_email"))
    if clean.get("caller_phone"):
        clean["caller_phone"] = _call_intake_clean_phone(clean.get("caller_phone"))
    return clean


def _call_intake_existing_profile_by_email(email: str, domain: str) -> dict:
    clean_email = _call_intake_clean_spoken_email(email)
    if not clean_email:
        return {}
    try:
        matches = candidates.searchCandidatesByNameEmail(clean_email, limit=5, domain=domain) or []
        for match in matches:
            if (match.get("email") or "").strip().lower() == clean_email.lower():
                first = _safe_action_text(match.get("firstname"), 80)
                last = _safe_action_text(match.get("lastname"), 80)
                return {
                    "source_tag": "Call Ask",
                    "profile_id": _safe_action_text(match.get("personid") or match.get("id"), 80),
                    "name": _safe_action_text(" ".join([first, last]).strip() or match.get("name") or clean_email, 180),
                    "email": clean_email,
                    "existing": True,
                }
    except Exception:
        return {}
    return {}


def _call_intake_build_jd(session: dict) -> dict:
    answers = _call_intake_clean_answers(session.get("answers") if isinstance(session.get("answers"), dict) else {})
    practice = _safe_action_text(answers.get("practice") or answers.get("business_unit") or answers.get("domain"), 220)
    role = _safe_action_text(answers.get("role"), 220) or "New role"
    client = _safe_action_text(answers.get("client"), 500) or "DevReady client"
    skills_text = _safe_action_text(answers.get("skills"), 900)
    seniority = _safe_action_text(answers.get("seniority"), 700)
    delivery = _safe_action_text(answers.get("delivery"), 700)
    constraints = _safe_action_text(answers.get("constraints"), 700)
    success = _safe_action_text(answers.get("success"), 900)
    caller_email = _safe_action_text(answers.get("caller_email"), 240)
    caller_phone = _safe_action_text(answers.get("caller_phone"), 120)
    delivery_back = _safe_action_text(answers.get("delivery_back"), 700)
    industry_experience = _safe_action_text(answers.get("industry_experience"), 700)
    callback_permission = _safe_action_text(answers.get("callback_permission") or answers.get("callback") or answers.get("follow_up"), 700)
    known_keys = {
        "practice",
        "business_unit",
        "domain",
        "role",
        "client",
        "skills",
        "seniority",
        "delivery",
        "constraints",
        "success",
        "industry_experience",
        "caller_email",
        "caller_phone",
        "delivery_back",
        "callback_permission",
        "confirmation_email",
        "callback",
        "follow_up",
    }
    extra_answers = [
        f"{key.replace('_', ' ').title()}: {_safe_action_text(value, 900)}"
        for key, value in answers.items()
        if key not in known_keys and _safe_action_text(value, 900)
    ][:12]
    company = client.split(",")[0].strip()[:180] or "DevReady client"
    title = role[:220]
    call_sid = _safe_action_text(session.get("call_sid"), 120)
    transcript_text = _call_intake_session_transcript_text(session)
    jd_text = "\n".join(
        [
            "Source tag: Call Ask",
            f"Call ID: {call_sid or 'Unknown'}",
            f"Practice area: {practice or _domain_key(session.get('domain') or 'dev')}",
            f"Role target: {role}",
            f"Client context: {client}",
            f"Required skills: {skills_text or 'To be confirmed'}",
            f"Seniority: {seniority or 'To be confirmed'}",
            f"Delivery model: {delivery or 'To be confirmed'}",
            f"Constraints: {constraints or 'To be confirmed'}",
            f"Success profile: {success or 'To be confirmed'}",
            f"Industry experience: {industry_experience or 'To be confirmed'}",
            f"Caller email: {caller_email or 'To be confirmed'}",
            f"Caller phone: {caller_phone or 'To be confirmed'}",
            f"Caller delivery preference: {delivery_back or 'Voice readback'}",
            f"Callback permission: {callback_permission or 'Ask before calling back'}",
            f"Confirmation email: {'Queued when caller email is available' if caller_email else 'Email needed before sending'}",
            *extra_answers,
            "",
            "Call transcript:",
            transcript_text or "Transcript pending.",
        ]
    )
    return {
        "company": company,
        "title": title,
        "description": jd_text,
        "skills": _call_intake_extract_skills(skills_text + "\n" + jd_text),
        "source_tag": "Call Ask",
        "call_sid": call_sid,
        "caller_email": caller_email,
        "caller_phone": caller_phone,
        "transcript": transcript_text,
    }


def _call_intake_finalize(session: dict) -> dict:
    current_answers = session.get("answers") if isinstance(session.get("answers"), dict) else {}
    inferred_domain = _call_intake_domain_from_answers(
        current_answers,
        _call_intake_session_transcript_text(session),
        session.get("domain") or "dev",
    )
    session["domain"] = inferred_domain
    domain = inferred_domain
    jd = _call_intake_build_jd(session)
    created = {}
    match = {}
    try:
        existing_job = session.get("job") if isinstance(session.get("job"), dict) else {}
        jd_id = _safe_action_text(existing_job.get("jd_id"), 80)
        if jd_id:
            updated = jobs.updateJob(jd_id, jd["company"], jd["title"], domain, jd["description"], jd["skills"]) or {}
            created = {"jd_id": updated.get("jd_id") or jd_id}
        else:
            created = jobs.uploadJob(jd["company"], jd["title"], domain, jd["description"], jd["skills"]) or {}
            jd_id = str(created.get("jd_id") or "")
        session["job"] = {**jd, "jd_id": jd_id, "source_tag": "Call Ask"}
        if jd_id:
            saved_job = jobs.getJob(jd_id, domain) or session["job"]
            try:
                match = _egeria_best_internal_candidate_for_job(saved_job, domain)
            except Exception as match_error:
                match = {"error": _safe_action_text(str(match_error), 500)}
        session["request_contact"] = {
            "source_tag": "Call Ask",
            "email": jd.get("caller_email") or "",
            "phone": jd.get("caller_phone") or "",
        }
        candidate = match.get("candidate") if isinstance(match, dict) else {}
        session["profile"] = {
            "source_tag": "Internal talent match",
            "profile_id": _safe_action_text(candidate.get("profile_id") if isinstance(candidate, dict) else "", 80),
            "name": _safe_action_text(candidate.get("name") if isinstance(candidate, dict) else "", 180),
            "email": _safe_action_text(candidate.get("email") if isinstance(candidate, dict) else "", 240),
            "headline": _safe_action_text(candidate.get("headline") if isinstance(candidate, dict) else "", 220),
        }
    except Exception as create_error:
        session["job"] = jd
        session["error"] = _safe_action_text(str(create_error), 500)

    session["match"] = match
    candidate = match.get("candidate") if isinstance(match, dict) else {}
    if isinstance(candidate, dict) and candidate.get("name"):
        summary = (
            f"All set. I created the job description for {jd['title']}. "
            f"The best current match I found is {candidate.get('name')}, "
            f"{candidate.get('headline') or 'a candidate in the system'}, with a match score of {match.get('score', 'available')}. "
            "I saved the intake, queued a confirmation email with the captured details, and the team can confirm readiness before following up."
        )
    elif session.get("error"):
        summary = (
            "Thanks, I captured the intake. I am sending it to the DevReady team for review, "
            "and they will follow up with the best match."
        )
    else:
        summary = (
            f"Perfect, I created the job description for {jd['title']}. "
            "I saved it for matching review, queued a confirmation email with the captured details, and the team will follow up with the best confirmed fit."
        )
    session["summary"] = summary
    session["status"] = "completed"
    session["completed_at"] = _now_utc()
    try:
        _persist_call_intake_ask(session, domain)
    except Exception as persist_error:
        session["persist_error"] = _safe_action_text(str(persist_error), 500)
    return session


def _call_intake_partial_summary(session: dict) -> dict:
    answers = session.get("answers") if isinstance(session.get("answers"), dict) else {}
    role = _safe_action_text(answers.get("role"), 220) or "your request"
    session["status"] = "needs_follow_up"
    session["summary"] = (
        f"Thanks, I saved what I have for {role}. "
        "The DevReady team will review this Call Ask and follow up."
    )
    session["completed_at"] = session.get("completed_at") or _now_utc()
    try:
        _persist_call_intake_ask(session, session.get("domain") or "dev")
    except Exception as persist_error:
        session["persist_error"] = _safe_action_text(str(persist_error), 500)
    return session


def _call_intake_record_from_session(event: str, domain: str, session: dict) -> dict:
    return {
        "event": event,
        "provider": _safe_action_text(session.get("provider") or "twilio", 80),
        "domain": _domain_key(domain),
        "call_sid": _safe_action_text(session.get("call_sid"), 120),
        "source_tag": "Call Ask",
        "job": session.get("job", {}),
        "profile": session.get("profile", {}),
        "match": session.get("match", {}),
        "summary": session.get("summary", ""),
        "transcript": _call_intake_session_transcript_text(session),
    }


def _call_intake_header_secret(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("x-call-intake-secret")
        or request.headers.get("x-devready-call-secret")
        or request.query_params.get("secret")
        or ""
    ).strip()


def _call_intake_verify_retell_signature(raw_body: bytes, api_key: str, signature: str) -> bool:
    if not raw_body or not api_key or not signature:
        return False
    match = re.match(r"v=(\d+),d=([a-fA-F0-9]+)", signature.strip())
    if not match:
        return False
    timestamp = match.group(1)
    digest = match.group(2)
    try:
        sent_ms = int(timestamp)
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        if abs(now_ms - sent_ms) > 5 * 60 * 1000:
            return False
        expected = hmac.new(
            api_key.encode("utf-8"),
            raw_body + timestamp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, digest)
    except Exception:
        return False


def _call_intake_require_provider_secret(request: Request, raw_body: bytes | None = None) -> None:
    retell_key = (os.getenv("RETELL_API_KEY") or "").strip()
    retell_signature = request.headers.get("x-retell-signature") or request.headers.get("X-Retell-Signature") or ""
    if retell_key and retell_signature and _call_intake_verify_retell_signature(raw_body or b"", retell_key, retell_signature):
        return
    expected = (os.getenv("CALL_INTAKE_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return
    provided = _call_intake_header_secret(request)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid call intake webhook secret")


def _call_intake_nested(payload: dict, path: str):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _call_intake_first(payload: dict, paths: list[str], default=""):
    for path in paths:
        value = _call_intake_nested(payload, path) if "." in path else payload.get(path)
        if value not in (None, "", [], {}):
            return value
    return default


def _call_intake_find_structured(payload: dict) -> dict:
    candidates_to_check = [
        payload,
        payload.get("data") if isinstance(payload.get("data"), dict) else {},
        payload.get("call") if isinstance(payload.get("call"), dict) else {},
        payload.get("message") if isinstance(payload.get("message"), dict) else {},
        _call_intake_nested(payload, "data.call_analysis") or {},
        _call_intake_nested(payload, "data.call_analysis.custom_analysis_data") or {},
        _call_intake_nested(payload, "data.call.call_analysis") or {},
        _call_intake_nested(payload, "data.call.call_analysis.custom_analysis_data") or {},
        _call_intake_nested(payload, "call.call_analysis") or {},
        _call_intake_nested(payload, "call.call_analysis.custom_analysis_data") or {},
        _call_intake_nested(payload, "call_analysis") or {},
        _call_intake_nested(payload, "call_analysis.custom_analysis_data") or {},
        _call_intake_nested(payload, "message.call.analysis") or {},
        _call_intake_nested(payload, "message.call.analysis.custom_analysis_data") or {},
        _call_intake_nested(payload, "message.call.artifact") or {},
    ]
    for candidate in candidates_to_check:
        if not isinstance(candidate, dict):
            continue
        for key in ("structured_data", "structuredData", "custom_analysis_data", "customAnalysisData", "result"):
            value = candidate.get(key)
            if isinstance(value, dict) and value:
                return value
        outputs = candidate.get("structuredOutputs")
        if isinstance(outputs, dict):
            for output in outputs.values():
                if isinstance(output, dict) and isinstance(output.get("result"), dict):
                    return output["result"]
    return {}


def _call_intake_text(value) -> str:
    if isinstance(value, list):
        return ", ".join([_safe_action_text(item, 120) for item in value if _safe_action_text(item, 120)])
    if isinstance(value, dict):
        return _safe_action_text(json.dumps(value, ensure_ascii=True), 900)
    return _safe_action_text(value, 900)


def _call_intake_domain_from_text(value: str, default: str = "dev") -> str:
    text = (value or "").strip().lower()
    if not text:
        return _domain_key(default)
    if any(token in text for token in ["legalready", "legal ready", "law", "legal", "attorney", "paralegal", "compliance counsel"]):
        return "law"
    if any(token in text for token in ["buildready", "build ready", "construction", "engineering", "engineer", "project manager", "superintendent"]):
        return "engineer"
    if any(token in text for token in ["devready", "dev ready", "technology", "software", "developer", "ai", "data", "cloud", "cyber"]):
        return "dev"
    return _domain_key(default)


def _call_intake_domain_from_answers(answers: dict, transcript: str = "", default: str = "dev") -> str:
    if not isinstance(answers, dict):
        answers = {}
    direct = answers.get("domain") or answers.get("practice") or answers.get("business_unit") or answers.get("brand")
    combined = "\n".join(
        [
            _safe_action_text(direct, 400),
            _safe_action_text(answers.get("role"), 400),
            _safe_action_text(answers.get("client"), 400),
            _safe_action_text(answers.get("skills"), 800),
            _safe_action_text(transcript, 3000),
        ]
    )
    return _call_intake_domain_from_text(combined, default)


def _call_intake_is_weak_answer(key: str, value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return True
    weak = {
        "role": {"new role", "role", "to be confirmed", "your request", "call ask"},
        "client": {"devready client", "client", "client to confirm", "to be confirmed"},
        "skills": {"to be confirmed", "not specified", "unknown"},
        "seniority": {"to be confirmed", "unknown"},
        "delivery": {"to be confirmed", "unknown"},
        "constraints": {"to be confirmed", "unknown"},
        "success": {"to be confirmed", "unknown"},
        "caller_email": {"to be confirmed", "unknown", "none"},
        "caller_phone": {"to be confirmed", "unknown", "none"},
    }
    return text in weak.get(key, set())


def _call_intake_merge_answers(primary: dict, secondary: dict) -> dict:
    merged = dict(primary or {})
    for key, value in (secondary or {}).items():
        clean = _call_intake_text(value)
        if not clean:
            continue
        if _call_intake_is_weak_answer(key, merged.get(key, "")) or len(clean) > len(str(merged.get(key) or "")) + 8:
            merged[key] = clean
    return _call_intake_clean_answers(merged)


def _call_intake_transcript_line(item) -> str:
    if isinstance(item, str):
        return _safe_action_text(item, 1200)
    if not isinstance(item, dict):
        return _call_intake_text(item)
    role = _safe_action_text(
        item.get("role")
        or item.get("speaker")
        or item.get("user")
        or item.get("source")
        or item.get("participant")
        or "",
        80,
    )
    content = _safe_action_text(
        item.get("content")
        or item.get("text")
        or item.get("transcript")
        or item.get("message")
        or item.get("answer")
        or item.get("words")
        or "",
        1200,
    )
    if not role and item.get("question"):
        role = _safe_action_text(item.get("question"), 80)
    if not content and isinstance(item.get("arguments"), dict):
        content = _call_intake_text(item.get("arguments"))
    if role and content:
        return f"{role}: {content}"
    return content or _call_intake_text(item)


def _call_intake_flatten_transcript(value) -> str:
    if isinstance(value, list):
        return "\n".join([line for line in (_call_intake_transcript_line(item) for item in value) if line])[:12000]
    return _safe_action_text(value, 12000)


def _call_intake_session_transcript_text(session: dict) -> str:
    return _call_intake_flatten_transcript(session.get("transcript") or session.get("provider_transcript") or "")


def _call_intake_answers_from_provider_payload(payload: dict) -> dict:
    structured = _call_intake_find_structured(payload)
    merged = {}
    for source in [payload, payload.get("data") if isinstance(payload.get("data"), dict) else {}, payload.get("call") if isinstance(payload.get("call"), dict) else {}, structured]:
        if isinstance(source, dict):
            merged.update({k: v for k, v in source.items() if v not in (None, "", [], {})})
    answers = {
        "practice": _call_intake_text(_call_intake_first(merged, ["practice", "business_unit", "brand", "domain", "division"])),
        "role": _call_intake_text(_call_intake_first(merged, ["role", "role_title", "job_title", "title", "position"])),
        "client": _call_intake_text(_call_intake_first(merged, ["client", "company", "client_company", "team", "account"])),
        "skills": _call_intake_text(_call_intake_first(merged, ["skills", "required_skills", "must_have_skills", "tech_stack", "requirements"])),
        "seniority": _call_intake_text(_call_intake_first(merged, ["seniority", "level", "years_experience", "experience"])),
        "delivery": _call_intake_text(_call_intake_first(merged, ["delivery", "work_model", "location", "employment_type", "engagement_type"])),
        "constraints": _call_intake_text(_call_intake_first(merged, ["constraints", "timing", "budget", "compliance", "deal_breakers"])),
        "success": _call_intake_text(_call_intake_first(merged, ["success", "success_profile", "success_criteria", "first_90_days", "outcomes"])),
        "industry_experience": _call_intake_text(_call_intake_first(merged, ["industry_experience", "industry", "vertical", "domain_experience"])),
        "caller_email": _call_intake_text(_call_intake_first(merged, ["caller_email", "email", "contact_email"])),
        "caller_phone": _call_intake_text(_call_intake_first(merged, ["caller_phone", "phone", "phone_number", "contact_phone", "from_number", "caller_number"])),
        "callback_permission": _call_intake_text(_call_intake_first(merged, ["callback_permission", "callback", "follow_up", "call_back", "delivery_preference"])),
    }
    return _call_intake_clean_answers({key: value for key, value in answers.items() if value})


def _call_intake_transcript_from_provider(payload: dict) -> list[dict]:
    transcript = _call_intake_first(
        payload,
        [
            "transcript",
            "transcript_object",
            "transcript_with_tool_calls",
            "call.transcript",
            "call.transcript_object",
            "call.transcript_with_tool_calls",
            "message.transcript",
            "message.call.artifact.transcript",
        ],
        "",
    )
    if isinstance(transcript, list):
        return [{"question": "Provider transcript", "answer": _call_intake_transcript_line(item), "step": index, "at": _now_utc()} for index, item in enumerate(transcript[:40])]
    if transcript:
        return [{"question": "Provider transcript", "answer": _call_intake_text(transcript), "step": 0, "at": _now_utc()}]
    return []


def _call_intake_provider_text(payload: dict) -> str:
    pieces = []
    for path in [
        "summary",
        "call_summary",
        "call_analysis.call_summary",
        "call.call_analysis.call_summary",
        "message.call.summary",
        "transcript",
        "call.transcript",
        "message.transcript",
        "message.call.artifact.transcript",
    ]:
        value = _call_intake_first(payload, [path])
        if value:
            pieces.append(_call_intake_flatten_transcript(value))
    structured = _call_intake_find_structured(payload)
    if structured:
        pieces.append("Structured call analysis:\n" + _call_intake_text(structured))
    return "\n".join([piece for piece in pieces if piece])[:8000]


def _call_intake_ai_answers_from_text(text: str) -> dict:
    clean_text = _safe_action_text(text, 8000)
    if not clean_text or not os.getenv("OPENAI_API_KEY"):
        return {}
    try:
        client = getOpenAPIClient()
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract a DevReady Call Ask from a phone transcript or call summary. "
                        "Return only JSON with these string keys when present: practice, role, client, skills, seniority, "
                        "delivery, constraints, success, industry_experience, caller_email, caller_phone, callback_permission. Clean spoken emails and phone numbers. "
                        "For practice, infer DevReady for technology/AI/software, LegalReady for legal/compliance/law, and BuildReady for construction/engineering/project delivery. "
                        "For emails, convert spoken letters, 'at', 'dot', 'underscore', and 'dash' into a valid address when possible. "
                        "For client, prefer the company/team being hired for, not the caller's personal name. "
                        "For role, create a concise job title. Do not invent missing details."
                    ),
                },
                {"role": "user", "content": clean_text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return {}
        return _call_intake_clean_answers(
            {
                key: _call_intake_text(parsed.get(key))
                for key in [
                    "role",
                    "practice",
                    "client",
                    "skills",
                    "seniority",
                    "delivery",
                    "constraints",
                    "success",
                    "industry_experience",
                    "caller_email",
                    "caller_phone",
                    "callback_permission",
                ]
                if parsed.get(key)
            }
        )
    except Exception:
        return {}


def _call_intake_table_ready(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS callask_records (
            call_sid TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            source_tag TEXT,
            status TEXT,
            role TEXT,
            company TEXT,
            caller_email TEXT,
            caller_phone TEXT,
            jd_id TEXT,
            profile_id TEXT,
            match_name TEXT,
            match_score DOUBLE PRECISION,
            summary TEXT,
            transcript TEXT,
            created_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """
    )
    cur.execute("ALTER TABLE callask_records ADD COLUMN IF NOT EXISTS transcript TEXT")


def _call_intake_row_from_session(session: dict, domain: str) -> dict:
    answers = session.get("answers") if isinstance(session.get("answers"), dict) else {}
    job = session.get("job") if isinstance(session.get("job"), dict) else {}
    profile = session.get("profile") if isinstance(session.get("profile"), dict) else {}
    match = session.get("match") if isinstance(session.get("match"), dict) else {}
    candidate = match.get("candidate") if isinstance(match.get("candidate"), dict) else {}
    matched_profile_id = _safe_action_text(candidate.get("profile_id"), 80)
    return {
        "call_sid": _safe_action_text(session.get("call_sid"), 120),
        "domain": _domain_key(session.get("domain") or domain),
        "source_tag": session.get("source_tag") or job.get("source_tag") or "Call Ask",
        "status": _safe_action_text(session.get("status") or "in_progress", 80),
        "created_at": session.get("created_at") or _now_utc(),
        "completed_at": session.get("completed_at") or None,
        "updated_at": session.get("updated_at") or _now_utc(),
        "role": _safe_action_text(answers.get("role") or job.get("title"), 220),
        "company": _safe_action_text(job.get("company") or answers.get("client"), 220),
        "caller_email": _safe_action_text(answers.get("caller_email") or job.get("caller_email") or profile.get("email"), 240),
        "caller_phone": _safe_action_text(answers.get("caller_phone") or job.get("caller_phone") or profile.get("phone"), 120),
        "jd_id": _safe_action_text(job.get("jd_id"), 80),
        "profile_id": matched_profile_id or _safe_action_text(profile.get("profile_id"), 80),
        "match_name": _safe_action_text(candidate.get("name"), 180),
        "match_score": match.get("score") if isinstance(match.get("score"), (int, float)) else None,
        "summary": _safe_action_text(session.get("summary"), 900),
        "transcript": _safe_action_text(_call_intake_session_transcript_text(session), 12000),
    }


def _persist_call_intake_ask(session: dict, domain: str) -> None:
    row = _call_intake_row_from_session(session, domain)
    if not row["call_sid"]:
        return
    conn = azure_client.getConnection()
    cur = conn.cursor()
    try:
        _call_intake_table_ready(cur)
        cur.execute(
            """
            INSERT INTO callask_records (
                call_sid, domain, source_tag, status, role, company, caller_email, caller_phone,
                jd_id, profile_id, match_name, match_score, summary, transcript, created_at, completed_at, updated_at
            )
            VALUES (
                %(call_sid)s, %(domain)s, %(source_tag)s, %(status)s, %(role)s, %(company)s,
                %(caller_email)s, %(caller_phone)s, %(jd_id)s, %(profile_id)s, %(match_name)s,
                %(match_score)s, %(summary)s, %(transcript)s, %(created_at)s, %(completed_at)s, %(updated_at)s
            )
            ON CONFLICT (call_sid) DO UPDATE SET
                domain = EXCLUDED.domain,
                source_tag = EXCLUDED.source_tag,
                status = EXCLUDED.status,
                role = EXCLUDED.role,
                company = EXCLUDED.company,
                caller_email = EXCLUDED.caller_email,
                caller_phone = EXCLUDED.caller_phone,
                jd_id = EXCLUDED.jd_id,
                profile_id = EXCLUDED.profile_id,
                match_name = EXCLUDED.match_name,
                match_score = EXCLUDED.match_score,
                summary = EXCLUDED.summary,
                transcript = EXCLUDED.transcript,
                completed_at = EXCLUDED.completed_at,
                updated_at = EXCLUDED.updated_at
            """,
            row,
        )
        conn.commit()
    finally:
        conn.close()


def _call_intake_saved_transcript(domain: str, call_sid: str = "", jd_id: str = "") -> str:
    clean_domain = _domain_key(domain)
    call_sid = _safe_action_text(call_sid, 120)
    jd_id = _safe_action_text(jd_id, 80)
    if call_sid:
        session = _call_intake_sessions().get(_call_intake_session_key(call_sid))
        if isinstance(session, dict):
            transcript = _call_intake_session_transcript_text(session)
            if transcript:
                return transcript
    try:
        conn = azure_client.getConnection()
        cur = conn.cursor()
        try:
            _call_intake_table_ready(cur)
            if call_sid:
                cur.execute(
                    "SELECT transcript FROM callask_records WHERE domain = %s AND call_sid = %s LIMIT 1",
                    (clean_domain, call_sid),
                )
            elif jd_id:
                cur.execute(
                    "SELECT transcript FROM callask_records WHERE domain = %s AND jd_id = %s LIMIT 1",
                    (clean_domain, jd_id),
                )
            else:
                return ""
            row = cur.fetchone()
            return _safe_action_text(row[0] if row else "", 12000)
        finally:
            conn.close()
    except Exception:
        return ""


def _call_intake_internal_match_for_jd_id(jd_id: str, domain: str) -> dict:
    clean_domain = _domain_key(domain)
    clean_jd_id = _safe_action_text(jd_id, 80)
    if not clean_jd_id:
        return {}
    try:
        job = jobs.getJob(clean_jd_id, clean_domain)
    except Exception:
        return {}
    if not job:
        return {}
    try:
        match = _egeria_best_internal_candidate_for_job(job, clean_domain)
        return match if isinstance(match, dict) else {}
    except Exception:
        return {}


@app.get("/api/call-intake/health")
def call_intake_health(request: Request, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    status = _call_intake_env_status(request)
    provider = str(status.get("provider") or "twilio").lower()
    provider_ready = (
        status["retell"]["configured"]
        if provider == "retell"
        else status["vapi"]["configured"]
        if provider == "vapi"
        else status["twilio"]["configured"] and status["openai_realtime"]["configured"]
    )
    ready = (
        provider_ready
        and status["webhooks"]["configured"]
    )
    if provider == "retell":
        retell = status.get("retell") or {}
        missing_retell = []
        if not retell.get("agent_id"):
            missing_retell.append("RETELL_AGENT_ID")
        if not retell.get("api_key"):
            missing_retell.append("RETELL_API_KEY")
        provider_label = ", ".join(missing_retell) or "Retell agent/API key"
    elif provider == "vapi":
        provider_label = "Vapi API key or assistant"
    else:
        provider_label = "Twilio account, auth token, phone number, and OpenAI realtime"
    return {
        "ok": True,
        "ready": ready,
        "domain": clean_domain,
        "status": status,
        "missing": [
            label
            for label, configured in [
                (provider_label, provider_ready),
                ("CALL_INTAKE_WEBHOOK_SECRET", status["webhooks"]["configured"]),
            ]
            if not configured
        ],
        "updated_at": _now_utc(),
    }


@app.get("/api/call-intake/blueprint")
def call_intake_blueprint(request: Request, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    status = _call_intake_env_status(request)
    questions = _call_intake_questions(clean_domain)
    return {
        "ok": True,
        "domain": clean_domain,
        "questions": questions,
        "default_questions": _default_call_intake_questions(),
        "flow": [
            {"step": "open", "label": "Egeria opens warmly and asks how the caller is doing"},
            {"step": "freeflow", "label": "Caller describes the need naturally while Egeria listens and takes notes"},
            {"step": "checklist", "label": "Egeria optionally fills gaps with the saved question set"},
            {"step": "jd", "label": "Full transcript is post-processed into a saved job description"},
            {"step": "match", "label": "Existing DevReady, LegalReady, or BuildReady profiles are ranked against the JD"},
            {"step": "confirm", "label": "Egeria queues a confirmation email and prepares a callback-ready match summary"},
        ],
        "handoff_contract": {
            "create_job_endpoint": "/api/azureJobs/createJob",
            "create_profile_action": "create_profile",
            "match_endpoint": "/api/azureJobs/match/run",
            "delivery_channels": ["voice_readback", "sms", "email"],
        },
        "webhooks": status["webhooks"],
    }


@app.post("/api/call-intake/questions")
async def call_intake_save_questions(request: Request, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("reset"):
        store = _call_intake_question_store()
        store.pop(clean_domain, None)
        _write_json_store(CALL_INTAKE_QUESTIONS_PATH, store)
        questions = _default_call_intake_questions()
    else:
        questions = _save_call_intake_questions(clean_domain, payload.get("questions") or [])
    return {"ok": True, "domain": clean_domain, "questions": questions, "updated_at": _now_utc()}


@app.get("/api/call-intake/asks")
def call_intake_asks(domain: str = "dev", limit: int = 25):
    clean_domain = _domain_key(domain)
    max_rows = max(1, min(int(limit or 25), 100))
    archived_keys = _call_intake_archive_keys(clean_domain)
    deleted_keys = _call_intake_deleted_keys(clean_domain)
    internal_match_cache = {}
    rows = []
    try:
        conn = azure_client.getConnection()
        cur = conn.cursor()
        try:
            _call_intake_table_ready(cur)
            cur.execute(
                """
                SELECT call_sid, source_tag, status, created_at, completed_at, updated_at, role, company,
                       caller_email, caller_phone, jd_id, profile_id, match_name, match_score, summary, transcript
                FROM callask_records
                WHERE domain = %s
                ORDER BY COALESCE(updated_at, created_at) DESC
                LIMIT %s
                """,
                (clean_domain, max_rows),
            )
            for row in cur.fetchall():
                session_for_row = _call_intake_sessions().get(_call_intake_session_key(row[0] or ""))
                session_match = session_for_row.get("match") if isinstance(session_for_row, dict) and isinstance(session_for_row.get("match"), dict) else {}
                session_candidate = session_match.get("candidate") if isinstance(session_match.get("candidate"), dict) else {}
                matched_profile_id = _safe_action_text(session_candidate.get("profile_id"), 80)
                if _call_intake_is_deleted(clean_domain, row[0], row[10], deleted_keys):
                    continue
                if _call_intake_is_archived(clean_domain, row[0], row[10], archived_keys):
                    continue
                computed_match = {}
                if row[10]:
                    cache_key = str(row[10])
                    if cache_key not in internal_match_cache:
                        internal_match_cache[cache_key] = _call_intake_internal_match_for_jd_id(cache_key, clean_domain)
                    computed_match = internal_match_cache.get(cache_key) or {}
                computed_candidate = computed_match.get("candidate") if isinstance(computed_match.get("candidate"), dict) else {}
                computed_profile_id = _safe_action_text(computed_candidate.get("profile_id"), 80)
                rows.append(
                    {
                        "call_sid": row[0] or "",
                        "source_tag": row[1] or "Call Ask",
                        "status": row[2] or "",
                        "created_at": row[3].isoformat() if row[3] else "",
                        "completed_at": row[4].isoformat() if row[4] else "",
                        "updated_at": row[5].isoformat() if row[5] else "",
                        "role": row[6] or "",
                        "company": row[7] or "",
                        "caller_email": row[8] or "",
                        "caller_phone": row[9] or "",
                        "jd_id": row[10] or "",
                        "profile_id": matched_profile_id or computed_profile_id or row[11] or "",
                        "match_name": computed_candidate.get("name") or row[12] or "",
                        "match_score": computed_match.get("score") if computed_match.get("score") is not None else row[13],
                        "summary": row[14] or "",
                        "transcript": row[15] or "",
                        "archive_key": _call_intake_archive_key(clean_domain, row[0], row[10]),
                    }
                )
            if len(rows) < max_rows:
                existing_jd_ids = {str(item.get("jd_id") or "") for item in rows}
                cur.execute(
                    """
                    SELECT id, company, jobtitle, description
                    FROM jobdescription
                    WHERE domain = %s AND description ILIKE 'Source tag: Call Ask%%'
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (clean_domain, max_rows),
                )
                for jd_id, company, title, description in cur.fetchall():
                    if str(jd_id) in existing_jd_ids:
                        continue
                    if _call_intake_is_deleted(clean_domain, "", str(jd_id), deleted_keys):
                        continue
                    if _call_intake_is_archived(clean_domain, "", str(jd_id), archived_keys):
                        continue
                    text = description or ""
                    email_match = re.search(r"Caller email:\s*(.+)", text)
                    phone_match = re.search(r"Caller phone:\s*(.+)", text)
                    call_match = re.search(r"Call ID:\s*(.+)", text)
                    transcript_match = re.search(r"Call transcript:\s*(.+)", text, re.DOTALL)
                    rows.append(
                        {
                            "call_sid": _safe_action_text(call_match.group(1) if call_match else "", 120),
                            "source_tag": "Call Ask",
                            "status": "completed",
                            "created_at": "",
                            "completed_at": "",
                            "updated_at": "",
                            "role": title or "",
                            "company": company or "",
                            "caller_email": _safe_action_text(email_match.group(1) if email_match else "", 240),
                            "caller_phone": _safe_action_text(phone_match.group(1) if phone_match else "", 120),
                            "jd_id": str(jd_id),
                            "profile_id": "",
                            "match_name": "",
                            "match_score": None,
                            "summary": "Saved as a Call Ask job description.",
                            "transcript": _safe_action_text(transcript_match.group(1).strip() if transcript_match else "", 12000),
                            "archive_key": _call_intake_archive_key(clean_domain, "", str(jd_id)),
                        }
                    )
                    existing_jd_ids.add(str(jd_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        rows = []

    sessions = _call_intake_sessions()
    for session in sessions.values():
        if not isinstance(session, dict):
            continue
        if _domain_key(session.get("domain") or "dev") != clean_domain:
            continue
        if any(row.get("call_sid") == _safe_action_text(session.get("call_sid"), 120) for row in rows):
            continue
        answers = session.get("answers") if isinstance(session.get("answers"), dict) else {}
        job = session.get("job") if isinstance(session.get("job"), dict) else {}
        if _call_intake_is_deleted(clean_domain, session.get("call_sid"), job.get("jd_id"), deleted_keys):
            continue
        if _call_intake_is_archived(clean_domain, session.get("call_sid"), job.get("jd_id"), archived_keys):
            continue
        profile = session.get("profile") if isinstance(session.get("profile"), dict) else {}
        match = session.get("match") if isinstance(session.get("match"), dict) else {}
        candidate = match.get("candidate") if isinstance(match.get("candidate"), dict) else {}
        matched_profile_id = _safe_action_text(candidate.get("profile_id"), 80)
        rows.append(
            {
                "call_sid": _safe_action_text(session.get("call_sid"), 120),
                "source_tag": session.get("source_tag") or job.get("source_tag") or "Call Ask",
                "status": _safe_action_text(session.get("status") or "in_progress", 80),
                "created_at": session.get("created_at") or "",
                "completed_at": session.get("completed_at") or "",
                "updated_at": session.get("updated_at") or "",
                "role": _safe_action_text(answers.get("role") or job.get("title"), 220),
                "company": _safe_action_text(job.get("company") or answers.get("client"), 220),
                "caller_email": _safe_action_text(answers.get("caller_email") or job.get("caller_email") or profile.get("email"), 240),
                "caller_phone": _safe_action_text(answers.get("caller_phone") or job.get("caller_phone") or profile.get("phone"), 120),
                "jd_id": _safe_action_text(job.get("jd_id"), 80),
                "profile_id": matched_profile_id or _safe_action_text(profile.get("profile_id"), 80),
                "match_name": _safe_action_text(candidate.get("name"), 180),
                "match_score": match.get("score") if isinstance(match, dict) else None,
                "summary": _safe_action_text(session.get("summary"), 900),
                "transcript": _safe_action_text(_call_intake_session_transcript_text(session), 12000),
                "archive_key": _call_intake_archive_key(clean_domain, session.get("call_sid"), job.get("jd_id")),
            }
        )
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return {"ok": True, "domain": clean_domain, "asks": rows[:max_rows]}


@app.post("/api/call-intake/asks/{call_sid}/finalize")
def call_intake_finalize_ask(call_sid: str, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    session = _get_call_intake_session(call_sid, clean_domain)
    if not session.get("answers"):
        raise HTTPException(status_code=404, detail="Call Ask not found or has no captured answers.")
    final_session = _call_intake_finalize(session)
    _save_call_intake_session(call_sid, final_session)
    _append_call_intake_record(_call_intake_record_from_session("completed_manual_finalize", clean_domain, final_session))
    return {"ok": True, "domain": clean_domain, "ask": final_session}


@app.post("/api/call-intake/asks/archive")
async def call_intake_archive_ask(request: Request, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    call_sid = _safe_action_text(payload.get("call_sid"), 120)
    jd_id = _safe_action_text(payload.get("jd_id"), 80)
    role = _safe_action_text(payload.get("role"), 220)
    company = _safe_action_text(payload.get("company"), 220)
    archive_key = _safe_action_text(payload.get("archive_key"), 260) or _call_intake_archive_key(clean_domain, call_sid, jd_id)
    if not archive_key:
        raise HTTPException(status_code=400, detail="Call Ask needs a call ID or JD ID to archive.")
    archive_keys = [
        key
        for key in [
            archive_key,
            _call_intake_archive_key(clean_domain, call_sid, ""),
            _call_intake_archive_key(clean_domain, "", jd_id),
        ]
        if key
    ]

    rows = _call_intake_archive_rows()
    archive_key_set = set(archive_keys)
    kept = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        row_keys = {row.get("archive_key")}
        if isinstance(row.get("archive_keys"), list):
            row_keys.update(row.get("archive_keys") or [])
        if not row_keys & archive_key_set:
            kept.append(row)
    archived = {
        "archive_key": archive_key,
        "archive_keys": archive_keys,
        "domain": clean_domain,
        "call_sid": call_sid,
        "jd_id": jd_id,
        "role": role,
        "company": company,
        "transcript": _call_intake_saved_transcript(clean_domain, call_sid, jd_id),
        "archived_at": _now_utc(),
    }
    kept.insert(0, archived)
    _write_json_store(CALL_INTAKE_ARCHIVE_PATH, kept[:500])
    return {"ok": True, "domain": clean_domain, "archived": archived}


@app.get("/api/call-intake/asks/archive")
def call_intake_list_archive(domain: str = "dev", limit: int = 50):
    clean_domain = _domain_key(domain)
    max_rows = max(1, min(int(limit or 50), 100))
    deleted_keys = _call_intake_deleted_keys(clean_domain)
    rows = [
        row
        for row in _call_intake_archive_rows()
        if isinstance(row, dict) and _domain_key(row.get("domain") or "") == clean_domain
        and not _call_intake_is_deleted(clean_domain, row.get("call_sid"), row.get("jd_id"), deleted_keys)
    ]
    rows.sort(key=lambda item: item.get("archived_at") or "", reverse=True)
    for row in rows:
        if not row.get("transcript"):
            row["transcript"] = _call_intake_saved_transcript(clean_domain, row.get("call_sid"), row.get("jd_id"))
    return {"ok": True, "domain": clean_domain, "asks": rows[:max_rows]}


@app.post("/api/call-intake/asks/restore")
async def call_intake_restore_ask(request: Request, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    archive_key = _safe_action_text(payload.get("archive_key"), 260)
    call_sid = _safe_action_text(payload.get("call_sid"), 120)
    jd_id = _safe_action_text(payload.get("jd_id"), 80)
    if not archive_key:
        archive_key = _call_intake_archive_key(clean_domain, call_sid, jd_id)
    if not archive_key:
        raise HTTPException(status_code=400, detail="Archived Call Ask needs an archive key, call ID, or JD ID.")

    rows = _call_intake_archive_rows()
    restored = None
    kept = []
    for row in rows:
        row_keys = {row.get("archive_key")} if isinstance(row, dict) else set()
        if isinstance(row, dict) and isinstance(row.get("archive_keys"), list):
            row_keys.update(row.get("archive_keys") or [])
        if isinstance(row, dict) and _domain_key(row.get("domain") or "") == clean_domain and archive_key in row_keys:
            restored = row
            continue
        kept.append(row)
    _write_json_store(CALL_INTAKE_ARCHIVE_PATH, kept[:500])
    return {"ok": True, "domain": clean_domain, "restored": restored or {"archive_key": archive_key}}


@app.post("/api/call-intake/asks/delete")
async def call_intake_delete_ask(request: Request, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    archive_key = _safe_action_text(payload.get("archive_key"), 260)
    call_sid = _safe_action_text(payload.get("call_sid"), 120)
    jd_id = _safe_action_text(payload.get("jd_id"), 80)
    role = _safe_action_text(payload.get("role"), 220)
    company = _safe_action_text(payload.get("company"), 220)
    if not archive_key:
        archive_key = _call_intake_archive_key(clean_domain, call_sid, jd_id)
    delete_keys = [
        key
        for key in [
            archive_key,
            _call_intake_archive_key(clean_domain, call_sid, ""),
            _call_intake_archive_key(clean_domain, "", jd_id),
        ]
        if key
    ]
    if not delete_keys:
        raise HTTPException(status_code=400, detail="Call Ask needs a call ID or JD ID to delete.")

    deleted_session = _delete_call_intake_session(call_sid) if call_sid else False
    deleted_tracking_row = False
    try:
        conn = azure_client.getConnection()
        cur = conn.cursor()
        try:
            _call_intake_table_ready(cur)
            if call_sid and jd_id:
                cur.execute("DELETE FROM callask_records WHERE domain = %s AND (call_sid = %s OR jd_id = %s)", (clean_domain, call_sid, jd_id))
            elif call_sid:
                cur.execute("DELETE FROM callask_records WHERE domain = %s AND call_sid = %s", (clean_domain, call_sid))
            elif jd_id:
                cur.execute("DELETE FROM callask_records WHERE domain = %s AND jd_id = %s", (clean_domain, jd_id))
            deleted_tracking_row = bool(cur.rowcount)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        deleted_tracking_row = False

    delete_key_set = set(delete_keys)
    archive_rows = []
    for row in _call_intake_archive_rows():
        if not isinstance(row, dict):
            archive_rows.append(row)
            continue
        row_keys = {row.get("archive_key")}
        if isinstance(row.get("archive_keys"), list):
            row_keys.update(row.get("archive_keys") or [])
        if not row_keys & delete_key_set:
            archive_rows.append(row)
    _write_json_store(CALL_INTAKE_ARCHIVE_PATH, archive_rows[:500])

    deleted_rows = _call_intake_deleted_rows()
    kept_deleted = []
    for row in deleted_rows:
        if not isinstance(row, dict):
            kept_deleted.append(row)
            continue
        row_keys = {row.get("delete_key"), row.get("archive_key")}
        if isinstance(row.get("delete_keys"), list):
            row_keys.update(row.get("delete_keys") or [])
        if not row_keys & delete_key_set:
            kept_deleted.append(row)
    deleted = {
        "delete_key": delete_keys[0],
        "delete_keys": delete_keys,
        "domain": clean_domain,
        "call_sid": call_sid,
        "jd_id": jd_id,
        "role": role,
        "company": company,
        "deleted_at": _now_utc(),
        "note": "Hidden from Call Ask page. Saved JD/profile records are retained.",
    }
    kept_deleted.insert(0, deleted)
    _call_intake_save_deleted_row(deleted)
    _write_json_store(CALL_INTAKE_DELETED_PATH, kept_deleted[:500])
    return {
        "ok": True,
        "domain": clean_domain,
        "deleted": deleted,
        "removed": {"session": deleted_session, "tracking_row": deleted_tracking_row},
    }


@app.post("/api/call-intake/provider-webhook")
async def call_intake_provider_webhook(request: Request, domain: str = "dev", provider: str = ""):
    raw_body = await request.body()
    _call_intake_require_provider_secret(request, raw_body)
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    provider_name = _safe_action_text(
        provider
        or payload.get("provider")
        or _call_intake_nested(payload, "message.provider")
        or _call_intake_nested(payload, "call.provider")
        or "voice-agent",
        80,
    )
    event = _safe_action_text(
        payload.get("event")
        or payload.get("event_type")
        or payload.get("type")
        or _call_intake_nested(payload, "message.type")
        or "provider_webhook",
        120,
    )
    final_events = {"call_analyzed", "call_ended", "end-of-call-report", "end_of_call_report", "call.completed", "completed"}
    is_final = event.lower() in final_events or bool(_call_intake_find_structured(payload))
    call_sid = _safe_action_text(
        payload.get("call_sid")
        or payload.get("callSid")
        or payload.get("call_id")
        or payload.get("callId")
        or payload.get("retell_call_id")
        or payload.get("twilio_call_sid")
        or _call_intake_nested(payload, "call.call_id")
        or _call_intake_nested(payload, "call.id")
        or _call_intake_nested(payload, "message.call.id")
        or _safe_token("CALL"),
        120,
    )
    clean_domain = _domain_key(
        _call_intake_first(payload, ["domain", "dynamic_variables.domain", "metadata.domain", "call.metadata.domain"], domain)
        or domain
    )
    transcript = _call_intake_transcript_from_provider(payload)
    provider_text = _call_intake_provider_text(payload)
    answers = _call_intake_answers_from_provider_payload(payload)
    ai_answers = _call_intake_ai_answers_from_text(provider_text)
    answers = _call_intake_merge_answers(answers, ai_answers)
    inferred_domain = _call_intake_domain_from_answers(answers, provider_text, clean_domain)
    clean_domain = inferred_domain
    session = _get_call_intake_session(call_sid, clean_domain)
    session["provider"] = provider_name
    session["provider_event"] = event
    session["source_tag"] = "Call Ask"
    session["raw_provider_payload"] = {
        "event": event,
        "provider": provider_name,
        "summary": _safe_action_text(
            _call_intake_first(payload, ["summary", "call_summary", "call_analysis.call_summary", "call.call_analysis.call_summary", "message.call.summary"]),
            1200,
        ),
    }
    session["answers"] = _call_intake_merge_answers(session.get("answers") if isinstance(session.get("answers"), dict) else {}, answers)
    session["domain"] = clean_domain
    if transcript:
        session["transcript"] = transcript
    session["status"] = "completed" if is_final and session.get("answers") else "in_progress"
    _save_call_intake_session(call_sid, session)
    try:
        _persist_call_intake_ask(session, clean_domain)
    except Exception:
        pass
    _append_call_intake_record(
        {
            "event": event,
            "provider": provider_name,
            "domain": clean_domain,
            "call_sid": call_sid,
            "source_tag": "Call Ask",
            "answers": session.get("answers"),
            "status": session.get("status"),
        }
    )
    if not is_final:
        return {"ok": True, "accepted": True, "finalized": False, "domain": clean_domain, "call_sid": call_sid}
    if not session.get("answers"):
        return {"ok": True, "accepted": True, "finalized": False, "domain": clean_domain, "call_sid": call_sid, "message": "No structured answers found yet."}
    final_session = _call_intake_finalize(session)
    _save_call_intake_session(call_sid, final_session)
    _append_call_intake_record(_call_intake_record_from_session(f"completed_{provider_name}", clean_domain, final_session))
    return {"ok": True, "accepted": True, "finalized": True, "domain": clean_domain, "call_sid": call_sid, "ask": final_session}


@app.api_route("/api/call-intake/voice", methods=["GET", "POST"])
async def call_intake_voice(request: Request, domain: str = "dev"):
    form = {}
    if request.method == "POST":
        try:
            form = dict(await request.form())
        except Exception:
            form = {}
    call_sid = _safe_action_text(form.get("CallSid") or request.query_params.get("CallSid"), 120)
    from_number = _safe_action_text(form.get("From") or request.query_params.get("From"), 80)
    clean_domain = _domain_key(form.get("domain") or request.query_params.get("domain") or domain)
    _append_call_intake_record(
        {
            "event": "voice_webhook",
            "provider": "twilio",
            "domain": clean_domain,
            "call_sid": call_sid,
            "from": from_number,
            "status": "speech_gather_started",
        }
    )
    session = _get_call_intake_session(call_sid, clean_domain, from_number)
    _save_call_intake_session(call_sid, session)
    return Response(content=_call_intake_gather_twiml(request, clean_domain, call_sid, 0), media_type="application/xml")


@app.api_route("/api/call-intake/gather", methods=["GET", "POST"])
async def call_intake_gather(request: Request, domain: str = "dev", callSid: str = "", step: int = 0, retry: int = 0):
    form = {}
    if request.method == "POST":
        try:
            form = dict(await request.form())
        except Exception:
            form = {}
    call_sid = _safe_action_text(form.get("CallSid") or callSid or request.query_params.get("callSid"), 120)
    clean_domain = _domain_key(form.get("domain") or request.query_params.get("domain") or domain)
    questions = _call_intake_questions(clean_domain)
    step = max(0, min(int(step or request.query_params.get("step") or 0), len(questions) - 1))
    try:
        retry_count = int(retry or request.query_params.get("retry") or 0)
    except Exception:
        retry_count = 0
    speech = _safe_action_text(form.get("SpeechResult") or request.query_params.get("SpeechResult"), 2400)
    session = _get_call_intake_session(call_sid, clean_domain, form.get("From") or "")

    if speech:
        question = questions[step]
        answers = session.setdefault("answers", {})
        transcript = session.setdefault("transcript", [])
        answers[question["key"]] = speech
        transcript.append({"question": question["prompt"], "answer": speech, "step": step, "at": _now_utc()})
        _append_call_intake_record(
            {
                "event": "answer",
                "provider": "twilio",
                "domain": clean_domain,
                "call_sid": call_sid,
                "question": question["key"],
                "answer": speech,
            }
        )
        _save_call_intake_session(call_sid, session)
        try:
            _persist_call_intake_ask(session, clean_domain)
        except Exception:
            pass
        next_step = step + 1
        if next_step < len(questions):
            lead_in = "Great. " if next_step in {1, 4, 7} else ("Perfect. " if next_step in {8, 9} else "Thanks. ")
            return Response(
                content=_call_intake_gather_twiml(request, clean_domain, call_sid, next_step, lead_in=lead_in),
                media_type="application/xml",
            )

        final_session = _call_intake_finalize(session)
        _save_call_intake_session(call_sid, final_session)
        _append_call_intake_record(_call_intake_record_from_session("completed", clean_domain, final_session))
        return Response(
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  {_call_intake_say(final_session.get("summary") or "The intake is complete.")}
  {_call_intake_say("Thank you. Goodbye.")}
  <Hangup/>
</Response>""",
            media_type="application/xml",
        )

    if retry_count:
        if retry_count >= 2:
            partial_session = _call_intake_partial_summary(session)
            _save_call_intake_session(call_sid, partial_session)
            _append_call_intake_record(
                {
                    "event": "partial_completed",
                    "provider": "twilio",
                    "domain": clean_domain,
                    "call_sid": call_sid,
                    "source_tag": "Call Ask",
                    "summary": partial_session.get("summary", ""),
                }
            )
            return Response(
                content=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  {_call_intake_say(partial_session.get("summary") or "Thanks, I saved what I have.")}
  {_call_intake_say("Goodbye.")}
  <Hangup/>
</Response>""",
                media_type="application/xml",
            )
        lead_in = "Sorry, I missed that. "
        return Response(
            content=_call_intake_gather_twiml(request, clean_domain, call_sid, step, lead_in=lead_in, retry=retry_count),
            media_type="application/xml",
        )

    return Response(
        content=_call_intake_gather_twiml(request, clean_domain, call_sid, step),
        media_type="application/xml",
    )


@app.api_route("/api/call-intake/status", methods=["GET", "POST"])
async def call_intake_status(request: Request, domain: str = "dev"):
    payload = {}
    if request.method == "POST":
        try:
            payload = dict(await request.form())
        except Exception:
            payload = {}
    payload.update({k: v for k, v in request.query_params.items() if k not in payload})
    record = _append_call_intake_record(
        {
            "event": _safe_action_text(payload.get("StreamEvent") or payload.get("CallStatus") or "status", 80),
            "provider": "twilio",
            "domain": _domain_key(payload.get("domain") or domain),
            "call_sid": _safe_action_text(payload.get("CallSid"), 120),
            "stream_sid": _safe_action_text(payload.get("StreamSid"), 120),
            "status": _safe_action_text(payload.get("CallStatus") or payload.get("StreamEvent"), 120),
            "error": _safe_action_text(payload.get("StreamError"), 500),
        }
    )
    return {"ok": True, "record": record}


@app.websocket("/api/call-intake/media")
async def call_intake_media(websocket: WebSocket):
    await websocket.accept()
    call_sid = ""
    stream_sid = ""
    domain = "dev"
    media_packets = 0
    try:
        while True:
            message = await websocket.receive_text()
            try:
                data = json.loads(message)
            except Exception:
                data = {}
            event = data.get("event")
            if event == "start":
                start = data.get("start") or {}
                stream_sid = _safe_action_text(start.get("streamSid") or data.get("streamSid"), 120)
                call_sid = _safe_action_text(start.get("callSid"), 120)
                params = start.get("customParameters") or {}
                domain = _domain_key(params.get("domain") or domain)
                _append_call_intake_record(
                    {
                        "event": "media_start",
                        "provider": "twilio",
                        "domain": domain,
                        "call_sid": call_sid,
                        "stream_sid": stream_sid,
                        "status": "websocket_connected",
                    }
                )
            elif event == "media":
                media_packets += 1
            elif event == "stop":
                break
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        pass
    finally:
        _append_call_intake_record(
            {
                "event": "media_stop",
                "provider": "twilio",
                "domain": domain,
                "call_sid": call_sid,
                "stream_sid": stream_sid,
                "media_packets": media_packets,
                "status": "websocket_closed",
            }
        )


def _agent_context_with_access(context_json: str = "{}", domain: str = "dev", admin_token: str = "", numa_change_mode: str = "off") -> dict:
    try:
        context = json.loads(context_json or "{}")
        if not isinstance(context, dict):
            context = {}
    except Exception:
        context = {}
    context["domain"] = _domain_key(context.get("domain") or domain)
    users = _seed_access_users()
    requested_user = context.get("user") if isinstance(context.get("user"), dict) else {}
    user = None
    user_id = str(requested_user.get("id") or "").strip()
    if user_id and user_id in users:
        user = users[user_id]
    if not user:
        user = _find_access_user(
            users,
            username=str(requested_user.get("username") or ""),
            email=str(requested_user.get("email") or ""),
        )
    admin_unlocked = False
    if admin_token:
        try:
            _require_admin_token(admin_token)
            admin_unlocked = True
        except Exception:
            admin_unlocked = False
    is_active_super = bool(user and user.get("status") == "active" and user.get("role") == "super_user")
    can_admin = admin_unlocked or is_active_super
    change_mode_enabled = can_admin and str(numa_change_mode or "").strip().lower() in {"on", "true", "1", "enabled"}
    context["numa_access"] = {
        "role": user.get("role", "anonymous") if user else "anonymous",
        "status": user.get("status", "unknown") if user else "unknown",
        "admin_unlocked": admin_unlocked,
        "can_view_sensitive": can_admin,
        "can_request_changes": change_mode_enabled,
        "mode": "change-enabled" if change_mode_enabled else ("sensitive-view" if can_admin else "guide-only"),
    }
    return context


def _require_numa_action_access(context: dict):
    access = context.get("numa_access") if isinstance(context.get("numa_access"), dict) else {}
    if not access.get("can_request_changes"):
        raise HTTPException(
            status_code=403,
            detail="Numa actions require Administrator unlock or Super user access with Admin Updates turned on.",
        )


def _safe_action_text(value, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _egeria_log_rows() -> list[dict]:
    rows = _read_json_store(EGERIA_PROCESS_LOG_PATH, [])
    return rows if isinstance(rows, list) else []


def _egeria_recent_log(domain: str = "dev", limit: int = 8) -> list[dict]:
    clean_domain = _domain_key(domain)
    rows = [
        row for row in _egeria_log_rows()
        if clean_domain == "all" or _domain_key(row.get("domain", "dev")) == clean_domain
    ]
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[: max(1, min(limit, 40))]


def _egeria_log_event(domain: str, event_type: str, context: dict | None = None, before=None, after=None, message: str = "", payload=None) -> dict:
    rows = _egeria_log_rows()
    clean_domain = _domain_key(domain)
    event = {
        "id": _safe_token("EGR"),
        "domain": clean_domain,
        "event_type": _safe_action_text(event_type, 80) or "process_event",
        "message": _safe_action_text(message, 500),
        "context": context if isinstance(context, dict) else {},
        "before": before,
        "after": after,
        "payload": payload if isinstance(payload, (dict, list)) else {},
        "created_at": _now_utc(),
    }
    rows.insert(0, event)
    _write_json_store(EGERIA_PROCESS_LOG_PATH, rows[:1200])
    return event


def _egeria_context_json(raw: str = "") -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _egeria_bool_text(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "ready", "complete", "completed"}


def _egeria_process_assessment(domain: str, context: dict | None = None) -> dict:
    context = context if isinstance(context, dict) else {}
    clean_domain = _domain_key(domain or context.get("domain") or "dev")
    raw_step = _safe_action_text(context.get("currentStep"), 80) or "talent"
    step = raw_step
    next_step = _safe_action_text(context.get("nextStep"), 80)
    candidate_id = _safe_action_text(context.get("candidateId") or context.get("selectedProfileId"), 80)
    candidate_name = _safe_action_text(context.get("candidateName"), 180)
    candidate_status = _safe_action_text(context.get("candidateStatus"), 220).lower()
    job_id = _safe_action_text(context.get("jobId") or context.get("selectedJdId") or context.get("jdId"), 80)
    job_title = _safe_action_text(context.get("jobTitle"), 220)
    candidate_interest_received = _egeria_bool_text(context.get("candidateInterestReceived"))
    shortlist_count = 0
    try:
        shortlist_count = int(context.get("shortlistCount") or 0)
    except Exception:
        shortlist_count = 0
    candidate_review_ready = _egeria_bool_text(context.get("candidateReviewComplete") or context.get("candidateReviewReady"))
    client_interview_ready = _egeria_bool_text(context.get("clientInterviewReady"))
    schedule_saved = _egeria_bool_text(context.get("scheduleSaved"))

    if step in {"egeria", "process-pilot", "pilot"}:
        if not candidate_id:
            step = "talent"
            next_step = "select-candidate"
        elif not job_id:
            step = "job-descriptions"
            next_step = "select-job"
        elif shortlist_count <= 0:
            step = "profile"
            next_step = "confirm-profile"
        elif not candidate_review_ready:
            step = "shortlist"
            next_step = "candidate-review"
        elif not client_interview_ready:
            step = "candidate-review"
            next_step = "client-interview"
        else:
            step = "client-interview"
            next_step = "status"

    blockers = []
    warnings = []
    actions = []

    if step in {"find-in", "find-out", "profile", "candidate-chat", "shortlist", "candidate-review", "client-interview", "status"} and not candidate_id:
        blockers.append("No candidate is selected for this workflow.")
        actions.append({"label": "Choose candidate", "href": f"find-candidate.html?domain={clean_domain}", "type": "navigate"})
    if step in {"find-in", "find-out", "shortlist", "candidate-review", "client-interview", "status"} and not job_id:
        blockers.append("No job description is selected.")
        actions.append({"label": "Select job description", "href": f"job-descriptions.html?domain={clean_domain}", "type": "navigate"})
    if candidate_id and ("partial" in candidate_status or "missing" in candidate_status):
        warnings.append(f"{candidate_name or 'Candidate'} has an incomplete profile. Complete missing profile pieces before public sharing.")
        actions.append({"label": "Open profile", "href": f"profile-preview.html?domain={clean_domain}&profileId={candidate_id}", "type": "navigate"})
    if step in {"shortlist", "candidate-review", "client-interview", "status"} and shortlist_count <= 0:
        blockers.append("Shortlist is empty. Add the candidate before preparing client communication.")
        actions.append({"label": "Open shortlist", "href": f"client-comm.html?domain={clean_domain}", "type": "navigate"})
    if step in {"shortlist", "client-interview", "status"} and not candidate_review_ready:
        warnings.append("Candidate review is missing before client outreach. Confirm interest, role fit, gaps, timing, and pay first.")
        actions.append({"label": "Conduct candidate review", "href": f"schedule-interview.html?domain={clean_domain}&interview=ready", "type": "navigate"})
    if step in {"client-interview", "status"} and not client_interview_ready and schedule_saved:
        warnings.append("A schedule action exists, but client interview completion has not been confirmed.")

    if not next_step:
        next_step = "job-descriptions" if step == "talent" and not job_id else ""

    if not actions:
        next_action = {
            "talent": ("Find or choose a candidate", f"find-candidate.html?domain={clean_domain}"),
            "job-descriptions": ("Select job description", f"job-descriptions.html?domain={clean_domain}"),
            "find-in": ("Open candidate profile", f"profile-preview.html?domain={clean_domain}" + (f"&profileId={candidate_id}" if candidate_id else "")),
            "find-out": ("Open candidate profile", f"profile-preview.html?domain={clean_domain}" + (f"&profileId={candidate_id}" if candidate_id else "")),
            "profile": ("Review candidate profile", f"profile-preview.html?domain={clean_domain}" + (f"&profileId={candidate_id}" if candidate_id else "")),
            "candidate-chat": ("Prepare shortlist", f"client-comm.html?domain={clean_domain}"),
            "shortlist": ("Conduct candidate review", f"schedule-interview.html?domain={clean_domain}&interview=ready"),
            "candidate-review": ("Schedule client interview", f"schedule-interview.html?domain={clean_domain}&interview=client"),
            "client-interview": ("Open status tracker", f"status-tracker.html?domain={clean_domain}"),
        }.get(step, ("Open Talent workspace", f"find-candidate.html?domain={clean_domain}"))
        actions.append({"label": next_action[0], "href": next_action[1], "type": "navigate"})

    if blockers:
        recommendation = blockers[0]
        state_label = "Blocked"
    elif warnings:
        recommendation = warnings[0]
        state_label = "Needs review"
    elif candidate_id and job_id and shortlist_count > 0 and step == "shortlist":
        recommendation = f"{candidate_name or 'Candidate'} is ready for shortlist workflow, but Egeria will keep candidate review as the next safe checkpoint."
        state_label = "Ready with checkpoint"
    elif candidate_interest_received and candidate_id and job_id and step == "profile":
        recommendation = f"{candidate_name or 'Candidate'} submitted role feedback. Next action: confirm the profile and add the candidate to the shortlist."
        state_label = "Interest received"
    elif candidate_id and job_id:
        action_label = actions[0].get("label") if actions else "continue"
        recommendation = f"{candidate_name or 'Candidate'} is connected to {job_title or 'the selected role'}. Next action: {action_label}."
        state_label = "Ready"
    else:
        recommendation = "Start by selecting a candidate and job description in the current domain."
        state_label = "Start"

    return {
        "ok": True,
        "domain": clean_domain,
        "state_label": state_label,
        "current_step": step,
        "next_step": next_step,
        "source_step": raw_step,
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "recommendation": recommendation,
        "safe_actions": actions[:5],
        "checkpoint_required": True,
        "agent": "Egeria",
        "permissions": [
            "read workflow state",
            "write action log",
            "create checkpoint",
            "restore browser workflow state",
            "delegate safe workflow actions after user approval",
        ],
    }


@app.post("/api/egeria/process-pilot/assess")
def assess_egeria_process_pilot(
    domain: str = Form(default="dev"),
    context_json: str = Form(default=""),
):
    context = _egeria_context_json(context_json)
    assessment = _egeria_process_assessment(domain or context.get("domain", "dev"), context)
    assessment["recent_log"] = _egeria_recent_log(assessment["domain"], 8)
    return assessment


@app.post("/api/egeria/process-pilot/checkpoint")
def checkpoint_egeria_process_pilot(
    domain: str = Form(default="dev"),
    event_type: str = Form(default="checkpoint"),
    context_json: str = Form(default=""),
    before_json: str = Form(default=""),
    after_json: str = Form(default=""),
    message: str = Form(default=""),
):
    context = _egeria_context_json(context_json)
    before = _egeria_context_json(before_json)
    after = _egeria_context_json(after_json)
    event = _egeria_log_event(domain or context.get("domain", "dev"), event_type, context, before, after, message)
    return {"ok": True, "event": event, "recent_log": _egeria_recent_log(event["domain"], 8)}


@app.post("/api/egeria/process-pilot/rollback")
def rollback_egeria_process_pilot(
    domain: str = Form(default="dev"),
    context_json: str = Form(default=""),
    before_json: str = Form(default=""),
    after_json: str = Form(default=""),
    message: str = Form(default=""),
):
    context = _egeria_context_json(context_json)
    event = _egeria_log_event(
        domain or context.get("domain", "dev"),
        "rollback",
        context,
        _egeria_context_json(before_json),
        _egeria_context_json(after_json),
        message or "Egeria restored the last browser workflow checkpoint.",
    )
    return {"ok": True, "event": event, "recent_log": _egeria_recent_log(event["domain"], 8)}


@app.get("/api/egeria/process-pilot/log")
def egeria_process_pilot_log(domain: str = "dev", limit: int = 20):
    clean_domain = _domain_key(domain)
    return {"ok": True, "domain": clean_domain, "events": _egeria_recent_log(clean_domain, limit)}


EGERIA_CANDIDATE_WORKFLOW = [
    "Candidate answers the interest link.",
    "Egeria records the response as private profile feedback.",
    "DevReady runs the internal candidate review.",
    "After approval, Egeria prepares the client interview and outreach.",
    "After client approval, onboarding starts from the completed profile.",
]


def _egeria_feedback_notification(event: dict) -> dict:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    after = event.get("after") if isinstance(event.get("after"), dict) else {}
    link = payload.get("link") if isinstance(payload.get("link"), dict) else {}
    note = payload.get("note") if isinstance(payload.get("note"), dict) else {}
    acknowledged_at = _safe_action_text(event.get("acknowledged_at"), 80)
    return {
        "id": _safe_action_text(event.get("id"), 100),
        "workflow_id": _safe_action_text(
            context.get("workflowId") or after.get("workflowId") or link.get("token"),
            140,
        ),
        "domain": _domain_key(event.get("domain") or link.get("domain") or "dev"),
        "created_at": _safe_action_text(event.get("created_at"), 80),
        "acknowledged": bool(acknowledged_at),
        "acknowledged_at": acknowledged_at,
        "candidate": {
            "profile_id": _safe_action_text(context.get("candidateId") or link.get("profile_id"), 80),
            "name": _safe_action_text(context.get("candidateName") or link.get("candidate_name"), 240),
            "email": _safe_action_text(context.get("candidateEmail") or link.get("candidate_email"), 320),
        },
        "job": {
            "jd_id": _safe_action_text(context.get("jobId") or link.get("job_id"), 80),
            "company": _safe_action_text(context.get("jobCompany") or link.get("role_company"), 240),
            "title": _safe_action_text(context.get("jobTitle") or link.get("role_title"), 240),
        },
        "feedback": {
            "note_id": _safe_action_text(note.get("id") or after.get("note_id"), 100),
            "interest": _safe_action_text(note.get("interest") or after.get("interest"), 80),
            "thoughts": _safe_action_text(note.get("note"), 5000),
            "skills": _safe_action_text(note.get("skills"), 3000),
            "availability": _safe_action_text(note.get("availability"), 1000),
            "questions": _safe_action_text(note.get("questions"), 2000),
            "private": True,
        },
        "workflow": {
            "current_stage": "candidate-review",
            "completed_steps": 2,
            "steps": EGERIA_CANDIDATE_WORKFLOW,
            "next_action": "Run the internal candidate review.",
        },
    }


def _egeria_candidate_feedback(domain: str = "dev", limit: int = 40) -> list[dict]:
    clean_domain = _domain_key(domain)
    rows = [
        row for row in _egeria_log_rows()
        if row.get("event_type") == "candidate_role_feedback_submitted"
        and (clean_domain == "all" or _domain_key(row.get("domain") or "dev") == clean_domain)
    ]
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    try:
        safe_limit = max(1, min(int(limit or 40), 100))
    except (TypeError, ValueError):
        safe_limit = 40
    return [_egeria_feedback_notification(row) for row in rows[:safe_limit]]


@app.get("/api/egeria/one-tap/feedback")
def egeria_one_tap_feedback(domain: str = "dev", limit: int = 40):
    notifications = _egeria_candidate_feedback(domain, limit)
    return {
        "ok": True,
        "domain": _domain_key(domain),
        "count": len(notifications),
        "unread_count": sum(1 for item in notifications if not item.get("acknowledged")),
        "notifications": notifications,
    }


@app.post("/api/egeria/one-tap/feedback/{event_id}/acknowledge")
def acknowledge_egeria_one_tap_feedback(
    event_id: str,
    domain: str = Form(default="dev"),
    acknowledged_by: str = Form(default="DevReady"),
):
    clean_id = _safe_action_text(event_id, 100)
    clean_domain = _domain_key(domain)
    rows = _egeria_log_rows()
    event = next((
        row for row in rows
        if row.get("id") == clean_id
        and row.get("event_type") == "candidate_role_feedback_submitted"
        and (clean_domain == "all" or _domain_key(row.get("domain") or "dev") == clean_domain)
    ), None)
    if not event:
        raise HTTPException(status_code=404, detail="Candidate feedback notification not found.")
    if not event.get("acknowledged_at"):
        event["acknowledged_at"] = _now_utc()
        event["acknowledged_by"] = _safe_action_text(acknowledged_by, 160) or "DevReady"
        _write_json_store(EGERIA_PROCESS_LOG_PATH, rows[:1200])
    return {"ok": True, "notification": _egeria_feedback_notification(event)}


@app.get("/api/egeria/one-tap/jobs")
def egeria_one_tap_jobs(domain: str = "dev", limit: int = 50):
    clean_domain = _domain_key(domain)
    try:
        rows = jobs.listJobs(clean_domain, max(1, min(int(limit or 50), 100)))
    except Exception:
        rows = []
    return {"ok": True, "domain": clean_domain, "jobs": rows}


def _egeria_best_internal_candidate_for_job(job: dict, domain: str) -> dict:
    clean_domain = _domain_key(domain)
    skills = _radar_skill_terms(job.get("skills") or job.get("jd_skills") or [])
    description_words = sorted(_radar_words(job.get("description") or job.get("jd_text") or ""))[:16]
    search_terms = skills or description_words
    if not search_terms:
        raise HTTPException(status_code=400, detail="This job needs saved skills or a useful description before Egeria can match it.")
    try:
        match_limit = max(50, min(int(os.getenv("CALL_INTAKE_MATCH_LIMIT", "250")), 500))
    except Exception:
        match_limit = 250
    try:
        rows = candidates.searchCandidatesBySkills(",".join(search_terms[:16]), match_limit, domain=clean_domain)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not search internal candidates: {exc}")
    scored_rows = []
    for row in rows or []:
        candidate_id = str(row.get("id") or "")
        if not candidate_id:
            continue
        candidate_name = _safe_action_text(f"{row.get('firstName', '')} {row.get('lastName', '')}", 180).strip()
        candidate_email = _safe_action_text(row.get("email"), 240)
        if (
            (row.get("firstName") or "").strip().lower() == "call"
            or candidate_name.lower().startswith(("call ask", "call role"))
            or candidate_email.lower().startswith("callask+")
            or candidate_email.lower() == "candidate@email.com"
        ):
            continue
        candidate_skills = row.get("skillMatches") or []
        weighted = _radar_weighted_skill_score(search_terms[:16], candidate_skills, base_rank=row.get("searchRank") or row.get("skillCount") or 0)
        role_score = _radar_title_alignment(
            {
                "title": job.get("title") or job.get("jobtitle") or "",
                "company": job.get("company") or "",
            },
            f"{row.get('primaryStack', '')} {row.get('firstName', '')} {row.get('lastName', '')}",
        )
        process_score = _radar_process_score(row.get("step"))
        score = min(99, round(float(weighted.get("score") or 0) + role_score + process_score, 1))
        scored_rows.append({
            "score": score,
            "candidate": {
                "profile_id": candidate_id,
                "name": candidate_name or f"Profile {candidate_id}",
                "email": candidate_email,
                "headline": _safe_action_text(row.get("primaryStack"), 220),
            },
            "matched": weighted.get("matched", [])[:8],
            "gaps": weighted.get("gaps", [])[:6],
            "score_parts": {
                **(weighted.get("score_parts") or {}),
                "role_title": role_score,
                "process": process_score,
            },
            "reason": "Matched: " + ", ".join((weighted.get("matched") or candidate_skills or [])[:5]) if (weighted.get("matched") or candidate_skills) else "Candidate has overlapping internal profile evidence.",
        })
    scored_rows.sort(key=lambda item: (item.get("score", 0), len(item.get("matched") or [])), reverse=True)
    if not scored_rows:
        raise HTTPException(status_code=404, detail="Egeria did not find an internal candidate for this job yet.")
    return scored_rows[0]


@app.post("/api/egeria/one-tap/start")
def egeria_one_tap_start(
    request: Request,
    domain: str = Form(default="dev"),
    job_id: str = Form(default=""),
):
    clean_domain = _domain_key(domain)
    job_id = _safe_action_text(job_id, 80)
    if not job_id:
        raise HTTPException(status_code=400, detail="Choose a job before starting One-Tap Pilot.")
    job = jobs.getJob(job_id, clean_domain)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found for this domain.")
    best = _egeria_best_internal_candidate_for_job(job, clean_domain)
    candidate = best.get("candidate") or {}
    profile_id = str(candidate.get("profile_id") or "")
    candidate_name = candidate.get("name") or "Candidate"
    candidate_email = candidate.get("email") or ""
    role_title = job.get("title") or ""
    company = job.get("company") or ""
    role_description = job.get("description") or ""

    feedback = profile_role_feedback_link(
        request=request,
        profile_id=profile_id,
        domain=clean_domain,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        job_id=str(job.get("jd_id") or job_id),
        role_company=company,
        role_title=role_title,
        role_description=role_description,
    )
    interest_url = feedback.get("url", "")
    workflow_id = feedback.get("token", "")
    subject = f"DevReady role interest: {role_title}"
    body = (
        f"Hi {candidate_name},\n\n"
        f"Egeria found a strong potential match for you.\n\n"
        f"Role: {role_title}\n"
        f"Client: {company or 'DevReady client'}\n"
        f"Why this may fit: {best.get('reason') or 'Your profile aligns with the role requirements.'}\n\n"
        "Please open this secure link and tell us if you are interested, what looks good, and anything we should know before we schedule an internal review:\n\n"
        f"{interest_url}\n\n"
        "Thank you,\nDevReady"
    )
    context = {
        "domain": clean_domain,
        "currentStep": "one-tap-pilot",
        "nextStep": "candidate-interest",
        "candidateId": profile_id,
        "candidateName": candidate_name,
        "candidateEmail": candidate_email,
        "jobId": str(job.get("jd_id") or job_id),
        "jobTitle": role_title,
        "workflowId": workflow_id,
    }
    event = _egeria_log_event(
        clean_domain,
        "one_tap_interest_started",
        context=context,
        before={},
        after={
            **context,
            "score": best.get("score"),
            "interestLink": interest_url,
            "emailSubject": subject,
        },
        message=f"One-Tap Pilot selected {candidate_name} for {role_title} and prepared the interest email.",
        payload={
            "job": {
                "jd_id": job.get("jd_id") or job_id,
                "company": company,
                "title": role_title,
            },
            "candidate": candidate,
            "match": best,
            "link": feedback.get("link") or {},
            "workflow_id": workflow_id,
            "interest_url": interest_url,
        },
    )
    return {
        "ok": True,
        "domain": clean_domain,
        "job": {
            "jd_id": job.get("jd_id") or job_id,
            "company": company,
            "title": role_title,
            "description": role_description,
            "skills": job.get("skills") or [],
        },
        "candidate": candidate,
        "match": best,
        "interest": {
            "url": interest_url,
            "subject": subject,
            "body": body,
        },
        "next_steps": [
            "Candidate answers the interest link.",
            "Egeria records the response as private profile feedback.",
            "DevReady runs the internal candidate review.",
            "After approval, Egeria prepares the client interview/outreach.",
            "After client approval, onboarding is started from the completed profile.",
        ],
        "event": event,
        "recent_log": _egeria_recent_log(clean_domain, 8),
    }


AI_TECH_DEBT_DIMENSIONS = {
    "strategy": "AI strategy and business outcomes",
    "data": "Data readiness and knowledge architecture",
    "architecture": "Application and integration architecture",
    "risk": "Security, privacy, risk, and compliance",
    "delivery": "AI delivery lifecycle and LLMOps",
    "operating_model": "Operating model, talent, and adoption",
    "automation": "Workflow redesign and automation",
    "economics": "Vendor, cost, measurement, and observability",
    "industry": "Industry-specific AI fit",
}


AI_TECH_DEBT_CHOICES = [
    {"value": 0, "label": "Not started", "description": "No clear owner, artifacts, or repeatable practice exists."},
    {"value": 1, "label": "Ad hoc / isolated", "description": "Some individual activity exists, but it is informal and hard to repeat."},
    {"value": 2, "label": "Emerging", "description": "Early pilots or standards exist, but coverage is uneven."},
    {"value": 3, "label": "Defined", "description": "A documented approach exists and is used by more than one team."},
    {"value": 4, "label": "Scaled", "description": "The practice is used across major workflows with governance."},
    {"value": 5, "label": "Optimized / governed", "description": "The practice is measured, improved, and embedded in operations."},
]


def _ai_tech_debt_industry_profile(industry: str) -> dict:
    key = re.sub(r"[^a-z0-9]+", "_", str(industry or "technology").strip().lower()).strip("_") or "technology"
    profiles = {
        "technology": {
            "label": "Technology / SaaS",
            "signals": ["software delivery", "support automation", "product telemetry", "security reviews", "developer enablement"],
            "pilot": "AI delivery cockpit for product support, backlog triage, code review assistance, and release-risk summaries.",
            "risk": "Protect source code, customer data, production secrets, and model outputs with reviewable controls.",
        },
        "healthcare": {
            "label": "Healthcare",
            "signals": ["HIPAA", "clinical safety", "patient access", "claims", "prior authorization", "care coordination"],
            "pilot": "Patient operations assistant for intake, prior authorization support, care-team summaries, and compliance-reviewed handoffs.",
            "risk": "Separate clinical decision support from administrative automation and enforce human review for patient-impacting outputs.",
        },
        "financial_services": {
            "label": "Financial Services",
            "signals": ["model risk", "fraud", "KYC", "AML", "auditability", "advisor workflows"],
            "pilot": "Risk-aware client operations copilot for document intake, KYC exception triage, and audit-ready decision support.",
            "risk": "Model risk management, explainability, retention, and customer-impact controls must be designed before scaling.",
        },
        "manufacturing_engineering": {
            "label": "Manufacturing / Engineering",
            "signals": ["quality", "field service", "predictive maintenance", "project controls", "safety", "supplier issues"],
            "pilot": "Engineering operations copilot for field reports, defect triage, safety observations, and project-control risk summaries.",
            "risk": "Keep safety-critical decisions and engineered calculations behind expert review and traceable source evidence.",
        },
        "construction": {
            "label": "Construction / Infrastructure",
            "signals": ["RFI", "submittals", "safety", "schedule risk", "change orders", "field reports"],
            "pilot": "Project-controls assistant for RFI triage, change-order risk, daily reports, and safety observation summaries.",
            "risk": "Contract language, safety data, and schedule commitments need strong approval gates before client-facing use.",
        },
        "retail": {
            "label": "Retail / Commerce",
            "signals": ["personalization", "inventory", "customer support", "pricing", "catalog operations", "returns"],
            "pilot": "Retail operations assistant for catalog enrichment, support deflection, inventory explanations, and campaign targeting.",
            "risk": "Customer data use, pricing fairness, brand voice, and fulfillment promises require measurable guardrails.",
        },
        "legal": {
            "label": "Legal / Professional Services",
            "signals": ["privilege", "matter knowledge", "contract review", "legal ops", "conflicts", "document retention"],
            "pilot": "Matter-intelligence assistant for contract intake, clause comparison, privilege-aware summaries, and legal ops reporting.",
            "risk": "Privilege, confidentiality, citations, and attorney review must be explicit in every AI workflow.",
        },
        "energy_utilities": {
            "label": "Energy / Utilities",
            "signals": ["asset inspection", "grid reliability", "field safety", "regulatory reporting", "maintenance", "outage response"],
            "pilot": "Asset and field-operations assistant for inspection notes, outage summaries, work-order prioritization, and regulatory evidence packs.",
            "risk": "Operational reliability, worker safety, critical infrastructure, and regulatory evidence require strict human approval.",
        },
        "public_sector": {
            "label": "Public Sector",
            "signals": ["citizen service", "records retention", "procurement", "transparency", "accessibility", "policy compliance"],
            "pilot": "Citizen-service and records assistant for intake routing, policy lookup, accessibility checks, and transparent response drafts.",
            "risk": "Procurement, public records, accessibility, bias review, and explainability need to be visible from day one.",
        },
    }
    return profiles.get(key, profiles["technology"])


def _ai_tech_debt_score_question(question_id: str, dimension: str, prompt: str) -> dict:
    return {
        "id": question_id,
        "type": "score",
        "dimension": dimension,
        "prompt": prompt,
        "choices": AI_TECH_DEBT_CHOICES,
    }


def _ai_tech_debt_text_question(question_id: str, dimension: str, prompt: str) -> dict:
    return {
        "id": question_id,
        "type": "text",
        "dimension": dimension,
        "prompt": prompt,
        "placeholder": "Type the CIO/CTO answer here. Specific examples help the AI recommendations.",
    }


def _ai_tech_debt_linkedin_profiles(value: str) -> list[str]:
    text = str(value or "")
    url_matches = re.findall(r"https?://[^\s,]+linkedin\.com/[^\s,]+|(?:www\.)?linkedin\.com/[^\s,]+", text, flags=re.I)
    raw = url_matches or re.split(r"[\n,]+", text)
    profiles = []
    for item in raw:
        clean = _safe_action_text(item, 500).strip()
        if not clean:
            continue
        if "linkedin.com" in clean.lower() and not re.match(r"^https?://", clean, re.I):
            clean = "https://" + clean.lstrip("/")
        profiles.append(clean)
    return profiles[:8]


def _ai_tech_debt_linkedin_context(profiles: list[str]) -> str:
    labels = []
    for profile in profiles:
        clean = re.sub(r"^https?://(www\.)?linkedin\.com/", "", profile, flags=re.I)
        clean = clean.strip("/").replace("/", ": ").replace("-", " ")
        if clean:
            labels.append(_safe_action_text(clean, 80))
    if not labels:
        return ""
    return "; ".join(labels[:5])


def _ai_tech_debt_questionnaire(industry: str = "technology", company: str = "", research: dict | None = None, linkedin_profiles: str = "") -> dict:
    profile = _ai_tech_debt_industry_profile(industry)
    raw_company = _safe_action_text(company, 180)
    standard_mode = not raw_company or raw_company.strip().lower() == "standard"
    company_name = "Standard AI readiness profile" if standard_mode else raw_company
    industry_label = profile["label"]
    research = research if isinstance(research, dict) else {}
    website = research.get("website") if isinstance(research.get("website"), dict) else {}
    public_items = (research.get("public_signals") or {}).get("items") if isinstance(research.get("public_signals"), dict) else []
    linkedin_items = _ai_tech_debt_linkedin_profiles(linkedin_profiles or research.get("linkedin_profiles_text") or "")
    linkedin_context = _ai_tech_debt_linkedin_context(linkedin_items)
    research_clues = []
    if website.get("signals"):
        research_clues.append("website signals: " + ", ".join(website.get("signals", [])[:6]))
    if public_items:
        research_clues.append("public/news signal: " + _safe_action_text(public_items[0].get("title"), 180))
    if linkedin_items:
        research_clues.append(f"LinkedIn signal(s): {linkedin_context or str(len(linkedin_items)) + ' supplied link(s)'}")
    research_context = "; ".join(research_clues)
    company_reference = "a standard organization" if standard_mode else company_name
    sections = [
        {
            "id": "strategy",
            "title": "1. AI Strategy And Executive Outcomes",
            "description": "Checks whether AI is tied to business value, ownership, and executive operating cadence.",
            "questions": [
                _ai_tech_debt_score_question("strategy_1", "strategy", f"How clearly has {company_name} defined the business outcomes AI should improve in {industry_label}?"),
                _ai_tech_debt_score_question("strategy_2", "strategy", "How well are AI opportunities prioritized by value, risk, effort, and executive sponsorship?"),
                _ai_tech_debt_score_question("strategy_3", "strategy", "How consistently does leadership review AI progress, blockers, and measurable impact?"),
                _ai_tech_debt_text_question("strategy_open", "strategy", f"What are the top three business outcomes the CIO/CTO wants AI to improve in the next 6 to 12 months for {company_reference}?"),
            ],
        },
        {
            "id": "data",
            "title": "2. Data Readiness And Knowledge Architecture",
            "description": "Assesses whether internal data can safely power retrieval, copilots, automation, and analytics.",
            "questions": [
                _ai_tech_debt_score_question("data_1", "data", "How clean, current, and well-owned are the data sources that AI would need?"),
                _ai_tech_debt_score_question("data_2", "data", "How mature are permissions, metadata, lineage, and retention rules for AI-accessible knowledge?"),
                _ai_tech_debt_score_question("data_3", "data", "How ready is the organization for retrieval-augmented generation across documents, systems, and operational records?"),
                _ai_tech_debt_text_question("data_open", "data", "Which data sources, systems, or knowledge bases are currently the biggest blocker to trustworthy AI?"),
            ],
        },
        {
            "id": "architecture",
            "title": "3. Application And Integration Architecture",
            "description": "Looks for AI-ready APIs, workflow integration, identity controls, and maintainable application patterns.",
            "questions": [
                _ai_tech_debt_score_question("architecture_1", "architecture", "How easily can existing applications expose secure APIs or events for AI workflows?"),
                _ai_tech_debt_score_question("architecture_2", "architecture", "How ready are identity, role-based access, audit logs, and environment separation for AI agents?"),
                _ai_tech_debt_score_question("architecture_3", "architecture", "How well can AI outputs be reviewed, edited, approved, and written back to core systems?"),
            ],
        },
        {
            "id": "risk",
            "title": "4. Security, Privacy, Risk, And Compliance",
            "description": "Measures whether AI use can be governed without blocking practical adoption.",
            "questions": [
                _ai_tech_debt_score_question("risk_1", "risk", f"How well are {profile['risk']} addressed today?"),
                _ai_tech_debt_score_question("risk_2", "risk", "How mature are policies for sensitive data, prompt logging, model selection, and external tool use?"),
                _ai_tech_debt_score_question("risk_3", "risk", "How consistently can the company prove what data an AI workflow used and who approved the final action?"),
                _ai_tech_debt_text_question("risk_open", "risk", "What is the executive team's biggest concern about AI risk, security, privacy, or compliance?"),
            ],
        },
        {
            "id": "delivery",
            "title": "5. AI Delivery Lifecycle And LLMOps",
            "description": "Checks whether pilots can move into production with test coverage, monitoring, and cost controls.",
            "questions": [
                _ai_tech_debt_score_question("delivery_1", "delivery", "How repeatable is the path from AI idea to prototype, pilot, production, and support?"),
                _ai_tech_debt_score_question("delivery_2", "delivery", "How mature are prompt/version management, evals, regression testing, and human feedback loops?"),
                _ai_tech_debt_score_question("delivery_3", "delivery", "How visible are AI quality, latency, usage, cost, and failure patterns after launch?"),
            ],
        },
        {
            "id": "operating_model",
            "title": "6. Operating Model, Talent, And Adoption",
            "description": "Assesses whether teams can adopt AI safely and change the way work gets done.",
            "questions": [
                _ai_tech_debt_score_question("operating_1", "operating_model", "How clear are ownership, funding, and decision rights for AI initiatives?"),
                _ai_tech_debt_score_question("operating_2", "operating_model", "How prepared are business users, engineers, and operations teams to use AI tools responsibly?"),
                _ai_tech_debt_score_question("operating_3", "operating_model", "How well does the organization handle change management, training, and adoption measurement?"),
                _ai_tech_debt_text_question("operating_open", "operating_model", "Which teams are most ready for an AI pilot, and which teams will need the most coaching?"),
            ],
        },
        {
            "id": "automation",
            "title": "7. Workflow Redesign And Automation",
            "description": "Finds whether AI is being used to redesign workflows, not just add chat on top of broken processes.",
            "questions": [
                _ai_tech_debt_score_question("automation_1", "automation", "How well are current workflows mapped with handoffs, decisions, exceptions, and approval points?"),
                _ai_tech_debt_score_question("automation_2", "automation", "How much manual work could be safely reduced through AI-assisted intake, triage, drafting, or decision support?"),
                _ai_tech_debt_score_question("automation_3", "automation", "How ready are teams to measure before-and-after workflow impact from an AI pilot?"),
                _ai_tech_debt_text_question("automation_open", "automation", "Name one workflow that would be valuable enough for a 30 to 60 day AI proof of concept."),
            ],
        },
        {
            "id": "economics",
            "title": "8. Vendor, Cost, Measurement, And Observability",
            "description": "Looks for portfolio discipline: vendor fit, spend visibility, benefits tracking, and run-cost control.",
            "questions": [
                _ai_tech_debt_score_question("economics_1", "economics", "How well are AI vendors, models, licenses, and tools inventoried and rationalized?"),
                _ai_tech_debt_score_question("economics_2", "economics", "How clearly can the company measure AI ROI, time saved, revenue impact, risk reduction, or quality gains?"),
                _ai_tech_debt_score_question("economics_3", "economics", "How mature are cost guardrails for model usage, data processing, and agent automation?"),
            ],
        },
        {
            "id": "industry",
            "title": f"9. Industry-Specific AI Fit: {industry_label}",
            "description": "These questions are generated around the selected industry's operating constraints and AI opportunity patterns.",
            "questions": [
                _ai_tech_debt_score_question("industry_1", "industry", f"How well has {company_name} identified AI use cases around {', '.join(profile['signals'][:3])}?"),
                _ai_tech_debt_score_question("industry_2", "industry", f"How prepared is the organization to manage industry-specific risk: {profile['risk']}"),
                _ai_tech_debt_score_question("industry_3", "industry", f"How strong is the case for this practical pilot: {profile['pilot']}"),
                _ai_tech_debt_text_question("industry_open", "industry", f"What industry-specific AI opportunity or constraint should the AI DevReady Coach understand before designing a pilot for {company_reference}?"),
            ],
        },
    ]
    if not standard_mode:
        sections.insert(
            1,
            {
                "id": "company_research",
                "title": f"2. Company Research Signal: {company_name}",
                "description": research_context or "Questions are customized from the selected company, supplied website, public web/news scan, LinkedIn links, and executive context.",
                "questions": [
                    _ai_tech_debt_score_question("company_research_1", "strategy", f"How clearly do {company_name}'s website, web/news, competitor, and supplied LinkedIn signals point to AI, automation, data, or workflow modernization opportunities?"),
                    _ai_tech_debt_score_question("company_research_2", "operating_model", f"How ready does {company_name}'s leadership and operating model appear for an AI adoption program based on the supplied LinkedIn research{(': ' + linkedin_context) if linkedin_context else ''}?"),
                    _ai_tech_debt_text_question("company_research_open", "strategy", f"Using {company_name}'s website, web/news scan, competitor signals, LinkedIn research{(' (' + linkedin_context + ')') if linkedin_context else ''}, and executive context, what company-specific AI opportunity or risk should be tested first?"),
                ],
            },
        )
    return {
        "ok": True,
        "generated_by": "Egeria AI Tech Debt",
        "domain": "dev",
        "company": company_name,
        "standard": standard_mode,
        "industry": industry,
        "industry_label": industry_label,
        "estimated_minutes": "15-20",
        "rating_choices": AI_TECH_DEBT_CHOICES,
        "dimensions": AI_TECH_DEBT_DIMENSIONS,
        "industry_profile": profile,
        "research_context": research_context,
        "linkedin_profiles": linkedin_items,
        "linkedin_context": linkedin_context,
        "sections": sections,
    }


def _ai_tech_debt_questionnaire_from_json(value: str, fallback: dict) -> dict:
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except Exception:
        return fallback
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sections"), list):
        return fallback

    def clean_item(item, max_text: int = 2000):
        if isinstance(item, dict):
            return {str(key)[:80]: clean_item(val, max_text) for key, val in item.items()}
        if isinstance(item, list):
            return [clean_item(val, max_text) for val in item[:80]]
        if isinstance(item, str):
            return _safe_action_text(item, max_text)
        if isinstance(item, (int, float, bool)) or item is None:
            return item
        return _safe_action_text(str(item), max_text)

    questionnaire = clean_item(parsed)
    for key in ("ok", "generated_by", "domain", "company", "standard", "industry", "industry_label", "estimated_minutes", "rating_choices", "dimensions", "industry_profile", "research_context", "linkedin_profiles", "linkedin_context"):
        if key not in questionnaire and key in fallback:
            questionnaire[key] = fallback[key]
    return questionnaire


def _ai_tech_debt_grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _ai_tech_debt_level(score: float) -> str:
    if score >= 85:
        return "AI-ready operating system"
    if score >= 72:
        return "Strong foundation with targeted debt"
    if score >= 58:
        return "Pilot-ready with governance gaps"
    if score >= 42:
        return "Early maturity with material AI tech debt"
    return "High AI tech debt; start with controlled foundations"


def _ai_tech_debt_eval_text(value: str, limit: int = 700) -> str:
    return _safe_action_text(re.sub(r"\s+", " ", str(value or "")).strip(), limit)


def _ai_tech_debt_normalize_url(value: str) -> str:
    clean = _safe_action_text(value, 500).strip()
    if not clean:
        return ""
    if not re.match(r"^https?://", clean, re.I):
        clean = "https://" + clean
    return clean


def _ai_tech_debt_fetch_website(website_url: str) -> dict:
    url = _ai_tech_debt_normalize_url(website_url)
    if not url:
        return {"ok": False, "url": "", "summary": "No company website provided.", "signals": []}
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "VETCODE AI Tech Debt assessment/1.0"},
            allow_redirects=True,
        )
        response.raise_for_status()
        text = _strip_html(response.text)
        text = re.sub(r"\s+", " ", text).strip()
        signals = []
        keywords = [
            "AI", "artificial intelligence", "automation", "data", "analytics", "cloud", "security",
            "platform", "customer", "workflow", "operations", "innovation", "machine learning",
        ]
        lower_text = text.lower()
        for keyword in keywords:
            if keyword.lower() in lower_text:
                signals.append(keyword)
        return {
            "ok": True,
            "url": response.url or url,
            "summary": text[:1800],
            "signals": signals[:12],
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "summary": f"Website scan unavailable: {_safe_action_text(str(exc), 220)}",
            "signals": [],
        }


def _ai_tech_debt_fetch_public_signals(company: str, industry_label: str, limit: int = 8) -> dict:
    clean_company = _safe_action_text(company, 220)
    if not clean_company:
        return {"query": "", "items": []}
    query = f'"{clean_company}" ({industry_label} OR AI OR automation OR digital transformation OR competitors OR rival OR market)'
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "VETCODE AI Tech Debt assessment/1.0"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall(".//item")[: max(1, min(limit, 12))]:
            source = item.find("source")
            title = _strip_html(item.findtext("title", ""))[:260]
            summary = _strip_html(item.findtext("description", ""))[:500]
            items.append(
                {
                    "title": title,
                    "link": item.findtext("link", ""),
                    "published": item.findtext("pubDate", ""),
                    "source": _strip_html(source.text if source is not None else "")[:120],
                    "summary": summary,
                    "competitor_signal": bool(re.search(r"\b(competitor|rival|market share|alternative|versus|vs\.?)\b", f"{title} {summary}", re.I)),
                }
            )
        return {"query": query, "items": items}
    except Exception as exc:
        return {
            "query": query,
            "items": [],
            "error": _safe_action_text(str(exc), 220),
        }


def _ai_tech_debt_research(company: str, industry: str, website_url: str = "", include_external: bool = False, linkedin_profiles: str = "") -> dict:
    profile = _ai_tech_debt_industry_profile(industry)
    standard_mode = not _safe_action_text(company, 220) or _safe_action_text(company, 220).strip().lower() == "standard"
    website = _ai_tech_debt_fetch_website(website_url) if website_url else {
        "ok": False,
        "url": "",
        "summary": "No company website provided.",
        "signals": [],
    }
    public = _ai_tech_debt_fetch_public_signals(company, profile["label"]) if include_external and not standard_mode else {
        "query": "",
        "items": [],
    }
    competitor_items = [
        item for item in public.get("items", [])
        if item.get("competitor_signal")
    ]
    return {
        "enabled": bool(include_external or website_url),
        "company": _safe_action_text(company, 220),
        "industry_label": profile["label"],
        "website": website,
        "public_signals": public,
        "competitor_signals": competitor_items[:5],
        "linkedin_profiles": _ai_tech_debt_linkedin_profiles(linkedin_profiles),
        "linkedin_profiles_text": _safe_action_text(linkedin_profiles, 3000),
        "scanned_at": _now_utc(),
    }


def _ai_tech_debt_dimension_recommendation(dimension: str, score: float) -> str:
    label = AI_TECH_DEBT_DIMENSIONS.get(dimension, dimension)
    base = {
        "strategy": "Create a prioritized AI value map with executive owners, measurable outcomes, and a 90-day governance cadence.",
        "data": "Build a trusted knowledge and data access layer before scaling copilots or agents into operational workflows.",
        "architecture": "Expose AI-safe APIs, approval states, write-back rules, and audit trails so agents can act without bypassing controls.",
        "risk": "Define model, data, privacy, and human-approval controls before pilots touch sensitive or customer-impacting workflows.",
        "delivery": "Create an LLMOps lane with prompt/version control, eval suites, rollback plans, usage monitoring, and feedback capture.",
        "operating_model": "Stand up an AI operating model with business owners, technical owners, training, and adoption metrics.",
        "automation": "Map the target workflow end to end, then redesign it around human review points and measurable cycle-time reduction.",
        "economics": "Inventory AI spend and vendors, then tie pilots to cost, revenue, risk, quality, or productivity measures.",
        "industry": "Choose one industry-specific use case with strong business value and low enough risk to prove safely in 30 to 60 days.",
    }.get(dimension, f"Improve {label.lower()} with clearer ownership, evidence, and measurable outcomes.")
    if score >= 78:
        return f"Keep scaling {label.lower()}: {base}"
    if score >= 60:
        return f"Tighten {label.lower()}: {base}"
    return f"Address {label.lower()} first: {base}"


def _ai_tech_debt_ai_enhance_report(report: dict, company: str, industry: str, business_context: str, research: dict) -> dict:
    if not business_context and not (research or {}).get("enabled"):
        return {}
    try:
        client = getOpenAPIClient()
        payload = json.dumps(
            {
                "company": company,
                "industry": _ai_tech_debt_industry_profile(industry).get("label"),
                "business_context": business_context,
                "research": research,
                "current_report": {
                    "overall_score": report.get("overall_score"),
                    "overall_grade": report.get("overall_grade"),
                    "priority_debt": report.get("priority_debt", [])[:4],
                    "recommended_pilot": report.get("recommended_pilot"),
                },
            },
            ensure_ascii=False,
        )
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Egeria for DevReady. Improve an AI Tech Debt assessment using only the CIO/CTO context, "
                        "website scan, and public/competitor signals provided. Return compact JSON with keys: "
                        "context_summary, research_summary, competitor_takeaways array, adjusted_recommendations array, "
                        "pilot_focus, executive_next_step. Do not invent facts; if research is thin, say so."
                    ),
                },
                {"role": "user", "content": payload},
            ],
            temperature=0.25,
        )
        content = response.choices[0].message.content or ""
        found = re.search(r"\{.*\}", content, re.S)
        parsed = json.loads(found.group(0) if found else content)
        return {
            "context_summary": _safe_action_text(parsed.get("context_summary"), 900),
            "research_summary": _safe_action_text(parsed.get("research_summary"), 900),
            "competitor_takeaways": [_safe_action_text(item, 320) for item in (parsed.get("competitor_takeaways") or [])[:5]],
            "adjusted_recommendations": [_safe_action_text(item, 420) for item in (parsed.get("adjusted_recommendations") or [])[:6]],
            "pilot_focus": _safe_action_text(parsed.get("pilot_focus"), 700),
            "executive_next_step": _safe_action_text(parsed.get("executive_next_step"), 500),
        }
    except Exception:
        signals = []
        website = (research or {}).get("website") or {}
        if website.get("signals"):
            signals.append("Website signals: " + ", ".join(website.get("signals", [])[:8]))
        competitor_count = len((research or {}).get("competitor_signals") or [])
        if competitor_count:
            signals.append(f"{competitor_count} public competitor/market signal(s) were found.")
        return {
            "context_summary": business_context[:900] if business_context else "No detailed CIO/CTO narrative was provided.",
            "research_summary": " ".join(signals) or "External research was limited or unavailable.",
            "competitor_takeaways": [],
            "adjusted_recommendations": [],
            "pilot_focus": "",
            "executive_next_step": "Review the scorecard and select one controlled AI pilot for an AI DevReady Coach to scope.",
        }


def _ai_tech_debt_evaluate(
    questionnaire: dict,
    answers: dict,
    company: str,
    industry: str,
    respondent_title: str = "",
    business_context: str = "",
    research: dict | None = None,
) -> dict:
    scored_by_dimension = {key: [] for key in AI_TECH_DEBT_DIMENSIONS}
    open_answers = []
    for section in questionnaire.get("sections", []):
        for question in section.get("questions", []):
            qid = question.get("id")
            dimension = question.get("dimension")
            if question.get("type") == "score":
                try:
                    value = float(answers.get(qid, 0))
                except Exception:
                    value = 0
                scored_by_dimension.setdefault(dimension, []).append(max(0, min(5, value)))
            elif question.get("type") == "text":
                text = _ai_tech_debt_eval_text(answers.get(qid, ""))
                if text:
                    open_answers.append({"id": qid, "dimension": dimension, "answer": text})

    scorecard = []
    total_values = []
    for dimension, label in AI_TECH_DEBT_DIMENSIONS.items():
        values = scored_by_dimension.get(dimension) or []
        avg = (sum(values) / len(values)) if values else 0
        percent = round((avg / 5) * 100, 1)
        total_values.extend(values)
        scorecard.append(
            {
                "dimension": dimension,
                "label": label,
                "score": percent,
                "grade": _ai_tech_debt_grade(percent),
                "level": _ai_tech_debt_level(percent),
                "recommendation": _ai_tech_debt_dimension_recommendation(dimension, percent),
            }
        )
    overall = round((sum(total_values) / max(1, len(total_values)) / 5) * 100, 1)
    weakest = sorted(scorecard, key=lambda item: item["score"])[:4]
    strongest = sorted(scorecard, key=lambda item: item["score"], reverse=True)[:3]
    industry_profile = _ai_tech_debt_industry_profile(industry)
    company_name = _safe_action_text(company, 180) or "the company"
    title = _safe_action_text(respondent_title, 120) or "CIO/CTO"
    context_text = _ai_tech_debt_eval_text(business_context, 5000)
    if context_text:
        open_answers.insert(0, {"id": "cio_business_context", "dimension": "strategy", "answer": context_text})
    executive_summary = (
        f"{company_name} is graded {overall}% ({_ai_tech_debt_grade(overall)}) for AI readiness. "
        f"The assessment suggests {_ai_tech_debt_level(overall).lower()}. "
        f"For a {industry_profile['label']} environment, the strongest near-term value is a controlled pilot that proves business impact while establishing reusable AI governance."
    )
    coach_plan = [
        {
            "phase": "First 15 days",
            "focus": "Confirm executive outcome, map the pilot workflow, inventory data sources, and define risk gates.",
        },
        {
            "phase": "Days 16-45",
            "focus": "Build a controlled POC with retrieval, human approval, baseline metrics, and prompt/eval versioning.",
        },
        {
            "phase": "Days 46-90",
            "focus": "Run the pilot with users, measure value, tune controls, and prepare a production readiness decision.",
        },
    ]
    report = {
        "overall_score": overall,
        "overall_grade": _ai_tech_debt_grade(overall),
        "readiness_level": _ai_tech_debt_level(overall),
        "executive_summary": executive_summary,
        "scorecard": scorecard,
        "strongest_areas": strongest,
        "priority_debt": weakest,
        "high_level_recommendations": [item["recommendation"] for item in weakest],
        "recommended_pilot": {
            "title": f"{industry_profile['label']} AI DevReady Coach Pilot",
            "description": industry_profile["pilot"],
            "why": (
                "This is the best first move because it can be scoped as a controlled proof of concept, "
                "it forces data and workflow readiness into the open, and it gives leadership a measurable AI adoption path."
            ),
            "coach_role": (
                "An AI DevReady Coach should facilitate the POC, align the business and technology teams, "
                "define the guardrails, coach users through adoption, and turn the pilot into a repeatable playbook."
            ),
        },
        "coach_plan": coach_plan,
        "open_answer_signals": open_answers[:12],
        "prepared_for": title,
        "business_context": context_text,
        "external_research": research or {},
    }
    ai_context = _ai_tech_debt_ai_enhance_report(report, company_name, industry, context_text, research or {})
    if ai_context:
        report["ai_context_analysis"] = ai_context
        if ai_context.get("adjusted_recommendations"):
            report["high_level_recommendations"] = ai_context["adjusted_recommendations"][:6]
        if ai_context.get("pilot_focus"):
            report["recommended_pilot"]["description"] = ai_context["pilot_focus"]
        if ai_context.get("executive_next_step"):
            report["executive_next_step"] = ai_context["executive_next_step"]
    return report


@app.get("/api/ai-tech-debt/questionnaire")
def ai_tech_debt_questionnaire(
    domain: str = "dev",
    industry: str = "technology",
    company: str = "",
    company_website: str = "",
    include_external_research: str = "true",
    linkedin_profiles: str = "",
):
    include_external = str(include_external_research or "").strip().lower() in {"1", "true", "yes", "on"}
    clean_website = _ai_tech_debt_normalize_url(company_website)
    research = _ai_tech_debt_research(company, industry, clean_website, include_external, linkedin_profiles)
    data = _ai_tech_debt_questionnaire(industry, company, research=research, linkedin_profiles=linkedin_profiles)
    data["domain"] = _domain_key(domain)
    data["company_website"] = clean_website
    data["external_research"] = research
    return data


def _read_ai_tech_debt_links() -> dict:
    data = _read_json_store(AI_TECH_DEBT_LINKS_PATH, {"links": {}})
    if not isinstance(data, dict):
        data = {"links": {}}
    data.setdefault("links", {})
    if not isinstance(data["links"], dict):
        data["links"] = {}
    return data


def _write_ai_tech_debt_links(data: dict) -> None:
    data.setdefault("links", {})
    _write_json_store(AI_TECH_DEBT_LINKS_PATH, data)


def _ai_tech_debt_link_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/ui/pages/ai-tech-debt-survey.html?token={quote_plus(token)}"


def _ai_tech_debt_email_packet(link: dict, url: str) -> dict:
    company = link.get("company") or "your organization"
    title = link.get("recipient_title") or "executive"
    subject = f"AI Tech Debt Assessment for {company}"
    body = (
        f"Hi {link.get('recipient_name') or title},\n\n"
        "DevReady prepared a focused AI Tech Debt Assessment for your team. "
        "It takes about 15 to 20 minutes and helps identify AI readiness, governance, data, integration, security, "
        "workflow, and pilot opportunities.\n\n"
        f"Open your assessment link:\n{url}\n\n"
        "Please include any business priorities, current AI situation, competitor concerns, and focus areas in the "
        "open response field. Egeria will include that context in the final evaluation.\n\n"
        "Thank you,\nDevReady"
    )
    return {"subject": subject, "body": body}


@app.post("/api/ai-tech-debt/link")
def ai_tech_debt_create_link(
    request: Request,
    domain: str = Form(default="dev"),
    company: str = Form(default=""),
    industry: str = Form(default="technology"),
    company_website: str = Form(default=""),
    linkedin_profiles: str = Form(default=""),
    questionnaire_json: str = Form(default=""),
    recipient_name: str = Form(default=""),
    recipient_email: str = Form(default=""),
    recipient_title: str = Form(default="CIO"),
    include_external_research: str = Form(default="true"),
):
    email = _safe_action_text(recipient_email, 220).lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Recipient CTO/CIO email is required.")
    clean_domain = _domain_key(domain)
    token = _safe_token("AITD-LINK")
    include_external = str(include_external_research or "").strip().lower() in {"1", "true", "yes", "on"}
    clean_website = _ai_tech_debt_normalize_url(company_website)
    research = _ai_tech_debt_research(company, industry, clean_website, include_external, linkedin_profiles)
    fallback_questionnaire = _ai_tech_debt_questionnaire(industry, company, research=research, linkedin_profiles=linkedin_profiles)
    questionnaire = _ai_tech_debt_questionnaire_from_json(questionnaire_json, fallback_questionnaire)
    link = {
        "token": token,
        "domain": clean_domain,
        "company": _safe_action_text(company, 180) or "Unassigned client",
        "industry": industry,
        "industry_label": questionnaire.get("industry_label"),
        "company_website": clean_website,
        "linkedin_profiles": _safe_action_text(linkedin_profiles, 3000),
        "questionnaire": questionnaire,
        "recipient_name": _safe_action_text(recipient_name, 160),
        "recipient_email": email,
        "recipient_title": _safe_action_text(recipient_title, 120) or "CIO",
        "include_external_research": include_external,
        "status": "created",
        "created_at": _now_utc(),
        "updated_at": _now_utc(),
        "sent_at": "",
        "submitted_at": "",
        "assessment_id": "",
    }
    store = _read_ai_tech_debt_links()
    store["links"][token] = link
    _write_ai_tech_debt_links(store)
    url = _ai_tech_debt_link_url(request, token)
    packet = _ai_tech_debt_email_packet(link, url)
    return {"ok": True, "link": {**link, "url": url, "email_subject": packet["subject"], "email_body": packet["body"]}}


@app.post("/api/ai-tech-debt/link/{token}/mark-sent")
def ai_tech_debt_mark_link_sent(token: str):
    store = _read_ai_tech_debt_links()
    link = store["links"].get(token)
    if not link:
        raise HTTPException(status_code=404, detail="Assessment link not found.")
    link["status"] = "sent"
    link["sent_at"] = link.get("sent_at") or _now_utc()
    link["updated_at"] = _now_utc()
    store["links"][token] = link
    _write_ai_tech_debt_links(store)
    return {"ok": True, "link": link}


@app.get("/api/ai-tech-debt/link/{token}")
def ai_tech_debt_link_detail(request: Request, token: str):
    store = _read_ai_tech_debt_links()
    link = store["links"].get(token)
    if not link:
        raise HTTPException(status_code=404, detail="Assessment link not found.")
    research = _ai_tech_debt_research(
        link.get("company", ""),
        link.get("industry", "technology"),
        link.get("company_website", ""),
        bool(link.get("include_external_research")),
        link.get("linkedin_profiles", ""),
    )
    fallback_questionnaire = _ai_tech_debt_questionnaire(link.get("industry", "technology"), link.get("company", ""), research=research, linkedin_profiles=link.get("linkedin_profiles", ""))
    questionnaire = link.get("questionnaire") if isinstance(link.get("questionnaire"), dict) else fallback_questionnaire
    packet = _ai_tech_debt_email_packet(link, _ai_tech_debt_link_url(request, token))
    safe_link = {key: value for key, value in link.items() if key not in {"answers", "questionnaire"}}
    return {
        "ok": True,
        "link": {**safe_link, "url": _ai_tech_debt_link_url(request, token), "email_subject": packet["subject"], "email_body": packet["body"]},
        "questionnaire": questionnaire,
        "external_research": research,
    }


@app.post("/api/ai-tech-debt/link/{token}/submit")
def ai_tech_debt_link_submit(
    token: str,
    business_context: str = Form(default=""),
    answers_json: str = Form(default="{}"),
):
    store = _read_ai_tech_debt_links()
    link = store["links"].get(token)
    if not link:
        raise HTTPException(status_code=404, detail="Assessment link not found.")
    if link.get("status") == "submitted":
        raise HTTPException(status_code=409, detail="This assessment link has already been submitted.")
    try:
        answers = json.loads(answers_json or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="answers_json must be valid JSON.")
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="answers_json must be a JSON object.")
    company = link.get("company", "")
    industry = link.get("industry", "technology")
    research = _ai_tech_debt_research(
        company,
        industry,
        link.get("company_website", ""),
        bool(link.get("include_external_research")),
        link.get("linkedin_profiles", ""),
    )
    fallback_questionnaire = _ai_tech_debt_questionnaire(industry, company, research=research, linkedin_profiles=link.get("linkedin_profiles", ""))
    questionnaire = link.get("questionnaire") if isinstance(link.get("questionnaire"), dict) else fallback_questionnaire
    report = _ai_tech_debt_evaluate(
        questionnaire,
        answers,
        company,
        industry,
        link.get("recipient_title", "CIO"),
        business_context=business_context,
        research=research,
    )
    record = {
        "id": _safe_token("AITD"),
        "source_link_token": token,
        "domain": _domain_key(link.get("domain", "dev")),
        "company": _safe_action_text(company, 180) or "Unassigned client",
        "industry": industry,
        "industry_label": questionnaire.get("industry_label"),
        "respondent_name": _safe_action_text(link.get("recipient_name", ""), 160),
        "respondent_title": _safe_action_text(link.get("recipient_title", ""), 120),
        "respondent_email": _safe_action_text(link.get("recipient_email", ""), 220),
        "company_website": link.get("company_website", ""),
        "linkedin_profiles": link.get("linkedin_profiles", ""),
        "business_context": _ai_tech_debt_eval_text(business_context, 5000),
        "external_research": research,
        "answers": answers,
        "report": report,
        "created_at": _now_utc(),
    }
    assessments = _read_json_store(AI_TECH_DEBT_ASSESSMENTS_PATH, [])
    if not isinstance(assessments, list):
        assessments = []
    assessments.append(record)
    _write_json_store(AI_TECH_DEBT_ASSESSMENTS_PATH, assessments[-500:])
    link["status"] = "submitted"
    link["submitted_at"] = _now_utc()
    link["updated_at"] = _now_utc()
    link["assessment_id"] = record["id"]
    store["links"][token] = link
    _write_ai_tech_debt_links(store)
    return {
        "ok": True,
        "assessment": {
            "id": record["id"],
            "company": record["company"],
            "industry_label": record["industry_label"],
            "overall_score": report.get("overall_score"),
            "overall_grade": report.get("overall_grade"),
            "executive_summary": report.get("executive_summary"),
        },
    }


@app.post("/api/ai-tech-debt/submit")
def ai_tech_debt_submit(
    domain: str = Form(default="dev"),
    company: str = Form(default=""),
    industry: str = Form(default="technology"),
    respondent_name: str = Form(default=""),
    respondent_title: str = Form(default=""),
    company_website: str = Form(default=""),
    linkedin_profiles: str = Form(default=""),
    questionnaire_json: str = Form(default=""),
    business_context: str = Form(default=""),
    include_external_research: str = Form(default="false"),
    answers_json: str = Form(default="{}"),
):
    clean_domain = _domain_key(domain)
    try:
        answers = json.loads(answers_json or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="answers_json must be valid JSON.")
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="answers_json must be a JSON object.")
    include_external = str(include_external_research or "").strip().lower() in {"1", "true", "yes", "on"}
    clean_website = _ai_tech_debt_normalize_url(company_website)
    research = _ai_tech_debt_research(company, industry, clean_website, include_external, linkedin_profiles)
    fallback_questionnaire = _ai_tech_debt_questionnaire(industry, company, research=research, linkedin_profiles=linkedin_profiles)
    questionnaire = _ai_tech_debt_questionnaire_from_json(questionnaire_json, fallback_questionnaire)
    report = _ai_tech_debt_evaluate(
        questionnaire,
        answers,
        company,
        industry,
        respondent_title,
        business_context=business_context,
        research=research,
    )
    record = {
        "id": _safe_token("AITD"),
        "domain": clean_domain,
        "company": _safe_action_text(company, 180) or "Unassigned client",
        "industry": industry,
        "industry_label": questionnaire.get("industry_label"),
        "respondent_name": _safe_action_text(respondent_name, 160),
        "respondent_title": _safe_action_text(respondent_title, 120),
        "company_website": clean_website,
        "linkedin_profiles": _safe_action_text(linkedin_profiles, 3000),
        "business_context": _ai_tech_debt_eval_text(business_context, 5000),
        "external_research": research,
        "answers": answers,
        "report": report,
        "created_at": _now_utc(),
    }
    store = _read_json_store(AI_TECH_DEBT_ASSESSMENTS_PATH, [])
    if not isinstance(store, list):
        store = []
    store.append(record)
    _write_json_store(AI_TECH_DEBT_ASSESSMENTS_PATH, store[-500:])
    return {"ok": True, "assessment": record}


@app.get("/api/ai-tech-debt/assessments")
def ai_tech_debt_assessments(domain: str = "dev", limit: int = 25):
    clean_domain = _domain_key(domain)
    rows = _read_json_store(AI_TECH_DEBT_ASSESSMENTS_PATH, [])
    if not isinstance(rows, list):
        rows = []
    filtered = [row for row in rows if clean_domain == "all" or _domain_key(row.get("domain", "dev")) == clean_domain]
    filtered.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"ok": True, "domain": clean_domain, "assessments": filtered[: max(1, min(int(limit or 25), 100))]}


def _radar_words(value: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}", str(value or "").lower())
    stop = {
        "and", "the", "for", "with", "from", "that", "this", "will", "are", "our", "you",
        "your", "their", "role", "client", "candidate", "experience", "years", "using",
        "work", "team", "teams", "project", "projects", "system", "systems",
    }
    return {word.strip(".-") for word in words if word not in stop and len(word) > 2}


def _radar_skill_terms(skills) -> list[str]:
    terms = []
    if isinstance(skills, dict):
        for value in skills.values():
            if isinstance(value, list):
                terms.extend(str(item) for item in value if str(item or "").strip())
            elif value:
                terms.append(str(value))
    elif isinstance(skills, list):
        for item in skills:
            if isinstance(item, dict):
                terms.append(str(item.get("title") or item.get("skill") or ""))
            else:
                terms.append(str(item or ""))
    return [_safe_action_text(item, 80) for item in terms if _safe_action_text(item, 80)]


def _radar_profile_text(profile: dict) -> str:
    parts = []
    contact = profile.get("contact", {}) or {}
    summary = profile.get("summary", {}) or {}
    parts.extend([contact.get("full_name", ""), contact.get("email", ""), summary.get("headline", ""), summary.get("overview", "")])
    for item in _radar_skill_terms(profile.get("skills", {})):
        parts.append(item)
    for item in profile.get("experience", []) or []:
        if not isinstance(item, dict):
            continue
        parts.extend([
            item.get("company", ""),
            item.get("title", ""),
            item.get("mainrole", ""),
            item.get("summary", ""),
            item.get("description", ""),
        ])
        for bullet in item.get("bullets", []) or []:
            parts.append(str(bullet))
    return " ".join(str(part or "") for part in parts)


def _radar_profiles(domain: str, limit: int = 80) -> list[dict]:
    rows = []
    for key, db_path in _domain_db_items(domain):
        try:
            page = storage.search_profiles_full(
                db_path,
                domain=_storage_domain(key),
                search_string="",
                currentPage=0,
                pageLimit=max(10, min(limit, 200)),
            )
        except Exception:
            page = []
        for row in page:
            data = row.get("data") if isinstance(row, dict) else {}
            if not isinstance(data, dict):
                continue
            meta = data.get("meta", {}) or {}
            contact = data.get("contact", {}) or {}
            profile_id = str(meta.get("profile_id") or row.get("profile_id") or "")
            name = _safe_action_text(contact.get("full_name") or row.get("full_name"), 180)
            if not profile_id or not name:
                continue
            rows.append(
                {
                    "profile_id": profile_id,
                    "name": name,
                    "email": _safe_action_text(contact.get("email") or row.get("email"), 240),
                    "headline": _safe_action_text((data.get("summary", {}) or {}).get("headline"), 220),
                    "domain": key,
                    "skills": _radar_skill_terms(data.get("skills", {})),
                    "text": _radar_profile_text(data),
                    "profile": data,
                }
            )
    return rows[: max(1, min(limit, 200))]


def _radar_jds(domain: str, limit: int = 12) -> list[dict]:
    rows = []
    for key, db_path in _domain_db_items(domain):
        try:
            summaries = storage.list_jds(db_path, domain=_storage_domain(key))
        except Exception:
            summaries = []
        for summary in summaries[: max(1, min(limit, 50))]:
            jd_id = str(summary.get("jd_id") or "")
            if not jd_id:
                continue
            jd = storage.get_jd(db_path, jd_id) or summary
            jd["domain"] = _domain_key(jd.get("domain") or key)
            rows.append(jd)
    return rows[: max(1, min(limit, 50))]


def _radar_crm_records(domain: str, limit: int = 80) -> list[dict]:
    records = _atlas_crm_records_db(domain, include_archived=False, limit=max(limit, 200))
    if records is None:
        records = _read_json_store_with_demo(CRM_RECORDS_PATH, [])
    clean_domain = _domain_key(domain)
    if not isinstance(records, list):
        return []
    rows = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if _crm_record_archived(item):
            continue
        if clean_domain != "all" and _domain_key(item.get("domain", "dev")) != clean_domain:
            continue
        rows.append(item)
    rows.sort(key=lambda item: item.get("updatedAt") or item.get("createdAt") or item.get("when") or "", reverse=True)
    return rows[: max(1, min(limit, 200))]


def _radar_stage_weight(record: dict) -> int:
    stage = _safe_action_text(record.get("dealStage") or record.get("contractStatus") or record.get("status"), 80).lower()
    score = 0.0
    if any(word in stage for word in ["urgent", "qualified", "proposal", "active", "ready"]):
        score += 6
    elif any(word in stage for word in ["discovery", "building", "warm"]):
        score += 4
    elif any(word in stage for word in ["stalled", "risk", "needs"]):
        score += 5
    else:
        score += 2
    try:
        strength = float(record.get("strength") or record.get("heat") or 0)
    except Exception:
        strength = 0
    score += min(5, max(0, strength) * 0.55)
    try:
        days = _crm_days_since(record.get("lastTouched") or record.get("when"))
    except Exception:
        days = 0
    score += min(3, max(0, days) * 0.2)
    return int(round(min(14, score)))


def _radar_customer_for_jd(jd: dict, crm_records: list[dict]) -> dict:
    company = _safe_action_text(jd.get("company"), 240).lower()
    if not company:
        return {}
    for record in crm_records:
        customer = _safe_action_text(record.get("customer"), 240).lower()
        if customer and (customer in company or company in customer):
            return record
    return {}


def _radar_skill_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", "", str(value or "").lower())


def _radar_skill_related(needle: str, haystack: str) -> bool:
    clean_needle = _radar_skill_norm(needle)
    clean_haystack = _radar_skill_norm(haystack)
    if not clean_needle or not clean_haystack:
        return False
    return clean_needle in clean_haystack or clean_haystack in clean_needle


def _radar_weighted_skill_score(jd_terms: list[str], candidate_skills: list[str], base_rank: float = 0, crm_weight: float = 0) -> dict:
    required = [_safe_action_text(term, 80) for term in jd_terms if _safe_action_text(term, 80)]
    candidate = [_safe_action_text(skill, 120) for skill in candidate_skills if _safe_action_text(skill, 120)]
    exact = []
    related = []
    gaps = []
    for term in required:
        exact_hit = next((skill for skill in candidate if _radar_skill_norm(skill) == _radar_skill_norm(term)), "")
        if exact_hit:
            exact.append(term)
            continue
        related_hit = next((skill for skill in candidate if _radar_skill_related(term, skill)), "")
        if related_hit:
            related.append(related_hit)
        else:
            gaps.append(term)
    exact_score = (len(exact) / max(1, len(required))) * 62
    related_score = min(18, len(set(_radar_skill_norm(item) for item in related)) * 4.5)
    richness_score = min(8, len(set(_radar_skill_norm(item) for item in candidate)) * 0.25)
    rank_score = min(8, max(0, float(base_rank or 0)) / 5)
    crm_score = min(4, max(0, float(crm_weight or 0)) / 3.5)
    score = round(min(99, exact_score + related_score + richness_score + rank_score + crm_score), 1)
    matched = []
    for item in exact + related:
        if item and item not in matched:
            matched.append(item)
    return {
        "score": score,
        "exact": exact,
        "matched": matched[:8],
        "gaps": gaps[:6],
        "score_parts": {
            "exact_required": round(exact_score, 1),
            "related": round(related_score, 1),
            "skill_depth": round(richness_score, 1),
            "search_rank": round(rank_score, 1),
            "crm_signal": round(crm_score, 1),
        },
    }


def _radar_title_alignment(jd: dict, candidate_text: str) -> float:
    title_words = _radar_words(jd.get("title", ""))
    candidate_words = _radar_words(candidate_text)
    overlap = title_words & candidate_words
    return round(min(6, len(overlap) * 1.5), 1)


def _radar_process_score(value: str) -> float:
    clean = _safe_action_text(value, 100).lower()
    if "certified" in clean:
        return 3.0
    if "complete" in clean or "ready" in clean:
        return 2.0
    if "review" in clean or "progress" in clean:
        return 1.0
    return 0.0


def _radar_match_profile_to_jd(profile: dict, jd: dict) -> dict:
    jd_skills = jd.get("jd_skills") if isinstance(jd.get("jd_skills"), dict) else {}
    profile_skills = (profile.get("profile", {}) or {}).get("skills", {})
    jd_terms = _radar_skill_terms(jd_skills) or sorted(_radar_words(jd.get("jd_text", "")))[:18]
    if jd_skills and isinstance(profile_skills, dict):
        try:
            score, parts = match(profile_skills, jd_skills)
            matched = top_matches_from_parts(parts, limit=8)
            gaps = []
            for group in ["backend", "frontend", "cloud_devops", "data", "testing", "security", "languages"]:
                gaps.extend(((parts.get(group, {}) or {}).get("missing", []) or [])[:2])
            weighted = _radar_weighted_skill_score(jd_terms, _radar_skill_terms(profile_skills))
            role_score = _radar_title_alignment(jd, f"{profile.get('headline', '')} {profile.get('text', '')}")
            weighted["score_parts"]["role_title"] = role_score
            return {
                "score": min(99, max(float(score or 0), weighted["score"]) + role_score),
                "matched": matched or weighted["matched"],
                "gaps": gaps[:6] or weighted["gaps"],
                "score_parts": weighted.get("score_parts", {}),
                "exact": weighted.get("exact", []),
            }
        except Exception:
            pass
    weighted = _radar_weighted_skill_score(jd_terms, profile.get("skills", []))
    role_score = _radar_title_alignment(jd, f"{profile.get('headline', '')} {profile.get('text', '')}")
    weighted["score"] = min(99, round(weighted["score"] + role_score, 1))
    weighted["score_parts"]["role_title"] = role_score
    return weighted


def _radar_external_people_suggestions(jd: dict, clean_domain: str, customer: dict, customer_weight: int, limit: int = 5) -> list[dict]:
    if not os.getenv("PDL_API_KEY"):
        return []
    jd_skill_terms = _radar_skill_terms(jd.get("jd_skills", {}))[:12]
    if not jd_skill_terms:
        return []
    try:
        people = peopleDataLabs.searchSkills(jd_skill_terms, size=max(1, min(limit, 10))).get("data", [])
    except Exception:
        people = []
    suggestions = []
    for row in people[: max(1, min(limit, 10))]:
        if not isinstance(row, dict):
            continue
        skills = [str(skill) for skill in (row.get("skills") or []) if str(skill or "").strip()]
        scored = _radar_weighted_skill_score(jd_skill_terms, skills, base_rank=0, crm_weight=customer_weight)
        role_score = _radar_title_alignment(jd, f"{row.get('job_title', '')} {row.get('headline', '')} {row.get('summary', '')}")
        scored["score"] = min(95, round(scored["score"] + role_score, 1))
        scored["score_parts"]["role_title"] = role_score
        if scored["score"] < 25 and len(scored["matched"]) < 2:
            continue
        first = _safe_action_text(row.get("first_name"), 100)
        last = _safe_action_text(row.get("last_name"), 120)
        full_name = _safe_action_text(row.get("full_name") or f"{first} {last}", 220)
        suggestions.append(
            {
                "score": round(min(95, scored["score"] - 3), 1),
                "domain": clean_domain,
                "source": "people_data_labs",
                "source_label": "External People Data",
                "candidate": {
                    "profile_id": _safe_action_text(row.get("id"), 140),
                    "name": full_name or "External candidate",
                    "email": _safe_action_text(row.get("work_email") or row.get("recommended_personal_email"), 240),
                    "headline": _safe_action_text(row.get("job_title") or row.get("headline"), 220),
                    "linkedin_url": _safe_action_text(row.get("linkedin_url"), 400),
                },
                "job": {
                    "jd_id": jd.get("jd_id", ""),
                    "company": _safe_action_text(jd.get("company"), 180),
                    "title": _safe_action_text(jd.get("title"), 220),
                },
                "client": {
                    "id": _safe_action_text(customer.get("id"), 120),
                    "name": _safe_action_text(customer.get("customer"), 180),
                    "stage": _safe_action_text(customer.get("dealStage") or customer.get("contractStatus"), 120),
                    "owner": _safe_action_text(customer.get("owner"), 120),
                } if customer else {},
                "matched": scored["matched"],
                "gaps": scored["gaps"],
                "score_parts": scored["score_parts"],
                "reason": "External People Data prospect. Matched: " + ", ".join(scored["matched"][:5]),
                "next_action": "Review as a temporary outside prospect before creating a permanent profile.",
                "links": {
                    "profile": f"mine-candidate-external.html?domain={clean_domain}",
                    "match": f"mine-candidate-external.html?domain={clean_domain}",
                    "client_comm": f"client-comm.html?domain={clean_domain}",
                },
            }
        )
    return suggestions


def _radar_sort_key(item: dict):
    source = item.get("source") or "local_profile"
    source_order = 1 if source == "people_data_labs" else 0
    return (source_order, -float(item.get("score") or 0))


def _radar_source_counts(suggestions: list[dict]) -> dict:
    return {
        "internal": len([item for item in suggestions if item.get("source") != "people_data_labs"]),
        "external": len([item for item in suggestions if item.get("source") == "people_data_labs"]),
    }


def _external_radar_status() -> dict:
    return {
        "people_data": {
            "ready": bool(os.getenv("PDL_API_KEY")),
            "label": "People Data Labs",
            "action": "Use Find Candidates (Out) or enable include_external for sourced prospects.",
        },
        "github": {
            "ready": bool(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")),
            "label": "GitHub",
            "action": "Ready for a code-footprint scanner when a token is configured.",
        },
        "news": {
            "ready": True,
            "label": "Client news scan",
            "action": "Atlas news scan can check recent public web/news signals for selected customers.",
        },
    }


def _ai_radar_narrative(domain: str, suggestions: list[dict], crm_records: list[dict]) -> dict:
    fallback = {
        "headline": "Egeria: Opportunity found cross-system candidate and client signals.",
        "brief": "Review the highest-scoring suggestion, confirm candidate interest, then prepare a client-ready shortlist.",
        "sales_angle": "Use the reason and gaps to guide the next sales or recruiter touch.",
    }
    if not suggestions:
        return {
            "headline": "Egeria: Opportunity did not find a strong match yet.",
            "brief": "Load or normalize more job descriptions, then run the radar again.",
            "sales_angle": "If internal matches are thin, route the role to outside sourcing.",
        }
    try:
        client = getOpenAPIClient()
        payload = json.dumps(
            {
                "domain": _domain_key(domain),
                "suggestions": suggestions[:5],
                "crm_sample": [
                    {
                        "customer": item.get("customer"),
                        "stage": item.get("dealStage") or item.get("contractStatus"),
                        "nextStep": item.get("nextStep"),
                    }
                    for item in crm_records[:5]
                ],
            },
            ensure_ascii=False,
        )
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Egeria: Opportunity for VETCODE. Return compact JSON with "
                        "headline, brief, and sales_angle. Use only the provided data. Do not invent "
                        "candidate facts, client facts, compensation, or promises."
                    ),
                },
                {"role": "user", "content": payload},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        found = re.search(r"\{.*\}", content, re.S)
        parsed = json.loads(found.group(0) if found else content)
        return {
            "headline": _safe_action_text(parsed.get("headline"), 220) or fallback["headline"],
            "brief": _safe_action_text(parsed.get("brief"), 420) or fallback["brief"],
            "sales_angle": _safe_action_text(parsed.get("sales_angle"), 360) or fallback["sales_angle"],
        }
    except Exception:
        return fallback


@app.get("/api/ai/opportunity-radar")
def opportunity_radar(domain: str = "dev", limit: int = 5, include_external: bool = False):
    clean_domain = _domain_key(domain)
    jds = _radar_jds(clean_domain, limit=12)
    profiles = _radar_profiles(clean_domain, limit=100)
    crm_records = _radar_crm_records(clean_domain, limit=120)
    suggestions = []
    for jd in jds[:10]:
        customer = _radar_customer_for_jd(jd, crm_records)
        customer_weight = _radar_stage_weight(customer) if customer else 0
        for profile in profiles:
            result = _radar_match_profile_to_jd(profile, jd)
            score_parts = dict(result.get("score_parts") or {})
            score_parts["crm_signal"] = round(min(4, max(0, float(customer_weight or 0)) / 3.5), 1)
            score = min(99, round(float(result.get("score", 0)) + customer_weight, 1))
            if score < 25 and len(result.get("matched", [])) < 2:
                continue
            matched = result.get("matched", [])[:8]
            gaps = result.get("gaps", [])[:6]
            reason_bits = []
            if matched:
                reason_bits.append("Matched: " + ", ".join(matched[:5]))
            if customer:
                reason_bits.append(f"Atlas signal: {customer.get('customer')} is {customer.get('dealStage') or customer.get('contractStatus') or 'active'}")
            if gaps:
                reason_bits.append("Check gaps: " + ", ".join(gaps[:3]))
            suggestions.append(
                {
                    "score": score,
                    "domain": clean_domain,
                    "candidate": {
                        "profile_id": profile["profile_id"],
                        "name": profile["name"],
                        "email": profile["email"],
                        "headline": profile["headline"],
                    },
                    "job": {
                        "jd_id": jd.get("jd_id", ""),
                        "company": _safe_action_text(jd.get("company"), 180),
                        "title": _safe_action_text(jd.get("title"), 220),
                    },
                    "client": {
                        "id": _safe_action_text(customer.get("id"), 120),
                        "name": _safe_action_text(customer.get("customer"), 180),
                        "stage": _safe_action_text(customer.get("dealStage") or customer.get("contractStatus"), 120),
                        "owner": _safe_action_text(customer.get("owner"), 120),
                    } if customer else {},
                    "matched": matched,
                    "gaps": gaps,
                    "score_parts": score_parts,
                    "source": "local_profile_database",
                    "source_label": "Internal Profile",
                    "reason": ". ".join(reason_bits) or "Candidate and role show overlapping evidence.",
                    "next_action": "Send role feedback link, then run candidate review before shortlist.",
                    "links": {
                        "profile": f"profile-preview.html?domain={clean_domain}&profileId={profile['profile_id']}",
                        "match": f"match-role.html?domain={clean_domain}&jdId={jd.get('jd_id', '')}",
                        "client_comm": f"client-comm.html?domain={clean_domain}",
                    },
                }
            )
        jd_skill_terms = _radar_skill_terms(jd.get("jd_skills", {}))[:16]
        if jd_skill_terms:
            try:
                azure_rows = candidates.searchCandidatesBySkills(",".join(jd_skill_terms), 8, domain=clean_domain)
            except Exception:
                azure_rows = []
            for row in azure_rows:
                candidate_id = str(row.get("id") or "")
                if not candidate_id:
                    continue
                try:
                    match_score, parts = azureMatch(row.get("skillMatches", []), jd.get("jd_skills", {}))
                    matched = top_matches_from_parts(parts, limit=8) or (row.get("skillMatches", []) or [])[:8]
                    gaps = []
                    for group in ["backend", "frontend", "cloud_devops", "data", "testing", "security", "languages"]:
                        gaps.extend(((parts.get(group, {}) or {}).get("missing", []) or [])[:2])
                    weighted = _radar_weighted_skill_score(
                        jd_skill_terms,
                        row.get("skillMatches", []) or [],
                        base_rank=row.get("searchRank") or row.get("skillCount") or 0,
                        crm_weight=customer_weight,
                    )
                    role_score = _radar_title_alignment(jd, f"{row.get('primaryStack', '')} {row.get('firstName', '')} {row.get('lastName', '')}")
                    process_score = _radar_process_score(row.get("step"))
                    weighted["score"] = min(99, round(weighted["score"] + role_score + process_score, 1))
                    weighted["score_parts"]["role_title"] = role_score
                    weighted["score_parts"]["process"] = process_score
                    match_score = max(float(match_score or 0), weighted["score"])
                    matched = weighted["matched"] or matched
                    gaps = weighted["gaps"] or gaps
                    score_parts = weighted.get("score_parts", {})
                except Exception:
                    matched = [skill for skill in (row.get("skillMatches", []) or []) if skill][:8]
                    weighted = _radar_weighted_skill_score(jd_skill_terms, matched, base_rank=row.get("searchRank") or row.get("skillCount") or 0, crm_weight=customer_weight)
                    role_score = _radar_title_alignment(jd, f"{row.get('primaryStack', '')} {row.get('firstName', '')} {row.get('lastName', '')}")
                    process_score = _radar_process_score(row.get("step"))
                    weighted["score"] = min(99, round(weighted["score"] + role_score + process_score, 1))
                    weighted["score_parts"]["role_title"] = role_score
                    weighted["score_parts"]["process"] = process_score
                    gaps = weighted["gaps"]
                    match_score = weighted["score"]
                    score_parts = weighted.get("score_parts", {})
                score = min(99, round(float(match_score or 0), 1))
                if score < 25 and len(matched) < 2:
                    continue
                candidate_name = _safe_action_text(f"{row.get('firstName', '')} {row.get('lastName', '')}", 180)
                suggestions.append(
                    {
                        "score": score,
                        "domain": clean_domain,
                        "source": "azure_candidate_database",
                        "source_label": "Internal Candidate",
                        "candidate": {
                            "profile_id": candidate_id,
                            "name": candidate_name,
                            "email": _safe_action_text(row.get("email"), 240),
                            "headline": _safe_action_text(row.get("primaryStack"), 220),
                        },
                        "job": {
                            "jd_id": jd.get("jd_id", ""),
                            "company": _safe_action_text(jd.get("company"), 180),
                            "title": _safe_action_text(jd.get("title"), 220),
                        },
                        "client": {
                            "id": _safe_action_text(customer.get("id"), 120),
                            "name": _safe_action_text(customer.get("customer"), 180),
                            "stage": _safe_action_text(customer.get("dealStage") or customer.get("contractStatus"), 120),
                            "owner": _safe_action_text(customer.get("owner"), 120),
                        } if customer else {},
                        "matched": matched[:8],
                        "gaps": gaps[:6],
                        "score_parts": score_parts,
                        "reason": ". ".join(
                            [
                                "Azure candidate database match: " + ", ".join(matched[:5]) if matched else "",
                                f"Atlas signal: {customer.get('customer')} is {customer.get('dealStage') or customer.get('contractStatus') or 'active'}" if customer else "",
                                "Check gaps: " + ", ".join(gaps[:3]) if gaps else "",
                            ]
                        ).strip(". ") or "Candidate and role show overlapping database evidence.",
                        "next_action": "Open profile, confirm interest with a role-feedback link, then run candidate review before shortlist.",
                        "links": {
                            "profile": f"profile-preview.html?domain={clean_domain}&profileId={candidate_id}",
                            "match": f"match-role.html?domain={clean_domain}&jdId={jd.get('jd_id', '')}",
                            "client_comm": f"client-comm.html?domain={clean_domain}",
                        },
                    }
                )
        if include_external:
            suggestions.extend(_radar_external_people_suggestions(jd, clean_domain, customer, customer_weight, limit=5))
    suggestions.sort(key=_radar_sort_key)
    seen_suggestions = set()
    unique_suggestions = []
    for item in suggestions:
        candidate_id = str((item.get("candidate") or {}).get("profile_id") or "")
        jd_id = str((item.get("job") or {}).get("jd_id") or "")
        key = (item.get("source") or "internal", candidate_id, jd_id)
        if key in seen_suggestions:
            continue
        seen_suggestions.add(key)
        unique_suggestions.append(item)
    suggestions = unique_suggestions
    internal_suggestions = [item for item in suggestions if item.get("source") != "people_data_labs"]
    external_suggestions = [item for item in suggestions if item.get("source") == "people_data_labs"]
    safe_limit = max(1, min(limit, 12))
    suggestions = internal_suggestions[:safe_limit] + (external_suggestions[:safe_limit] if include_external else [])
    external = _external_radar_status()
    if include_external and jds:
        external["requested"] = True
        external["seed_job"] = {
            "jd_id": jds[0].get("jd_id", ""),
            "company": _safe_action_text(jds[0].get("company"), 180),
            "title": _safe_action_text(jds[0].get("title"), 220),
            "skills": _radar_skill_terms(jds[0].get("jd_skills", {}))[:12],
        }
    narrative = _ai_radar_narrative(clean_domain, suggestions, crm_records)
    return {
        "ok": True,
        "domain": clean_domain,
        "generated_at": _now_utc(),
        "counts": {
            "profiles_scanned": len(profiles),
            "jobs_scanned": len(jds),
            "crm_records_scanned": len(crm_records),
            "suggestions": len(suggestions),
            "sources": _radar_source_counts(suggestions),
        },
        "narrative": narrative,
        "suggestions": suggestions,
        "external": external,
    }


def _split_action_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in _safe_action_text(full_name, 180).split(" ") if part]
    if not parts:
        raise HTTPException(status_code=400, detail="Profile name is required.")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _normalize_agent_skills(skills) -> list[dict]:
    clean = []
    if not isinstance(skills, list):
        return clean
    seen = set()
    for skill in skills[:30]:
        if isinstance(skill, dict):
            title = _safe_action_text(skill.get("title") or skill.get("skill"), 100)
            years = skill.get("years") or 1
        else:
            title = _safe_action_text(skill, 100)
            years = 1
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        try:
            years = int(years)
        except Exception:
            years = 1
        clean.append({"title": title, "years": max(1, min(years, 40))})
    return clean


def _execute_numa_profile_action(action: dict, context: dict) -> dict:
    action_type = action.get("type")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    domain = _domain_key(payload.get("domain") or context.get("domain") or "dev")
    if action_type == "create_profile":
        full_name = _safe_action_text(payload.get("full_name") or payload.get("name"), 180)
        if not full_name:
            raise HTTPException(status_code=400, detail="Profile name is required.")
        result = candidates.uploadProfile(
            skills=_normalize_agent_skills(payload.get("skills")),
            fullName=full_name,
            candidateDescription=_safe_action_text(payload.get("description"), 2400) or "Numa-created profile. Add resume or confirmed details before client use.",
            domain=domain,
            email=_safe_action_text(payload.get("email"), 240) or None,
            linkedInUrl=_safe_action_text(payload.get("linkedin_url") or payload.get("linkedinUrl"), 400) or None,
            candidateCity=_safe_action_text(payload.get("city"), 160) or None,
            candidateState=_safe_action_text(payload.get("state"), 160) or None,
            candidateCountry=_safe_action_text(payload.get("country"), 160) or None,
            candidateTitle=_safe_action_text(payload.get("title") or payload.get("job_title"), 200) or None,
        )
        profile_id = str(result.get("personid") or "")
        return {
            "ok": True,
            "type": action_type,
            "message": f"Numa created profile {profile_id} for {full_name}.",
            "profile_id": profile_id,
            "profile_name": full_name,
            "profile_email": _safe_action_text(payload.get("email"), 240),
            "profile_url": f"profile-preview.html?domain={domain}&profileId={profile_id}",
            "result": result,
        }

    if action_type == "create_job_description":
        company = _safe_action_text(payload.get("company") or payload.get("client"), 180)
        title = _safe_action_text(payload.get("job_title") or payload.get("title"), 220)
        jd_text = _safe_action_text(payload.get("jd_text") or payload.get("description"), 8000)
        if not company or not title or not jd_text:
            raise HTTPException(status_code=400, detail="Company, job title, and job description text are required.")
        try:
            flat_skills = list(dict.fromkeys(normalize_all_skills(jd_text)))
        except Exception:
            flat_skills = []
        created = jobs.uploadJob(company, title, domain, jd_text, flat_skills) or {}
        jd_id = str(created.get("jd_id") or "")
        return {
            "ok": True,
            "type": action_type,
            "message": f"Numa added JD {jd_id} for {company} - {title}.",
            "jd_id": jd_id,
            "company": company,
            "job_title": title,
            "job_url": f"job-descriptions.html?domain={domain}",
            "result": {"jd_id": jd_id, "company": company, "title": title, "domain": domain, "skills": flat_skills},
        }

    if action_type == "update_profile_core":
        profile_id = _safe_action_text(payload.get("profile_id") or context.get("candidateId"), 80)
        if not profile_id:
            raise HTTPException(status_code=400, detail="Profile ID is required for updates.")
        existing = candidates.getProfile(profile_id)
        existing_profile = existing.get("profile", {}) if isinstance(existing, dict) else {}
        first_name, last_name = _split_action_name(
            payload.get("full_name")
            or " ".join(
                [
                    _safe_action_text(payload.get("first_name") or existing_profile.get("firstName"), 100),
                    _safe_action_text(payload.get("last_name") or existing_profile.get("lastName"), 100),
                ]
            )
        )
        candidates.updateCandidateCore(
            personId=profile_id,
            firstName=first_name,
            lastName=last_name,
            city=_safe_action_text(payload.get("city") if payload.get("city") is not None else existing_profile.get("city"), 160),
            state=_safe_action_text(payload.get("state") if payload.get("state") is not None else existing_profile.get("state"), 160),
            country=_safe_action_text(payload.get("country") if payload.get("country") is not None else existing_profile.get("country"), 160),
            description=_safe_action_text(payload.get("description") if payload.get("description") is not None else existing_profile.get("description"), 3000),
            jobTitle=_safe_action_text(payload.get("title") or payload.get("job_title") or existing_profile.get("title"), 200),
        )
        if payload.get("email") is not None:
            candidates.updateCandidateEmail(profile_id, _safe_action_text(payload.get("email"), 240))
        skills = _normalize_agent_skills(payload.get("skills"))
        if skills:
            candidates.replaceCandidateSkills(profile_id, skills)
        return {
            "ok": True,
            "type": action_type,
            "message": f"Numa updated profile {profile_id}.",
            "profile_id": profile_id,
            "profile_name": " ".join([first_name, last_name]).strip(),
            "profile_url": f"profile-preview.html?domain={domain}&profileId={profile_id}",
            "updated_skills": len(skills),
        }

    raise HTTPException(status_code=400, detail="Unsupported Numa action.")


@app.post("/api/agents/ask")
def ask_agent(
    agent_key: str = Form(default="talent"),
    message: str = Form(default=""),
    context_json: str = Form(default="{}"),
    domain: str = Form(default="dev"),
    admin_token: str = Form(default=""),
    numa_change_mode: str = Form(default="off"),
):
    context = _agent_context_with_access(context_json, domain, admin_token, numa_change_mode)
    try:
        return pageAgents.ask_page_agent(agent_key, message, context)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "agent": pageAgents.get_agent(agent_key),
                "answer": f"Agent response failed: {exc}",
            },
        )


@app.post("/api/agents/action")
def run_agent_action(
    action_json: str = Form(default="{}"),
    context_json: str = Form(default="{}"),
    domain: str = Form(default="dev"),
    admin_token: str = Form(default=""),
    numa_change_mode: str = Form(default="off"),
):
    context = _agent_context_with_access(context_json, domain, admin_token, numa_change_mode)
    _require_numa_action_access(context)
    try:
        action = json.loads(action_json or "{}")
        if not isinstance(action, dict):
            action = {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid action JSON.")
    try:
        return _execute_numa_profile_action(action, context)
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/access/login")
def access_login(
    username: str = Form(default=""),
    email: str = Form(default=""),
    password: str = Form(default=""),
    domain: str = Form(default="dev"),
):
    users = _seed_access_users()
    username = (username or "").strip()
    email = (email or "").strip()
    if not username and not email:
        raise HTTPException(status_code=400, detail="Enter a username or email.")
    if not password:
        raise HTTPException(status_code=400, detail="Enter your password.")

    user = _find_access_user(users, username=username, email=email)
    now = _now_utc()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found. Create an account first.")
    if user and user.get("status") == "blocked":
        raise HTTPException(status_code=403, detail="This user is blocked. Contact a DevReady admin.")
    if not _verify_password(password, user.get("password_hash", "")):
        raise HTTPException(status_code=403, detail="Incorrect username/email or password.")
    _refresh_candidate_user_link(user, domain)
    user["last_login_at"] = now
    user["updated_at"] = now
    _write_json_store(ACCESS_USERS_PATH, users)
    public_user = _public_user(user)
    public_user["allowed_menu"] = _domain_menu_keys(public_user.get("allowed_menu", []), domain)
    return {"ok": True, "user": public_user, "menu_items": _domain_menu_items(domain)}


@app.post("/api/access/admin-login")
def access_admin_login(
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    users = _seed_access_users()
    username = (username or "").strip()
    if _normalize_user_key(username) != "administrator":
        raise HTTPException(status_code=403, detail="Use the Administrator account for Admin.")
    if not password:
        raise HTTPException(status_code=400, detail="Enter the Administrator password.")
    user = _administrator_user(users, username=username)
    if not user:
        raise HTTPException(status_code=404, detail="Administrator account not found.")
    if user.get("status") == "blocked":
        raise HTTPException(status_code=403, detail="Administrator account is blocked.")
    if not _verify_password(password, user.get("password_hash", "")):
        raise HTTPException(status_code=403, detail="Incorrect Administrator password.")
    token = _create_admin_token(user)
    return {"ok": True, "token": token, "user": _public_user(user)}


@app.post("/api/access/admin-check")
def access_admin_check(token: str = Form(default="")):
    session = _require_admin_token(token)
    return {"ok": True, "session": session}


@app.post("/api/access/register")
def access_register(
    username: str = Form(default=""),
    display_name: str = Form(default=""),
    email: str = Form(default=""),
    password: str = Form(default=""),
    confirm_password: str = Form(default=""),
    password_confirm: str = Form(default=""),
    login_type: str = Form(default="internal"),
    domain: str = Form(default="dev"),
    conversation_id: str = Form(default=""),
):
    users = _seed_access_users()
    username = (username or "").strip()
    display_name = (display_name or "").strip()
    email = (email or "").strip()
    if not username and not email:
        raise HTTPException(status_code=400, detail="Enter a username or email.")
    if len(password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    confirmation = password_confirm or confirm_password
    if password != confirmation:
        raise HTTPException(status_code=400, detail="Password and confirmation do not match.")
    if _find_access_user(users, username=username, email=email):
        raise HTTPException(status_code=409, detail="Account already exists. Use login or ask an admin to reset access.")

    now = _now_utc()
    clean_domain = _domain_key(domain)
    clean_login_type = str(login_type or "internal").strip().lower()
    if clean_login_type == "channel":
        clean_conversation_id = _channel_key(conversation_id) if str(conversation_id or "").strip() else ""
        if not clean_conversation_id:
            raise HTTPException(status_code=400, detail="A conversation invitation is required.")
        _, conversations = _read_channel_conversations(clean_domain)
        conversation = next(
            (row for row in conversations if str(row.get("id") or "") == clean_conversation_id),
            None,
        )
        if not conversation or not _channel_viewer_allowed(conversation, email):
            raise HTTPException(status_code=403, detail="Use an email address invited to this conversation.")
        role = "channel_guest"
    else:
        role = "candidate" if clean_login_type == "candidate" else "internal"
    linked_profile = _candidate_profile_for_login(email, username, clean_domain) if role == "candidate" else {}
    user_id = _safe_token("USR")
    users[user_id] = {
        "id": user_id,
        "username": username or email,
        "display_name": display_name or username or email,
        "email": email,
        "role": role,
        "status": "active",
        "allowed_menu": _default_menu_for_user(role, email),
        "domain": clean_domain,
        "profile_id": str(linked_profile.get("id") or ""),
        "candidate_profile_id": str(linked_profile.get("id") or ""),
        "password_hash": _password_hash(password),
        "created_at": now,
        "updated_at": now,
        "last_login_at": now,
    }
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(users[user_id]), "menu_items": MENU_ITEMS}


@app.get("/api/access/sales-owners")
def access_sales_owners(domain: str = "dev"):
    clean_domain = _domain_key(domain)
    users = _seed_access_users()
    sales_roles = {"sales", "admin", "super_user"}
    owners = []
    for user in users.values():
        if user.get("status", "active") != "active":
            continue
        if user.get("role") not in sales_roles:
            continue
        user_domain = _domain_key(user.get("domain", clean_domain))
        if user_domain not in {clean_domain, "dev"} and user.get("role") != "super_user":
            continue
        owners.append(_sales_owner_user(user))
    owners.sort(key=lambda item: (item.get("role") != "sales", item.get("name", "").lower()))
    return {"ok": True, "owners": owners}


@app.get("/api/admin/users")
def admin_users(x_devready_admin_token: str = Header(default="")):
    _require_admin_token(x_devready_admin_token)
    users = _seed_access_users()
    candidates_state = _read_json_store(ACCESS_CANDIDATES_PATH, {})
    return {
        "users": [_public_user(user) for user in users.values()],
        "menu_items": MENU_ITEMS,
        "default_internal_menu": DEFAULT_INTERNAL_MENU,
        "default_candidate_menu": DEFAULT_CANDIDATE_MENU,
        "super_menu": SUPER_MENU,
        "blocked_candidates": candidates_state,
    }


@app.get("/api/admin/candidates/search")
def admin_candidate_search(
    query: str = "",
    domain: str = "dev",
    x_devready_admin_token: str = Header(default=""),
):
    _require_admin_token(x_devready_admin_token)
    query = (query or "").strip()
    domain = (domain or "dev").strip() or "dev"
    if len(query) < 2:
        return {"results": []}

    users = _seed_access_users()
    access_records = _read_json_store(ACCESS_CANDIDATES_PATH, {})
    try:
        matches = candidates.searchCandidatesByNameEmail(query, limit=12, domain=domain)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Candidate search failed: {exc}")

    results = []
    for match in matches:
        candidate_id = str(match.get("id") or "")
        email = (match.get("email") or "").strip()
        account = _find_access_user(users, email=email) if email else None
        access_record = access_records.get(candidate_id) or access_records.get(email) or {}
        results.append({
            "candidate": {
                "id": candidate_id,
                "firstName": match.get("firstName") or "",
                "lastName": match.get("lastName") or "",
                "name": " ".join(part for part in [match.get("firstName"), match.get("lastName")] if part).strip(),
                "email": email,
                "step": match.get("step"),
                "primaryStack": match.get("primaryStack") or "",
                "skillMatches": match.get("skillMatches") or [],
                "domain": domain,
            },
            "user": _public_user(account) if account else None,
            "access": access_record,
        })
    return {"results": results}


@app.post("/api/admin/users")
def admin_save_user(
    x_devready_admin_token: str = Header(default=""),
    user_id: str = Form(default=""),
    username: str = Form(default=""),
    display_name: str = Form(default=""),
    email: str = Form(default=""),
    password: str = Form(default=""),
    confirm_password: str = Form(default=""),
    password_confirm: str = Form(default=""),
    role: str = Form(default="internal"),
    status: str = Form(default="active"),
    allowed_menu_json: str = Form(default="[]"),
):
    _require_admin_token(x_devready_admin_token)
    users = _seed_access_users()
    now = _now_utc()
    role = role if role in {"super_user", "admin", "sales", "internal", "candidate"} else "internal"
    status = status if status in {"active", "blocked"} else "active"
    try:
        allowed_menu = json.loads(allowed_menu_json or "[]")
    except Exception:
        allowed_menu = []
    allowed_keys = {item["key"] for item in MENU_ITEMS}
    allowed_menu = [key for key in allowed_menu if key in allowed_keys]
    if role in {"super_user", "admin"}:
        allowed_menu = SUPER_MENU
    elif _normalize_user_key(email).endswith("@devready.io") and set(allowed_menu) == set(DEFAULT_INTERNAL_MENU):
        allowed_menu = SUPER_MENU
    elif not allowed_menu:
        allowed_menu = _default_menu_for_user(role, email)

    user_id = user_id or _safe_token("USR")
    existing = users.get(user_id, {})
    password_hash = existing.get("password_hash", "")
    if password:
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
        confirmation = password_confirm or confirm_password
        if password != confirmation:
            raise HTTPException(status_code=400, detail="Password and confirmation do not match.")
        password_hash = _password_hash(password)
    elif not password_hash:
        raise HTTPException(status_code=400, detail="Set a password for this user.")
    users[user_id] = {
        "id": user_id,
        "username": username or existing.get("username", "") or email,
        "display_name": display_name or existing.get("display_name", "") or username or email,
        "email": email or existing.get("email", ""),
        "role": role,
        "status": status,
        "allowed_menu": allowed_menu,
        "password_hash": password_hash,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "last_login_at": existing.get("last_login_at", ""),
    }
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(users[user_id])}


@app.post("/api/admin/users/{user_id}/block")
def admin_block_user(user_id: str, blocked: str = Form(default="true"), x_devready_admin_token: str = Header(default="")):
    _require_admin_token(x_devready_admin_token)
    users = _seed_access_users()
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    users[user_id]["status"] = "blocked" if str(blocked).lower() in {"true", "1", "yes", "on"} else "active"
    users[user_id]["updated_at"] = _now_utc()
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(users[user_id])}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: str, x_devready_admin_token: str = Header(default="")):
    _require_admin_token(x_devready_admin_token)
    users = _seed_access_users()
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    deleted = users.pop(user_id)
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "deleted": _public_user(deleted)}


@app.post("/api/admin/users/{user_id}/send-login")
def admin_send_login_info(user_id: str, x_devready_admin_token: str = Header(default="")):
    _require_admin_token(x_devready_admin_token)
    users = _seed_access_users()
    user = users.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    now = _now_utc()
    events = _read_json_store(WORKFLOW_EVENTS_PATH, [])
    login_link = "/ui/index.html"
    events.insert(0, {
        "id": _safe_token("EVT"),
        "profile_id": "",
        "candidate_name": user.get("display_name", ""),
        "email": user.get("email", ""),
        "domain": "admin",
        "event_type": "login_information_prepared",
        "status": "ready_to_send",
        "notes": "Login information prepared for manual send.",
        "payload": {"login_link": login_link, "username": user.get("username", "")},
        "created_at": now,
        "updated_at": now,
    })
    _write_json_store(WORKFLOW_EVENTS_PATH, events[:1000])
    return {
        "ok": True,
        "message": "Login information prepared.",
        "login_link": login_link,
        "user": _public_user(user),
    }


@app.post("/api/admin/candidates/access")
def admin_candidate_access(
    x_devready_admin_token: str = Header(default=""),
    candidate_id: str = Form(default=""),
    candidate_email: str = Form(default=""),
    action: str = Form(default="block"),
    notes: str = Form(default=""),
):
    _require_admin_token(x_devready_admin_token)
    key = candidate_id or candidate_email
    if not key:
        raise HTTPException(status_code=400, detail="Enter a candidate id or email.")
    records = _read_json_store(ACCESS_CANDIDATES_PATH, {})
    now = _now_utc()
    if action == "unblock":
        removed = records.pop(key, None)
        _write_json_store(ACCESS_CANDIDATES_PATH, records)
        return {"ok": True, "candidate": removed or {"candidate_id": candidate_id, "candidate_email": candidate_email, "status": "active"}}
    records[key] = {
        "candidate_id": candidate_id,
        "candidate_email": candidate_email,
        "status": "blocked" if action == "block" else "deleted",
        "notes": notes,
        "updated_at": now,
    }
    _write_json_store(ACCESS_CANDIDATES_PATH, records)
    return {"ok": True, "candidate": records[key]}


def extract_text_from_upload(file: UploadFile) -> str:
    name = (file.filename or "").lower()
    data = file.file.read()
    # Reset pointer not needed; we operate on bytes.
    if name.endswith(".pdf"):
        import io, pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    text_parts.append(t)
        return "\n\n".join(text_parts).strip()
    elif name.endswith(".docx"):
        import io
        from docx import Document
        doc = Document(io.BytesIO(data))
        return "\n".join([p.text for p in doc.paragraphs if p.text]).strip()
    elif name.endswith(".txt"):
        try:
            return data.decode("utf-8", errors="ignore").strip()
        except Exception:
            return data.decode(errors="ignore").strip()
    elif name.endswith(".doc"):
        return ""
    else:
        # Try best-effort utf-8
        try:
            return data.decode("utf-8", errors="ignore").strip()
        except Exception:
            return ""


@app.post("/api/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    source_type: Optional[str] = Form(None),   # preferred: "pdf" / "docx"
    file_type: Optional[str] = Form(None),     # legacy: "PDF" / "DOCX"
    domain: str = Form("technology"),
):
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file name received.")
        path = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        st = (source_type or "").strip().lower()
        if not st:
            ft = (file_type or "").strip().lower()
            if "pdf" in ft:
                st = "pdf"
            elif "docx" in ft:
                st = "docx"
            else:
                ext = os.path.splitext(file.filename.lower())[1]
                if ext == ".pdf":
                    st = "pdf"
                elif ext == ".docx":
                    st = "docx"
                elif ext == ".doc":
                    raise HTTPException(status_code=400, detail="Legacy .doc resumes are not supported. Please upload a PDF or DOCX.")
                else:
                    raise HTTPException(status_code=400, detail="Unsupported resume type. Please upload a PDF or DOCX.")

        raw = ingest(st, path)
        if not (raw or "").strip():
            raise HTTPException(status_code=400, detail="Could not extract resume text. Please upload a text-based PDF or DOCX.")
        profile = build_profile_from_text(raw)
        profile.setdefault("meta", {})["domain"] = _storage_domain(domain)

        storage.upsert_profile(_domain_db_path(domain), profile)
        pid = profile.get("meta", {}).get("profile_id", "")

        return JSONResponse({"profile_id": pid, "profile": profile})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.get("/api/profiles")
def list_profiles(domain: str = "technology"):
    return storage.list_profiles(_domain_db_path(domain), domain=_storage_domain(domain))

@app.post("/api/profiles/skillSearch")
def search_profiles(domain: str = Form("technology"), skills: str = Form("")):
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    return storage.list_profiles(_domain_db_path(domain), domain=_storage_domain(domain), skills_filter=skill_list)


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id, domain=expected_domain)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return p


@app.get("/api/profiles/{profile_id}/html", response_class=HTMLResponse)
def get_profile_html(profile_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id, domain=expected_domain)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(profile_to_html(p))


@app.get("/api/profiles/{profile_id}/docx")
def get_profile_docx(profile_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id, domain=expected_domain)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    out = os.path.join(EXPORT_DIR, f"{profile_id}.docx")
    profile_to_docx(p, out)
    filename = f"DevReady_Profile_{p.get('contact',{}).get('full_name','Candidate').replace(' ','_')}.docx"
    return FileResponse(out, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=filename)


@app.post("/api/resume/bulk_upload")
async def bulk_upload_resumes(domain: str = Form("technology"), files: list[UploadFile] = File(...)):
    """Bulk upload multiple resumes (PDF/DOCX). Each file is parsed, normalized, and saved as a DevReady profile."""
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files received.")
        created = []
        failed = []
        for f in files:
            if not f.filename:
                continue
            fname = os.path.basename(f.filename)
            ext = os.path.splitext(fname)[1].lower()
            source_type = "pdf" if ext == ".pdf" else ("docx" if ext == ".docx" else "pdf")
            path = os.path.join(UPLOAD_DIR, fname)
            with open(path, "wb") as out:
                shutil.copyfileobj(f.file, out)

            try:
                raw = ingest(source_type, path)
                profile = build_profile_from_text(raw)
                # enforce domain
                profile.setdefault("meta", {})["domain"] = _storage_domain(domain)
                storage.upsert_profile(_domain_db_path(domain), profile)
                created.append({
                    "profile_id": profile.get("meta", {}).get("profile_id",""),
                    "full_name": profile.get("contact", {}).get("full_name",""),
                    "email": profile.get("contact", {}).get("email",""),
                    "filename": fname
                })
            except Exception as e:
                failed.append({"filename": fname, "error": str(e)})
        return {"created": created, "failed": failed, "created_count": len(created), "failed_count": len(failed), "added": len(created)}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.post("/api/jd/upload")
def jd_upload(
    file: UploadFile = File(...),
    company: str = Form(""),
    title: str = Form(""),
    domain: str = Form("technology"),
):
    try:
        jd_text = extract_text_from_upload(file)
        if (file.filename or "").lower().endswith(".doc") and not jd_text.strip():
            raise HTTPException(status_code=400, detail="Legacy .doc is not supported. Please upload .docx or .pdf.")
        if not jd_text.strip():
            raise HTTPException(status_code=400, detail="Could not extract any text from the uploaded JD file.")

        jd_id = new_id("JDD")  # stable + matches your codebase
        created_at = datetime.utcnow().isoformat() + "Z"
        skills = normalize_jd(jd_text)

        storage.upsert_jd(_domain_db_path(domain), jd_id, company, title, _storage_domain(domain), created_at, jd_text, skills)
        return {"jd_id": jd_id, "company": company, "title": title, "domain": _storage_domain(domain), "created_at": created_at, "jd_text": jd_text, "jd_skills": skills}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.post("/api/jd/normalize")
async def jd_normalize(company: str = Form(...), title: str = Form(...), jd_text: str = Form(...), domain: str = Form("technology")):
    try:
        jd_id = new_id("JDD")
        skills = normalize_jd(jd_text)
        created_at = datetime.utcnow().isoformat() + "Z"
        storage.upsert_jd(_domain_db_path(domain), jd_id, company, title, _storage_domain(domain), created_at, jd_text, skills)
        return {"jd_id": jd_id, "company": company, "title": title, "domain": _storage_domain(domain), "created_at": created_at, "jd_skills": skills, "jd_text": jd_text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.get("/api/profile/list")
def profile_list(domain: str = "technology"):
    if domain in ("all","*","",None):
        rows = []
        for key, db_path in DOMAIN_DB_PATHS.items():
            rows.extend(storage.list_profiles(db_path, domain=_storage_domain(key), limit=1000))
        return rows
    return storage.list_profiles(_domain_db_path(domain), domain=_storage_domain(domain))

@app.get("/api/profile/count")
def profile_count(domain: str = "technology"):
    if domain in ("all","*","",None):
        return sum(storage.count_profiles(db_path, domain=_storage_domain(key)) for key, db_path in DOMAIN_DB_PATHS.items())
    return storage.count_profiles(_domain_db_path(domain), domain=_storage_domain(domain))

@app.get("/api/profile/count/recent")
def profile_count_recent(domain: str = "technology"):
    if domain in ("all","*","",None):
        return sum(storage.count_profiles_recent(db_path, domain=_storage_domain(key)) for key, db_path in DOMAIN_DB_PATHS.items())
    return storage.count_profiles_recent(_domain_db_path(domain), domain=_storage_domain(domain))

# Used to search for profiles with the search bar
@app.post("/api/profile/search")
def profile_search(domain: str = Form(default="technology"), search_string: str = Form(default="")):
    if domain in ("all","*","",None):
        rows = []
        for key, db_path in DOMAIN_DB_PATHS.items():
            rows.extend(storage.search_profiles(db_path, domain=_storage_domain(key), search_string=search_string, limit=5))
        return rows[:15]
    return storage.search_profiles(_domain_db_path(domain), domain=_storage_domain(domain), search_string=search_string, limit=5)

@app.post("/api/profile/pageCount")
def profile_page_count(domain: str = Form(default="technology"), search_string: str = Form(default=""), pageLimit: int = Form(default=10)):
    print(f"Calculating page count for domain='{domain}' with search_string='{search_string}'")
    if domain in ("all","*","",None):
        row_count = sum(storage.search_profiles_page_count(db_path, domain=_storage_domain(key), search_string=search_string, pageLimit=pageLimit)[0] for key, db_path in DOMAIN_DB_PATHS.items())
        pages = (row_count // pageLimit) + (1 if row_count % pageLimit > 0 else 0)
        return [row_count, pages]
    return storage.search_profiles_page_count(_domain_db_path(domain), domain=_storage_domain(domain), search_string=search_string, pageLimit=pageLimit)

@app.post("/api/profile/pageSearch")
def profile_page_search(domain: str = Form(default="technology"), search_string: str = Form(default=""), currentPage: int = Form(default=0), pageLimit: int = Form(default=10)):
    print(f"Searching profiles for domain='{domain}' with search_string='{search_string}' on page {currentPage} with pageLimit {pageLimit}")

    currentPage = currentPage - 1  # adjust for 0-based indexing in backend

    if domain in ("all","*","",None):
        rows = []
        for key, db_path in DOMAIN_DB_PATHS.items():
            rows.extend(storage.search_profiles_full(db_path, domain=_storage_domain(key), search_string=search_string, currentPage=0, pageLimit=pageLimit))
        start = max(0, currentPage) * pageLimit
        return rows[start:start + pageLimit]
    return storage.search_profiles_full(_domain_db_path(domain), domain=_storage_domain(domain), search_string=search_string, currentPage=currentPage, pageLimit=pageLimit)


@app.get("/api/profile/{profile_id}/notes")
def profile_notes(profile_id: str, domain: str = "dev"):
    data = _read_profile_notes_store()
    record = _profile_notes_record(data, profile_id, domain)
    notes = sorted(record.get("notes", []), key=lambda item: item.get("created_at", ""), reverse=True)
    return {
        "profile_id": str(profile_id),
        "domain": _domain_key(domain),
        "notes": notes,
        "links": [
            link
            for link in data.get("links", {}).values()
            if str(link.get("profile_id")) == str(profile_id) and _domain_key(link.get("domain")) == _domain_key(domain)
        ],
    }


@app.post("/api/profile/{profile_id}/notes")
def profile_notes_add(
    profile_id: str,
    domain: str = Form(default="dev"),
    note: str = Form(default=""),
    role_title: str = Form(default=""),
    author: str = Form(default="DevReady"),
):
    clean_note = _trim_note_text(note)
    if not clean_note:
        raise HTTPException(status_code=400, detail="Add a note before saving.")
    data = _read_profile_notes_store()
    record = _profile_notes_record(data, profile_id, domain)
    item = {
        "id": _safe_token("NOTE"),
        "kind": "internal_note",
        "created_at": _now_utc(),
        "author": _trim_note_text(author, 160) or "DevReady",
        "role_title": _trim_note_text(role_title, 240),
        "note": clean_note,
        "private": True,
    }
    record["notes"].append(item)
    _write_profile_notes_store(data)
    return {"ok": True, "note": item}


@app.post("/api/profile/{profile_id}/role-feedback-link")
def profile_role_feedback_link(
    request: Request,
    profile_id: str,
    domain: str = Form(default="dev"),
    candidate_name: str = Form(default=""),
    candidate_email: str = Form(default=""),
    job_id: str = Form(default=""),
    role_company: str = Form(default=""),
    role_title: str = Form(default=""),
    role_description: str = Form(default=""),
):
    if not _trim_note_text(role_title, 240):
        raise HTTPException(status_code=400, detail="Add a role title before creating the link.")
    token = _safe_token("ROLE")
    data = _read_profile_notes_store()
    link = {
        "token": token,
        "profile_id": str(profile_id),
        "domain": _domain_key(domain),
        "candidate_name": _trim_note_text(candidate_name, 240),
        "candidate_email": _trim_note_text(candidate_email, 320),
        "job_id": _trim_note_text(job_id, 80),
        "role_company": _trim_note_text(role_company, 240),
        "role_title": _trim_note_text(role_title, 240),
        "role_description": _trim_note_text(role_description, 7000),
        "status": "open",
        "created_at": _now_utc(),
        "submitted_at": "",
    }
    data.setdefault("links", {})[token] = link
    _profile_notes_record(data, profile_id, domain)
    _write_profile_notes_store(data)
    base_url = str(request.base_url).rstrip("/")
    return {
        "ok": True,
        "token": token,
        "link": link,
        "url": f"{base_url}/ui/pages/role-feedback.html?token={quote_plus(token)}",
    }


@app.get("/api/profile/role-feedback/{token}")
def profile_role_feedback_get(token: str):
    data = _read_profile_notes_store()
    link = data.get("links", {}).get(token)
    if not link:
        raise HTTPException(status_code=404, detail="Role feedback link not found.")
    return {"ok": True, "link": link}


@app.post("/api/profile/role-feedback/{token}")
def profile_role_feedback_submit(
    token: str,
    interest: str = Form(default=""),
    thoughts: str = Form(default=""),
    skills: str = Form(default=""),
    availability: str = Form(default=""),
    questions: str = Form(default=""),
):
    data = _read_profile_notes_store()
    link = data.get("links", {}).get(token)
    if not link:
        raise HTTPException(status_code=404, detail="Role feedback link not found.")
    if link.get("status") == "closed":
        raise HTTPException(status_code=400, detail="This feedback link is closed.")
    profile_id = str(link.get("profile_id") or "")
    domain = _domain_key(link.get("domain") or "dev")
    note_text = _trim_note_text(thoughts)
    if not any([interest, note_text, skills, availability, questions]):
        raise HTTPException(status_code=400, detail="Add at least one response before submitting.")

    record = _profile_notes_record(data, profile_id, domain)
    item = {
        "id": _safe_token("NOTE"),
        "kind": "candidate_role_feedback",
        "created_at": _now_utc(),
        "author": link.get("candidate_name") or "Candidate",
        "role_title": link.get("role_title") or "",
        "note": note_text,
        "interest": _trim_note_text(interest, 80),
        "skills": _trim_note_text(skills, 3000),
        "availability": _trim_note_text(availability, 1000),
        "questions": _trim_note_text(questions, 2000),
        "source": "candidate_role_feedback_link",
        "token": token,
        "private": True,
    }
    record["notes"].append(item)
    link["status"] = "submitted"
    link["submitted_at"] = item["created_at"]
    link["note_id"] = item["id"]
    _write_profile_notes_store(data)
    _egeria_log_event(
        domain,
        "candidate_role_feedback_submitted",
        context={
            "domain": domain,
            "currentStep": "candidate-interest",
            "nextStep": "candidate-review",
            "workflowId": token,
            "candidateId": profile_id,
            "candidateName": link.get("candidate_name") or "",
            "candidateEmail": link.get("candidate_email") or "",
            "jobId": link.get("job_id") or "",
            "jobCompany": link.get("role_company") or "",
            "jobTitle": link.get("role_title") or "",
        },
        before={"link_status": "open", "token": token},
        after={
            "link_status": "submitted",
            "token": token,
            "note_id": item["id"],
            "interest": item["interest"],
            "submitted_at": item["created_at"],
            "workflowId": token,
        },
        message=f"{link.get('candidate_name') or 'Candidate'} submitted interest feedback for {link.get('role_title') or 'the role'}.",
        payload={"link": link, "note": item},
    )
    return {"ok": True, "message": "Feedback submitted. Thank you.", "note_id": item["id"]}


@app.get("/api/profile/{profile_id}")
def profile_get(profile_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id, domain=expected_domain)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return p


@app.get("/api/profile/{profile_id}/html", response_class=HTMLResponse)
def profile_html(profile_id: str, domain: str = ""):
    # Reuse the canonical /api/profiles/{id}/html implementation
    return get_profile_html(profile_id, domain=domain)


@app.get("/api/profile/{profile_id}/docx")
def profile_docx(profile_id: str, domain: str = ""):
    # Reuse the canonical /api/profiles/{id}/docx implementation
    return get_profile_docx(profile_id, domain=domain)


@app.get("/api/jd/list")
def jd_list(domain: str = "technology"):
    if domain in ("all","*","",None):
        rows = []
        for key, db_path in DOMAIN_DB_PATHS.items():
            rows.extend(storage.list_jds(db_path, domain=_storage_domain(key)))
        return rows
    return storage.list_jds(_domain_db_path(domain), domain=_storage_domain(domain))


@app.get("/api/jd/latest")
def jd_latest(domain: str = "technology", jd_id: Optional[str] = None):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id, domain=_storage_domain(domain)) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd:
        return {"jd_id": "", "company":"", "title": "", "domain": _storage_domain(domain), "created_at": "", "jd_text": "", "jd_skills": {}}
    return jd


@app.get("/api/jd/{jd_id}")
def jd_get(jd_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id, domain=expected_domain)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return jd


@app.get("/api/jd/{jd_id}/html", response_class=HTMLResponse)
def jd_html(jd_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id, domain=expected_domain)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return HTMLResponse(jd_to_html(jd))


@app.get("/api/jd/{jd_id}/docx")
def jd_docx(jd_id: str, domain: str = ""):
    expected_domain = _storage_domain(domain) if domain else None
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id, domain=expected_domain)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    out = os.path.join(EXPORT_DIR, f"{jd_id}.docx")
    jd_to_docx(jd, out)
    filename = f"Job_Description_{jd.get('company','Company').replace(' ','_')}_{jd.get('title','Role').replace(' ','_')}.docx"
    return FileResponse(out, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=filename)

from openAI import externalPeopleSearch
import peopleDataLabs.peopleSearch as peopleDataLabs
from azureUtils.storage import candidates

@app.post("/api/match/run")
def run_match(domain: str = Form("technology"), jd_id: str = Form(None), top_k: int = Form(10)):
    # TODO: Set up job descriptions in the database
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    jd_skills = jd["jd_skills"]
    
    peopleDataSkills = []
    if jd_skills:
        # Get all skill from JD
        for key, value in jd_skills.items():
            peopleDataSkills.extend(value)

        peopleDataSkills = list(set(peopleDataSkills))  # unique skills
        print(f'Extracted skills for external search: {peopleDataSkills}')
    else:
        peopleDataSkills = externalPeopleSearch.getPeopleSkills(jd["jd_text"])
        storage.upsert_jd(_domain_db_path(domain), jd["jd_id"], jd.get("company",""), jd.get("title",""), _storage_domain(domain), jd.get("created_at",""), jd["jd_text"], {"ai_extracted_skills": peopleDataSkills})
    
    returnedExternalPeople = []

    # Extract location info
    '''jobCity = externalPeopleSearch.getPeopleCity(jd["jd_text"])
    jobState = externalPeopleSearch.getPeopleState(jd["jd_text"])
    jobCountry = externalPeopleSearch.getPeopleCountry(jd["jd_text"])

    if len(jobCity) > 0 or len(jobState) > 0 or len(jobCountry) > 0:
        print(f'Extracted location for external search: City={jobCity}, State={jobState}, Country={jobCountry}')

        returnedExternalPeople = peopleDataLabs.searchSkillsAndLocation(peopleDataSkills, jobCity, jobState, jobCountry, top_k)["data"]
    else:'''
    print('No location extracted from JD. Running external search based on skills only.')
    try:
        returnedExternalPeople = peopleDataLabs.searchSkills(peopleDataSkills, top_k)["data"]
    except Exception as e:
        print(f'Error during external people search: {e}')

    #profiles = storage.list_profiles(DB_PATH, domain=domain, limit=top_k, skills_filter=peopleDataSkills)
    profiles = candidates.searchCandidatesBySkills(','.join(peopleDataSkills), top_k)

    ranked = []
    for row in profiles:
        #p = storage.get_profile(DB_PATH, row["profile_id"])
        #score, parts = match((p or {}).get("skills", {}), jd_skills)
        score, parts = azureMatch(row['skillMatches'],jd_skills)
        
        '''ranked.append({
            "profile_id": row["profile_id"],
            "name": row.get("full_name",""),
            "email": row.get("email",""),
            "score": score,
            "top_matches": top_matches_from_parts(parts),
            "breakdown": parts
        })'''
        ranked.append({
            "profile_id": row["id"],
            "name": row["firstName"] + ' ' + row["lastName"],
            "email": row["email"],
            "score": score,
            "top_matches": top_matches_from_parts(parts),
            "breakdown": parts
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    rankedExternal = []
    for row in returnedExternalPeople:
        score, parts = azureMatch(row['skills'],jd_skills)
        inferredSalary = None
        if "inferred_salary" in row:
            inferredSalary = row["inferred_salary"]
        
        rankedExternal.append({
            "profile_id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "recommended_personal_email": row.get("work_email") or "",
            "linkedin_url": row["linkedin_url"],
            "inferred_salary": inferredSalary,
            "score": score,
            "top_matches": top_matches_from_parts(parts),
            "breakdown": parts
        })

    rankedExternal.sort(key=lambda x: x["score"], reverse=True)
    return {"jd": {"jd_id": jd["jd_id"], "company": jd.get("company",""), "title": jd.get("title",""), "created_at": jd.get("created_at","")}, "results": ranked[:top_k], "externalMatches": rankedExternal, "skillList": peopleDataSkills}


@app.post("/api/match/scorecard")
def match_scorecard(
    profile_id: str = Form(...),
    domain: str = Form("technology"),
    jd_id: str = Form(""),
):
    # Use selected JD if provided, else most recent JD in this domain
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")

    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    match_score, breakdown = match((p or {}).get("skills", {}), jd["jd_skills"])
    card = build_scorecard(p, jd, match_score, breakdown)
    card["match_score"] = match_score
    card["top_matches"] = top_matches_from_parts(breakdown, limit=10)
    return card


@app.post("/api/match/interview_questions")
def interview_questions(
    profile_id: str = Form(...),
    domain: str = Form("technology"),
    jd_id: str = Form(""),
):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")

    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    match_score, breakdown = match((p or {}).get("skills", {}), jd["jd_skills"])
    questions = build_interview_questions(p, jd, breakdown)
    return {"profile_id": profile_id, "jd_id": jd.get("jd_id",""), "questions": questions}


@app.post("/api/match/explain")
def explain(profile_id: str = Form(...), domain: str = Form("technology"), jd_id: str = Form("")):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    score, parts = match(p.get("skills", {}), jd["jd_skills"])
    must_haves = []
    gaps = []
    for grp, info in parts.items():
        if info["matched"]:
            must_haves.append(f"{grp}: " + ", ".join(info["matched"][:8]))
        if info["missing"]:
            gaps.append(f"{grp}: " + ", ".join(info["missing"][:6]))

    why = {
      "match_score": score,
      "top_matches": must_haves[:6],
      "notable_gaps": gaps[:4],
      "client_excerpt": (
        f"{p['contact'].get('full_name','Candidate')} is a strong match for the role based on aligned technical stack. "
        f"Top overlaps include: " + ("; ".join(must_haves[:4]) if must_haves else "core skill alignment") + "."
      ),
      "draft_client_email": (
        f"Subject: Candidate Recommendation - {p['contact'].get('full_name','Candidate')}\n\n"
        f"Hi,\n\n"
        f"Based on the job description '{jd.get('title','')}' at {jd.get('company','')}, we recommend {p['contact'].get('full_name','Candidate')} for interview consideration. "
        f"Match score: {score}/100.\n\n"
        f"Key alignment:\n- " + ("\n- ".join(must_haves[:5]) if must_haves else "Aligned with core requirements") + "\n\n"
        f"Potential gaps to validate:\n- " + ("\n- ".join(gaps[:3]) if gaps else "None identified from keyword matching") + "\n\n"
        f"Contact: {p['contact'].get('email','')}\n\nBest,\nDJ"
      )
    }
    return why


@app.get("/api/match/report/html", response_class=HTMLResponse)
def match_report_html(profile_id: str, jd_id: str, domain: str = "technology"):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    score, parts = match(p.get("skills", {}), jd["jd_skills"])
    scorecard = build_scorecard(p, jd, score, parts)
    interview = {"questions": build_interview_questions(p, jd, parts)}

    must_haves = []
    gaps = []
    for grp, info in parts.items():
        if info.get("matched"):
            must_haves.append(f"{grp}: " + ", ".join(info["matched"][:8]))
        if info.get("missing"):
            gaps.append(f"{grp}: " + ", ".join(info["missing"][:6]))

    explain = {
      "match_score": score,
      "top_matches": must_haves[:6],
      "notable_gaps": gaps[:4],
      "client_excerpt": (
        f"{p['contact'].get('full_name','Candidate')} is a strong match for the role based on aligned technical stack. "
        f"Top overlaps include: " + ("; ".join(must_haves[:4]) if must_haves else "core skill alignment") + "."
      ),
      "draft_client_email": (
        f"Subject: Candidate Recommendation - {p['contact'].get('full_name','Candidate')}\n\n"
        f"Hi,\n\n"
        f"Based on the job description '{jd.get('title','')}' at {jd.get('company','')}, we recommend {p['contact'].get('full_name','Candidate')} for interview consideration. "
        f"Match score: {score}/100.\n\n"
        f"Key alignment:\n- " + ("\n- ".join(must_haves[:5]) if must_haves else "Aligned with core requirements") + "\n\n"
        f"Potential gaps to validate:\n- " + ("\n- ".join(gaps[:3]) if gaps else "None identified from keyword matching") + "\n\n"
        f"Contact: {p['contact'].get('email','')}\n\nBest,\nDJ"
      )
    }

    html_doc = match_report_to_html(p, jd, scorecard, interview, explain)
    return HTMLResponse(html_doc)


@app.get("/api/match/report/docx")
def match_report_docx(profile_id: str, jd_id: str, domain: str = "technology"):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    score, parts = match(p.get("skills", {}), jd["jd_skills"])
    scorecard = build_scorecard(p, jd, score, parts)
    interview = {"questions": build_interview_questions(p, jd, parts)}

    must_haves = []
    gaps = []
    for grp, info in parts.items():
        if info.get("matched"):
            must_haves.append(f"{grp}: " + ", ".join(info["matched"][:8]))
        if info.get("missing"):
            gaps.append(f"{grp}: " + ", ".join(info["missing"][:6]))

    explain = {
      "match_score": score,
      "top_matches": must_haves[:6],
      "notable_gaps": gaps[:4],
      "client_excerpt": (
        f"{p['contact'].get('full_name','Candidate')} is a strong match for the role based on aligned technical stack. "
        f"Top overlaps include: " + ("; ".join(must_haves[:4]) if must_haves else "core skill alignment") + "."
      ),
      "draft_client_email": (
        f"Subject: Candidate Recommendation - {p['contact'].get('full_name','Candidate')}\n\n"
        f"Hi,\n\n"
        f"Based on the job description '{jd.get('title','')}' at {jd.get('company','')}, we recommend {p['contact'].get('full_name','Candidate')} for interview consideration. "
        f"Match score: {score}/100.\n\n"
        f"Key alignment:\n- " + ("\n- ".join(must_haves[:5]) if must_haves else "Aligned with core requirements") + "\n\n"
        f"Potential gaps to validate:\n- " + ("\n- ".join(gaps[:3]) if gaps else "None identified from keyword matching") + "\n\n"
        f"Contact: {p['contact'].get('email','')}\n\nBest,\nDJ"
      )
    }

    os.makedirs(EXPORT_DIR, exist_ok=True)
    safe_name = (p['contact'].get('full_name','candidate') or 'candidate').replace(" ", "_")
    out_path = os.path.join(EXPORT_DIR, f"match_report_{safe_name}_{jd.get('jd_id','')}.docx")
    match_report_to_docx(out_path, p, jd, scorecard, interview, explain)
    return FileResponse(out_path, filename=os.path.basename(out_path), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

import duxSoup.duxProfiles as duxProfiles

@app.post("/api/linkedin/sendMessage")
def send_linkedin_message(
    selectedProfileId: str = Form(...),
    message: str = Form(...)
):
    print(f"Sending LinkedIn message to profile ID {selectedProfileId} with message: {message}")
    outcome = duxProfiles.sendLinkedInMessage(selectedProfileId, message)
    print('Results: ' + str(outcome))
    return {"status": "success", "returnMessage": "Successfully sent LinkedIn message!" }

@app.post("/api/duxsoup/profileToPDF")
def profile_to_pdf(
    linkedInProfileUrl: str = Form(...)
):
    print(f"Exporting profile ID {linkedInProfileUrl} to PDF")
    outcome = duxProfiles.getProfilePDF(linkedInProfileUrl)
    print('Results: ' + str(outcome))

@app.post("/api/peopleLabs/search")
def people_labs_search(
    skills: str = Form(...),
    locationCity: str = Form(default=None),
    locationState: str = Form(default=None),
    locationCountry: str = Form(default=None)
):
    print(f"Received PeopleLabs search request with skills: {skills} and location: {locationCity}, {locationState}, {locationCountry}")
    skills_list = [s.strip() for s in skills.split(",") if s.strip()]

    if (locationCity and len(locationCity.strip()) > 0) or (locationState and len(locationState.strip()) > 0) or (locationCountry and len(locationCountry.strip()) > 0):
        print(f"Searching PeopleLabs for skills: {skills}, location: {locationCity}, {locationState}, {locationCountry}")
        outcome = peopleDataLabs.searchSkillsAndLocation(skills_list, locationCity=locationCity, locationState=locationState, locationCountry=locationCountry, size=30)
        #print('Results: ' + str(outcome))
    else:
        print(f"Searching PeopleLabs for skills: {skills}")
        outcome =peopleDataLabs.searchSkills(skills_list)
        #print('Results: ' + str(outcome))

    return {"status": "success", "returnMessage": "Successfully searched PeopleDataLabs!", "results": outcome['data'] }


@app.get("/api/profile/{profile_id}/badges")
def profile_badges(profile_id: str):
    badges = _read_profile_badges()
    return {
        "profile_id": profile_id,
        "badges": badges.get(str(profile_id), {}),
    }


@app.get("/api/profile/{profile_id}/process-stage")
def profile_process_stage(profile_id: str, domain: str = "dev"):
    return _profile_process_stage_status(profile_id, domain)


@app.post("/api/profile/{profile_id}/badges/tech-challenge")
def mark_tech_challenge_badge(
    profile_id: str,
    status: str = Form(default="passed"),
    score: str = Form(default=""),
    challenge_title: str = Form(default="DevReady 20-question Multiple Choice Challenge"),
    notes: str = Form(default=""),
):
    normalized_status = (status or "passed").strip().lower()
    if normalized_status not in {"passed", "failed", "completed"}:
        raise HTTPException(status_code=400, detail="status must be passed, failed, or completed.")

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    badges = _read_profile_badges()
    profile_entry = badges.setdefault(str(profile_id), {})
    profile_entry["techChallenge"] = {
        "status": normalized_status,
        "score": score,
        "challengeTitle": challenge_title,
        "notes": notes,
        "updatedAt": now,
    }
    _write_profile_badges(badges)
    return {
        "ok": True,
        "profile_id": profile_id,
        "badge": profile_entry["techChallenge"],
    }


@app.post("/api/profile/{profile_id}/badges/ai-certification")
def mark_ai_certification_badge(
    profile_id: str,
    status: str = Form(default="certified"),
    level: str = Form(default="AI Certified"),
    score: str = Form(default=""),
    certificate_id: str = Form(default=""),
    candidate_name: str = Form(default=""),
    email: str = Form(default=""),
    title: str = Form(default=""),
    notes: str = Form(default=""),
    domain: str = Form(default="dev"),
):
    normalized_status = (status or "certified").strip().lower()
    if normalized_status not in {"started", "completed", "certified", "failed"}:
        raise HTTPException(status_code=400, detail="status must be started, completed, certified, or failed.")

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    ensured_profile_id, created_profile = _ensure_profile_for_certification(
        profile_id=profile_id,
        candidate_name=candidate_name,
        email=email,
        title=title,
        level=level,
        score=score,
        certificate_id=certificate_id,
        domain=domain,
    )
    badges = _read_profile_badges()
    profile_entry = badges.setdefault(str(ensured_profile_id), {})
    profile_entry["aiCertification"] = {
        "status": normalized_status,
        "level": level,
        "score": score,
        "certificateId": certificate_id,
        "title": title or _normalize_cert_title(level, certificate_id),
        "notes": notes,
        "updatedAt": now,
    }
    _write_profile_badges(badges)
    return {
        "ok": True,
        "profile_id": ensured_profile_id,
        "created_profile": created_profile,
        "badge": profile_entry["aiCertification"],
    }


@app.post("/api/profile/badges/ai-certification")
def mark_ai_certification_badge_without_profile(
    profile_id: str = Form(default=""),
    status: str = Form(default="certified"),
    level: str = Form(default="AI Certified"),
    score: str = Form(default=""),
    certificate_id: str = Form(default=""),
    candidate_name: str = Form(default=""),
    email: str = Form(default=""),
    title: str = Form(default=""),
    notes: str = Form(default=""),
    domain: str = Form(default="dev"),
):
    return mark_ai_certification_badge(
        profile_id=profile_id,
        status=status,
        level=level,
        score=score,
        certificate_id=certificate_id,
        candidate_name=candidate_name,
        email=email,
        title=title,
        notes=notes,
        domain=domain,
    )


@app.post("/api/workflow/events")
def record_workflow_event(
    profile_id: str = Form(default=""),
    candidate_name: str = Form(default=""),
    email: str = Form(default=""),
    domain: str = Form(default="dev"),
    event_type: str = Form(default="workflow"),
    status: str = Form(default="recorded"),
    notes: str = Form(default=""),
    payload_json: str = Form(default="{}"),
):
    events = _read_json_store(WORKFLOW_EVENTS_PATH, [])
    now = _now_utc()
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except Exception:
        payload = {"raw": payload_json}
    event = {
        "id": _safe_token("EVT"),
        "profile_id": profile_id,
        "candidate_name": candidate_name,
        "email": email,
        "domain": domain or "dev",
        "event_type": event_type,
        "status": status,
        "notes": notes,
        "payload": payload if isinstance(payload, dict) else {"value": payload},
        "created_at": now,
        "updated_at": now,
    }
    events.insert(0, event)
    _write_json_store(WORKFLOW_EVENTS_PATH, events[:1000])
    return {"ok": True, "event": event}


@app.get("/api/workflow/events/{profile_id}")
def get_workflow_events(profile_id: str):
    events = _read_json_store(WORKFLOW_EVENTS_PATH, [])
    return {
        "profile_id": profile_id,
        "events": [event for event in events if str(event.get("profile_id", "")) == str(profile_id)],
    }


@app.post("/api/interviews/archive")
def save_interview_archive(
    record_json: str = Form(default="{}"),
    domain: str = Form(default="dev"),
):
    archive = _read_json_store(INTERVIEW_ARCHIVE_PATH, [])
    now = _now_utc()
    try:
        record = json.loads(record_json or "{}")
        if not isinstance(record, dict):
            record = {"value": record}
    except Exception:
        record = {"raw": record_json}
    record_id = str(record.get("id") or _safe_token("INT"))
    record["id"] = record_id
    record["domain"] = _domain_key(record.get("domain") or domain)
    record["archivedAt"] = record.get("archivedAt") or now
    record["updatedAt"] = now
    record["archiveType"] = "interview"

    kept = [item for item in archive if str(item.get("id")) != record_id]
    kept.insert(0, record)
    _write_json_store(INTERVIEW_ARCHIVE_PATH, kept[:1000])
    return {"ok": True, "record": record}


@app.get("/api/interviews/archive")
def list_interview_archive(
    domain: str = "dev",
    profile_id: str = "",
    record_id: str = "",
    limit: int = 50,
):
    archive = _read_json_store_with_demo(INTERVIEW_ARCHIVE_PATH, [])
    clean_domain = _domain_key(domain)
    rows = []
    for item in archive:
        if clean_domain != "all" and item.get("domain") not in {clean_domain, "", None}:
            continue
        if profile_id and str(item.get("candidateId") or item.get("profile_id") or "") != str(profile_id):
            continue
        if record_id and str(item.get("id") or "") != str(record_id):
            continue
        rows.append(item)
    return {"ok": True, "records": rows[: max(1, min(limit, 250))]}


@app.get("/api/crm/records")
def list_crm_records(domain: str = "dev", limit: int = 200, include_archived: bool = False):
    db_records = _atlas_crm_records_db(domain, include_archived=include_archived, limit=limit)
    if db_records is not None:
        return {"ok": True, "records": db_records[: max(1, min(limit, 500))], "storage": "postgres"}
    records = _read_json_store_with_demo(CRM_RECORDS_PATH, [])
    wanted_domain = _domain_key(domain)
    if not isinstance(records, list):
        records = []
    if not include_archived:
        records = [item for item in records if isinstance(item, dict) and not _crm_record_archived(item)]
    if wanted_domain != "all":
        records = [item for item in records if _domain_key(item.get("domain", "dev")) == wanted_domain]
    records = sorted(records, key=lambda item: item.get("updatedAt") or item.get("createdAt") or "", reverse=True)
    return {"ok": True, "records": records[: max(1, min(limit, 500))], "storage": "json_fallback"}


def _crm_customer_rows(domain: str = "dev") -> list[dict]:
    clean_domain = _domain_key(domain)
    records = _atlas_crm_records_db(clean_domain, include_archived=False, limit=2000)
    if records is None:
        records = _read_json_store_with_demo(CRM_RECORDS_PATH, [])
    if not isinstance(records, list):
        records = []
    rows = []
    seen = set()
    for item in sorted(records, key=lambda row: row.get("updatedAt") or row.get("createdAt") or "", reverse=True):
        if not isinstance(item, dict):
            continue
        if _crm_record_archived(item):
            continue
        if clean_domain != "all" and _domain_key(item.get("domain", "dev")) != clean_domain:
            continue
        name = _safe_action_text(item.get("customer"), 240)
        if not name:
            continue
        row_id = _safe_action_text(item.get("id"), 120) or name
        key = row_id.lower()
        name_key = name.lower()
        if key in seen or name_key in seen:
            continue
        seen.add(key)
        seen.add(name_key)
        rows.append(
            {
                "id": row_id,
                "name": name,
                "email": _safe_action_text(item.get("billing_email") or item.get("ap_email") or item.get("email"), 240),
                "address": _safe_action_text(item.get("billing_address") or item.get("address"), 1200),
                "contact": _safe_action_text(item.get("contact"), 240),
                "owner": _safe_action_text(item.get("owner"), 120),
                "domain": _domain_key(item.get("domain", clean_domain)),
                "source": item,
            }
        )
    return rows


def _crm_customer_for_value(domain: str, value: str) -> dict:
    clean = _safe_action_text(value, 240).lower()
    if not clean:
        return {}
    for customer in _crm_customer_rows(domain):
        if customer.get("id", "").lower() == clean or customer.get("name", "").lower() == clean:
            return customer
    return {}


def _prospect_reference_rows(domain: str = "dev") -> list[dict]:
    clean_domain = _domain_key(domain)
    db_result = _prospect_reference_rows_db(clean_domain, limit=500, offset=0)
    if db_result is not None:
        return db_result[0]
    records = _read_json_store(PROSPECT_REFERENCE_RECORDS_PATH, [])
    if not isinstance(records, list):
        return []
    rows = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if item.get("archived") or item.get("archivedAt"):
            continue
        if clean_domain != "all" and _domain_key(item.get("domain", "dev")) != clean_domain:
            continue
        rows.append(item)
    return rows


@app.get("/api/prospects/reference")
def list_prospect_reference(
    domain: str = "dev",
    q: str = "",
    industry: str = "",
    limit: int = 200,
    offset: int = 0,
):
    db_result = _prospect_reference_rows_db(domain, q=q, industry=industry, limit=limit, offset=offset)
    if db_result is not None:
        db_rows, db_count = db_result
        safe_limit = max(1, min(limit, 500))
        safe_offset = max(0, offset)
        return {
            "ok": True,
            "domain": _domain_key(domain),
            "count": db_count,
            "offset": safe_offset,
            "limit": safe_limit,
            "records": db_rows,
            "storage": "postgres",
        }
    clean_query = _safe_action_text(q, 240).lower()
    clean_industry = _safe_action_text(industry, 180).lower()
    rows = _prospect_reference_rows(domain)
    if clean_query:
        def _matches(item: dict) -> bool:
            haystack = " ".join(
                [
                    _safe_action_text(item.get("company"), 240),
                    _safe_action_text(item.get("domain_name"), 240),
                    _safe_action_text(item.get("website"), 240),
                    _safe_action_text(item.get("industry"), 240),
                    _safe_action_text(item.get("city"), 120),
                    _safe_action_text(item.get("state"), 120),
                    _safe_action_text(item.get("country"), 120),
                    _safe_action_text(item.get("description"), 1000),
                    _safe_action_text(item.get("web_technologies"), 1000),
                ]
            ).lower()
            return clean_query in haystack

        rows = [item for item in rows if _matches(item)]
    if clean_industry:
        rows = [item for item in rows if clean_industry in _safe_action_text(item.get("industry"), 240).lower()]
    rows = sorted(rows, key=lambda item: item.get("company", "").lower())
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    return {
        "ok": True,
        "domain": _domain_key(domain),
        "count": len(rows),
        "offset": safe_offset,
        "limit": safe_limit,
        "records": rows[safe_offset:safe_offset + safe_limit],
        "storage": "json_fallback",
    }


@app.post("/api/prospects/reference/promote")
def promote_prospect_reference(
    prospect_id: str = Form(default=""),
    domain: str = Form(default="dev"),
    owner: str = Form(default=""),
    next_step: str = Form(default="Review and qualify this promoted prospect."),
):
    clean_domain = _domain_key(domain)
    clean_id = _safe_action_text(prospect_id, 160)
    if not clean_id:
        raise HTTPException(status_code=400, detail="Prospect id is required.")
    prospects = _read_json_store(PROSPECT_REFERENCE_RECORDS_PATH, [])
    if not isinstance(prospects, list):
        prospects = []
    prospect = _prospect_reference_by_id_db(clean_id)
    for item in prospects:
        if not prospect and isinstance(item, dict) and item.get("id") == clean_id:
            prospect = item
            break
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect reference not found.")
    if clean_domain != _domain_key(prospect.get("domain", clean_domain)):
        raise HTTPException(status_code=400, detail="Prospect belongs to a different domain.")

    crm_records = _atlas_crm_records_db(clean_domain, include_archived=True, limit=2000)
    if crm_records is None:
        crm_records = _read_json_store(CRM_RECORDS_PATH, [])
    if not isinstance(crm_records, list):
        crm_records = []
    for record in crm_records:
        if isinstance(record, dict) and record.get("sourceProspectId") == clean_id and _domain_key(record.get("domain", clean_domain)) == clean_domain:
            return {"ok": True, "record": record, "already_promoted": True}

    now = _now_utc()
    contacts = prospect.get("contacts") if isinstance(prospect.get("contacts"), list) else []
    primary = contacts[0] if contacts else {}
    address = ", ".join([part for part in [
        _safe_action_text(prospect.get("street"), 240),
        _safe_action_text(prospect.get("city"), 120),
        _safe_action_text(prospect.get("state"), 80),
        _safe_action_text(prospect.get("postal_code"), 40),
        _safe_action_text(prospect.get("country"), 120),
    ] if part])
    record = {
        "id": _safe_token("CRM-PROSPECT"),
        "domain": clean_domain,
        "customer": _safe_action_text(prospect.get("company"), 240) or "Promoted prospect",
        "contact": _safe_action_text(primary.get("name"), 180),
        "email": _safe_action_text(primary.get("email"), 240),
        "phone": _safe_action_text(prospect.get("phone"), 80),
        "owner": _safe_action_text(owner, 120),
        "territory": "Promoted prospect reference",
        "industry": _safe_action_text(prospect.get("industry"), 240),
        "value": 0,
        "strength": 3,
        "contractStatus": "Prospect",
        "dealStage": "Prospect",
        "dealProbability": 5,
        "dealTitle": f"Prospect qualification - {_safe_action_text(prospect.get('company'), 160)}",
        "lastTouched": now[:16],
        "when": now[:16],
        "where": "Prospect Reference Library",
        "what": _safe_action_text(prospect.get("description"), 1200),
        "why": "Promoted from the passive Prospect Reference Library for active Atlas follow-up.",
        "nextStep": _safe_action_text(next_step, 500) or "Review and qualify this promoted prospect.",
        "website": _safe_action_text(prospect.get("website"), 240),
        "linkedinUrl": _safe_action_text(prospect.get("linkedin_url"), 240),
        "address": address,
        "contacts": "\n".join([
            f"{_safe_action_text(contact.get('name'), 180)} | Prospect Contact | {_safe_action_text(contact.get('email'), 240)} |  |  | Imported from prospect reference. | "
            for contact in contacts[:12]
        ]),
        "teamMembers": [
            {
                "id": f"{clean_id}-CONTACT-{idx}",
                "name": _safe_action_text(contact.get("name"), 180),
                "relationshipRole": "Prospect Contact" if idx > 1 else "Primary Contact",
                "email": _safe_action_text(contact.get("email"), 240),
                "phone": "",
                "title": "",
                "jobTitle": "",
                "description": "Promoted from prospect reference.",
                "lastConversation": "",
            }
            for idx, contact in enumerate(contacts[:12], start=1)
        ],
        "sourceSystem": "Prospect Reference Library",
        "sourceProspectId": clean_id,
        "sourceFile": prospect.get("source_file", ""),
        "createdAt": now,
        "updatedAt": now,
    }
    crm_records.insert(0, record)
    _write_json_store(CRM_RECORDS_PATH, crm_records)
    _upsert_atlas_crm_db(record)

    prospect["promotedAt"] = now
    prospect["promotedCrmId"] = record["id"]
    _write_json_store(PROSPECT_REFERENCE_RECORDS_PATH, prospects)
    _upsert_prospect_reference_db(prospect)
    return {"ok": True, "record": record, "already_promoted": False}


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _fetch_customer_news(customer: str, location: str = "", limit: int = 8) -> tuple[str, list[dict]]:
    terms = [f'"{customer}"']
    if location:
        terms.append(location)
    terms.append("(news OR expansion OR contract OR funding OR hiring OR acquisition OR lawsuit OR leadership)")
    query = " ".join(terms)
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    response = requests.get(
        url,
        timeout=12,
        headers={"User-Agent": "VETCODE Atlas news scanner/1.0"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    items = []
    for item in root.findall(".//item")[: max(1, min(limit, 12))]:
        source = item.find("source")
        items.append(
            {
                "title": _strip_html(item.findtext("title", ""))[:260],
                "link": item.findtext("link", ""),
                "published": item.findtext("pubDate", ""),
                "source": _strip_html(source.text if source is not None else "")[:120],
                "summary": _strip_html(item.findtext("description", ""))[:500],
            }
        )
    return query, items


def _summarize_customer_news(customer: str, location: str, items: list[dict]) -> dict:
    if not items:
        return {
            "headline": f"No recent web/news signal found for {customer}.",
            "highlights": [],
            "recommended_action": "Keep the normal follow-up cadence and scan again before the next client touch.",
            "risk_level": "low",
        }
    fallback_highlights = [
        f"{item.get('title')} ({item.get('source') or 'news source'})"
        for item in items[:3]
        if item.get("title")
    ]
    try:
        client = getOpenAPIClient()
        payload = json.dumps(items[:8], ensure_ascii=False)
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an Atlas relationship research assistant. Read recent web/news search results for a customer. "
                        "Return compact JSON with headline, highlights array, recommended_action, and risk_level "
                        "where risk_level is low, medium, or high. Do not invent facts beyond the provided results."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Customer: {customer}\nLocation: {location or 'not specified'}\nNews results:\n{payload}",
                },
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        match = re.search(r"\{.*\}", content, re.S)
        parsed = json.loads(match.group(0) if match else content)
        return {
            "headline": _safe_action_text(parsed.get("headline"), 300) or f"Recent news scan for {customer}.",
            "highlights": [_safe_action_text(item, 260) for item in parsed.get("highlights", [])[:5]],
            "recommended_action": _safe_action_text(parsed.get("recommended_action"), 400) or "Review the linked results before the next touch.",
            "risk_level": _safe_action_text(parsed.get("risk_level"), 40).lower() or "low",
        }
    except Exception:
        return {
            "headline": f"Recent news scan found {len(items)} possible signal(s) for {customer}.",
            "highlights": fallback_highlights,
            "recommended_action": "Review the linked results and decide whether the next Atlas touch should reference one of these updates.",
            "risk_level": "medium" if fallback_highlights else "low",
        }


@app.post("/api/crm/news-scan")
def scan_crm_customer_news(
    customer: str = Form(default=""),
    location: str = Form(default=""),
    domain: str = Form(default="dev"),
):
    clean_customer = _safe_action_text(customer, 240)
    clean_location = _safe_action_text(location, 240)
    if not clean_customer:
        raise HTTPException(status_code=400, detail="Customer name is required for news scan.")
    try:
        query, items = _fetch_customer_news(clean_customer, clean_location)
    except Exception as exc:
        query = " ".join([part for part in [f'"{clean_customer}"', clean_location, "news customer update"] if part])
        items = []
        return {
            "ok": True,
            "domain": _domain_key(domain),
            "customer": clean_customer,
            "location": clean_location,
            "query": query,
            "scanned_at": _now_utc(),
            "summary": {
                "headline": f"News scan unavailable for {clean_customer}.",
                "highlights": [],
                "recommended_action": f"Web news lookup could not complete: {_safe_action_text(str(exc), 220)}",
                "risk_level": "low",
            },
            "items": items,
        }
    summary = _summarize_customer_news(clean_customer, clean_location, items)
    return {
        "ok": True,
        "domain": _domain_key(domain),
        "customer": clean_customer,
        "location": clean_location,
        "query": query,
        "scanned_at": _now_utc(),
        "summary": summary,
        "items": items,
    }


def _crm_briefing_contacts(record: dict) -> list[dict]:
    contacts = record.get("teamMembers") or record.get("players") or record.get("contacts")
    rows = []
    if isinstance(contacts, list):
        for item in contacts[:8]:
            if not isinstance(item, dict):
                continue
            name = _safe_action_text(item.get("name") or item.get("contact"), 160)
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "title": _safe_action_text(item.get("title") or item.get("jobTitle") or item.get("role"), 180),
                    "email": _safe_action_text(item.get("email"), 240),
                    "phone": _safe_action_text(item.get("phone"), 80),
                    "role": _safe_action_text(item.get("relationshipRole") or item.get("buying_role") or item.get("buyingRole") or item.get("persona"), 120),
                    "last_touch": _safe_action_text(item.get("lastConversation") or item.get("last_touch") or item.get("lastTouched"), 160),
                    "linkedin_url": _safe_action_text(item.get("linkedinUrl") or item.get("linkedin_url") or item.get("linkedin"), 400),
                    "description": _safe_action_text(item.get("description") or item.get("notes"), 300),
                }
            )
    if not rows and isinstance(contacts, str):
        for line in contacts.splitlines()[:8]:
            parts = [part.strip() for part in line.split("|")]
            name = _safe_action_text(parts[0] if len(parts) > 0 else "", 160)
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "role": _safe_action_text(parts[1] if len(parts) > 1 else "", 120),
                    "email": _safe_action_text(parts[2] if len(parts) > 2 else "", 240),
                    "phone": _safe_action_text(parts[3] if len(parts) > 3 else "", 80),
                    "title": _safe_action_text(parts[4] if len(parts) > 4 else "", 180),
                    "description": _safe_action_text(parts[5] if len(parts) > 5 else "", 300),
                    "last_touch": _safe_action_text(parts[6] if len(parts) > 6 else "", 160),
                    "linkedin_url": _safe_action_text(parts[7] if len(parts) > 7 else "", 400),
                }
            )
    if not rows:
        contact = _safe_action_text(record.get("contact"), 180)
        if contact:
            rows.append(
                {
                    "name": contact,
                    "title": "",
                    "email": _safe_action_text(record.get("email"), 240),
                    "phone": _safe_action_text(record.get("phone"), 80),
                    "role": "Primary contact",
                    "last_touch": _safe_action_text(record.get("lastTouched") or record.get("when"), 160),
                }
            )
    return rows


def _crm_pick_briefing_record(domain: str, customer: str = "") -> dict:
    clean_customer = _safe_action_text(customer, 240).lower()
    records = _radar_crm_records(domain, limit=200)
    if clean_customer:
        for record in records:
            name = _safe_action_text(record.get("customer"), 240).lower()
            record_id = _safe_action_text(record.get("id"), 240).lower()
            if clean_customer in {name, record_id} or clean_customer in name:
                return record
    def score(record: dict) -> float:
        days = _crm_days_since(record.get("lastTouched") or record.get("when"))
        missing_next = 10 if not _safe_action_text(record.get("nextStep"), 240) else 0
        try:
            value = min(12, float(record.get("value") or 0) / 25000)
        except Exception:
            value = 0
        try:
            strength = float(record.get("strength") or 0)
        except Exception:
            strength = 0
        stage = _safe_action_text(record.get("dealStage") or record.get("contractStatus") or record.get("status"), 80).lower()
        stage_score = 8 if any(word in stage for word in ["qualified", "candidate", "shortlist", "proposal"]) else 3
        return missing_next + min(18, days * 0.7) + value + strength + stage_score
    return sorted(records, key=score, reverse=True)[0] if records else {}


def _crm_briefing_recent_meetings(domain: str, customer: str) -> list[dict]:
    records = _read_json_store_with_demo(MEETING_RECORDS_PATH, [])
    if not isinstance(records, list):
        return []
    clean_domain = _domain_key(domain)
    customer_l = customer.lower()
    rows = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if clean_domain != "all" and _domain_key(item.get("domain", clean_domain)) != clean_domain:
            continue
        haystack = " ".join(str(item.get(key, "")) for key in ["client", "customer", "title", "meeting_title", "summary", "notes"]).lower()
        if customer_l and customer_l not in haystack:
            continue
        rows.append(
            {
                "title": _safe_action_text(item.get("title") or item.get("meeting_title") or "Meeting", 220),
                "date": _safe_action_text(item.get("date") or item.get("created_at") or item.get("when"), 80),
                "summary": _safe_action_text(item.get("summary") or item.get("notes") or item.get("readout"), 300),
            }
        )
    rows.sort(key=lambda item: item.get("date") or "", reverse=True)
    return rows[:5]


def _crm_briefing_open_roles(domain: str, customer: str) -> list[dict]:
    roles = []
    customer_l = customer.lower()
    for jd in _radar_jds(domain, limit=50):
        company = _safe_action_text(jd.get("company"), 220)
        if customer_l and customer_l not in company.lower() and company.lower() not in customer_l:
            continue
        roles.append(
            {
                "jd_id": _safe_action_text(jd.get("jd_id"), 80),
                "title": _safe_action_text(jd.get("title"), 220),
                "company": company,
                "skills": _radar_skill_terms(jd.get("jd_skills", {}))[:8],
            }
        )
    return roles[:5]


def _crm_briefing_time_invoice_signals(domain: str, customer: str) -> dict:
    clean_domain = _domain_key(domain)
    customer_l = customer.lower()
    entries = _read_json_store_with_demo(TIME_ENTRIES_PATH, [])
    time_hours = 0.0
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            if clean_domain != "all" and _domain_key(item.get("domain", clean_domain)) != clean_domain:
                continue
            haystack = " ".join(str(item.get(key, "")) for key in ["client", "customer", "project", "description"]).lower()
            if customer_l and customer_l not in haystack:
                continue
            try:
                time_hours += float(item.get("hours") or 0)
            except Exception:
                pass
    accounting = _accounting_store()
    invoices = accounting.get("invoices", []) if isinstance(accounting, dict) else []
    invoice_rows = []
    if isinstance(invoices, list):
        for invoice in invoices:
            if not isinstance(invoice, dict):
                continue
            if clean_domain != "all" and _domain_key(invoice.get("domain", clean_domain)) != clean_domain:
                continue
            haystack = " ".join(str(invoice.get(key, "")) for key in ["customer", "client", "customer_name", "bill_to"]).lower()
            if customer_l and customer_l not in haystack:
                continue
            invoice_rows.append(
                {
                    "invoice_id": _safe_action_text(invoice.get("invoice_id") or invoice.get("id"), 80),
                    "status": _safe_action_text(invoice.get("status"), 80),
                    "total": invoice.get("total") or invoice.get("billable") or invoice.get("amount") or 0,
                    "due_date": _safe_action_text(invoice.get("due_date") or invoice.get("dueDate"), 80),
                }
            )
    return {"time_hours": round(time_hours, 1), "invoices": invoice_rows[:5]}


@app.get("/api/crm/client-briefing")
def crm_client_intelligence_briefing(domain: str = "dev", customer: str = "", include_news: bool = False):
    clean_domain = _domain_key(domain)
    record = _crm_pick_briefing_record(clean_domain, customer)
    if not record:
        return {
            "ok": True,
            "domain": clean_domain,
            "generated_at": _now_utc(),
            "empty": True,
            "today_angle": "No Atlas team card is available yet. Add or seed an Atlas client first.",
            "signals": [],
            "touch_history": [],
        }
    customer_name = _safe_action_text(record.get("customer"), 240) or "Client"
    stage = _safe_action_text(record.get("dealStage") or record.get("contractStatus") or record.get("status"), 120) or "Discovery"
    days = _crm_days_since(record.get("lastTouched") or record.get("when"))
    contacts = _crm_briefing_contacts(record)
    public_signals = record.get("publicSignals") if isinstance(record.get("publicSignals"), list) else []
    roles = _crm_briefing_open_roles(clean_domain, customer_name)
    meetings = _crm_briefing_recent_meetings(clean_domain, customer_name)
    money_signals = _crm_briefing_time_invoice_signals(clean_domain, customer_name)
    primary_contact = contacts[0] if contacts else {}
    next_step = _safe_action_text(record.get("nextStep"), 360)
    risk = "Relationship is stale; no next step is recorded." if days >= 8 or not next_step else "Normal risk. Keep the next touch specific and tied to the open role."
    pitch = roles[0]["title"] if roles else _safe_action_text(record.get("dealTitle"), 220) or "the highest-priority open role"
    today_angle = (
        f"Lead with {customer_name}'s {stage} stage and propose a concrete next touch around {pitch}. "
        f"{'Refresh the relationship because the last touch is ' + str(days) + ' days old.' if days >= 8 else 'Use the recent Atlas context and keep the ask crisp.'}"
    )
    news = {}
    if include_news:
        try:
            query, items = _fetch_customer_news(customer_name, _safe_action_text(record.get("where") or record.get("location"), 180), limit=6)
            news = {"query": query, "items": items, "summary": _summarize_customer_news(customer_name, "", items)}
        except Exception as exc:
            news = {"error": _safe_action_text(str(exc), 240), "items": [], "summary": {"headline": "External scan could not complete.", "highlights": [], "recommended_action": "Continue from internal Atlas signals.", "risk_level": "low"}}
    signals = [
        {"label": "Deal stage", "value": stage},
        {"label": "Last touch", "value": f"{days} day(s) ago" if days < 999 else "Not recorded"},
        {"label": "Open roles", "value": str(len(roles))},
        {"label": "Approved/entered time", "value": f"{money_signals['time_hours']} hours"},
        {"label": "Invoices", "value": str(len(money_signals["invoices"]))},
    ]
    return {
        "ok": True,
        "domain": clean_domain,
        "generated_at": _now_utc(),
        "customer": {
            "id": _safe_action_text(record.get("id"), 120),
            "name": customer_name,
            "stage": stage,
            "owner": _safe_action_text(record.get("owner"), 120),
            "contact": primary_contact,
            "value": record.get("value") or 0,
            "days_since_touch": days,
        },
        "today_angle": today_angle,
        "who_to_contact": primary_contact.get("name") or _safe_action_text(record.get("contact"), 180) or "Add a named contact to the Atlas team card.",
        "role_or_candidate_to_pitch": pitch,
        "current_risk": risk,
        "suggested_next_touch": next_step or f"Book a 15-minute client touch to confirm current hiring priority for {pitch}.",
        "deal_stage_recommendation": "Move to Candidate Review or Shortlist Sent only after a named candidate is attached." if stage in {"Discovery", "Qualified"} else "Confirm the next owner and due date so the deal does not stall.",
        "signals": signals,
        "contacts": contacts,
        "public_signals": public_signals[:8],
        "open_roles": roles,
        "touch_history": meetings,
        "invoice_time": money_signals,
        "news": news,
        "links": {
            "crm": f"crm.html?domain={clean_domain}",
            "jobs": f"job-descriptions.html?domain={clean_domain}",
            "match": f"match-role.html?domain={clean_domain}",
            "client_comm": f"client-comm.html?domain={clean_domain}",
            "reports": f"reports.html?domain={clean_domain}",
        },
    }


def _parse_crm_datetime(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.replace("Z", ""), fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except Exception:
        return None


def _crm_days_since(value: str) -> int:
    parsed = _parse_crm_datetime(value)
    if not parsed:
        return 999
    return max(0, (datetime.utcnow() - parsed).days)


def _crm_contracts(record: dict) -> list[dict]:
    deals = record.get("deals")
    if isinstance(deals, list) and deals:
        contracts = []
        for deal in deals[:10]:
            if not isinstance(deal, dict):
                continue
            contracts.append(
                {
                    "name": deal.get("name") or deal.get("title") or record.get("dealTitle") or "Contract",
                    "stage": deal.get("stage") or record.get("dealStage") or "Discovery",
                    "value": deal.get("value") or record.get("value") or 0,
                    "owner": deal.get("owner") or record.get("owner") or "",
                    "contact": deal.get("contact") or record.get("contact") or "",
                    "probability": deal.get("probability") or record.get("dealProbability") or 0,
                    "close_date": deal.get("closeDate") or deal.get("close_date") or "",
                    "notes": deal.get("notes") or "",
                }
            )
        if contracts:
            return contracts
    return [
        {
            "name": record.get("dealTitle") or record.get("customer") or "Relationship contract",
            "stage": record.get("contractStatus") or record.get("dealStage") or "Discovery",
            "value": record.get("value") or 0,
            "owner": record.get("owner") or "",
            "contact": record.get("contact") or "",
            "probability": record.get("dealProbability") or 0,
            "close_date": record.get("closeDate") or "",
            "notes": record.get("what") or "",
        }
    ]


def _sales_todo_for_record(record: dict) -> list[dict]:
    tasks = []
    days = _crm_days_since(record.get("lastTouched") or record.get("when"))
    if days >= 7:
        tasks.append(
            {
                "priority": "high" if days >= 14 else "medium",
                "type": "touch",
                "title": f"Touch {record.get('customer') or 'account'}",
                "reason": f"No recorded touch in {days} days.",
                "record_id": record.get("id", ""),
            }
        )
    if not _safe_action_text(record.get("nextStep"), 240):
        tasks.append(
            {
                "priority": "high",
                "type": "next_step",
                "title": f"Set next action for {record.get('customer') or 'account'}",
                "reason": "Atlas needs a clear next action.",
                "record_id": record.get("id", ""),
            }
        )
    if float(record.get("value") or 0) > 100000 and days >= 3:
        tasks.append(
            {
                "priority": "medium",
                "type": "contract",
                "title": f"Advance contract for {record.get('customer') or 'account'}",
                "reason": "High-value contract needs visible movement.",
                "record_id": record.get("id", ""),
            }
        )
    return tasks


@app.get("/api/sales-crm/portal")
def sales_crm_portal(domain: str = "dev", rep: str = "", territory: str = "", limit: int = 200):
    records = _atlas_crm_records_db(domain, include_archived=False, limit=2000)
    if records is None:
        records = _read_json_store_with_demo(CRM_RECORDS_PATH, [])
    if not isinstance(records, list):
        records = []
    clean_domain = _domain_key(domain)
    clean_rep = _safe_action_text(rep, 120).lower()
    clean_territory = _safe_action_text(territory, 160).lower()
    rows = []
    reps = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        if _crm_record_archived(item):
            continue
        if clean_domain != "all" and _domain_key(item.get("domain", "dev")) != clean_domain:
            continue
        owner = _safe_action_text(item.get("owner"), 120)
        if owner:
            reps[owner] = reps.get(owner, 0) + 1
        row_territory = _safe_action_text(item.get("territory") or item.get("industry") or "", 160)
        if clean_rep and owner.lower() != clean_rep:
            continue
        if clean_territory:
            haystack = " ".join(
                [
                    row_territory,
                    _safe_action_text(item.get("customer"), 240),
                    _safe_action_text(item.get("what"), 500),
                    _safe_action_text(item.get("why"), 500),
                ]
            ).lower()
            if clean_territory not in haystack:
                continue
        enriched = {**item}
        enriched["territory"] = row_territory or "Unassigned territory"
        enriched["contracts"] = _crm_contracts(item)
        enriched["daysSinceTouch"] = _crm_days_since(item.get("lastTouched") or item.get("when"))
        rows.append(enriched)
    rows = sorted(rows, key=lambda item: (item.get("daysSinceTouch", 0), item.get("value") or 0), reverse=True)
    todos = []
    for row in rows:
        todos.extend(_sales_todo_for_record(row))
    return {
        "ok": True,
        "domain": clean_domain,
        "rep": rep,
        "territory": territory,
        "reps": [{"name": name, "accounts": count} for name, count in sorted(reps.items())],
        "records": rows[: max(1, min(limit, 500))],
        "todos": todos[:50],
        "summary": {
            "accounts": len(rows),
            "contracts": sum(len(row.get("contracts") or []) for row in rows),
            "pipeline": round(sum(float(row.get("value") or 0) for row in rows), 2),
            "attention": sum(1 for row in rows if row.get("daysSinceTouch", 0) >= 7 or not row.get("nextStep")),
        },
    }


@app.post("/api/sales-crm/account")
def update_sales_crm_account(
    record_id: str = Form(default=""),
    domain: str = Form(default="dev"),
    rep: str = Form(default=""),
    territory: str = Form(default=""),
    customer: str = Form(default=""),
    contact: str = Form(default=""),
    email: str = Form(default=""),
    value: str = Form(default="0"),
    contract_status: str = Form(default=""),
    next_step: str = Form(default=""),
    touch_channel: str = Form(default=""),
    touch_outcome: str = Form(default=""),
    touch_notes: str = Form(default=""),
):
    records = _atlas_crm_records_db(domain, include_archived=True, limit=2000)
    if records is None:
        records = _read_json_store(CRM_RECORDS_PATH, [])
    if not isinstance(records, list):
        records = []
    clean_domain = _domain_key(domain)
    clean_id = _safe_action_text(record_id, 120) or _safe_token("CRM-SALES")
    now = _now_utc()
    record = None
    for item in records:
        if isinstance(item, dict) and item.get("id") == clean_id:
            record = item
            break
    if record is None:
        record = {"id": clean_id, "createdAt": now, "domain": clean_domain}
        records.insert(0, record)
    record["domain"] = clean_domain
    if rep:
        record["owner"] = _safe_action_text(rep, 120)
    if territory:
        record["territory"] = _safe_action_text(territory, 160)
    if customer:
        record["customer"] = _safe_action_text(customer, 240)
    if contact:
        record["contact"] = _safe_action_text(contact, 240)
    if email:
        record["email"] = _safe_action_text(email, 240)
    if value:
        try:
            record["value"] = round(float(str(value).replace(",", "").replace("$", "") or 0), 2)
        except Exception:
            record["value"] = record.get("value") or 0
    if contract_status:
        record["contractStatus"] = _safe_action_text(contract_status, 80)
        record["dealStage"] = record["contractStatus"]
    if next_step:
        record["nextStep"] = _safe_action_text(next_step, 500)
    if touch_channel or touch_outcome or touch_notes:
        record["lastTouched"] = now[:16]
        record["when"] = now[:16]
        history = record.get("touchHistory") if isinstance(record.get("touchHistory"), list) else []
        history.insert(
            0,
            {
                "at": now,
                "channel": _safe_action_text(touch_channel, 80) or "Update",
                "outcome": _safe_action_text(touch_outcome, 160),
                "notes": _safe_action_text(touch_notes, 1200),
                "rep": _safe_action_text(rep, 120),
            },
        )
        record["touchHistory"] = history[:50]
        note = _safe_action_text(touch_notes, 1200)
        if note:
            prior = _safe_action_text(record.get("history"), 3000)
            record["history"] = f"{now} - {note}" + (f"\n{prior}" if prior else "")
    record["updatedAt"] = now
    record["salesPortalUpdatedAt"] = now
    _write_json_store(CRM_RECORDS_PATH, records)
    _upsert_atlas_crm_db(record)
    return {"ok": True, "record": record}


@app.get("/api/meetings/archive")
def list_meeting_records(domain: str = "dev", profile_id: str = "", limit: int = 200):
    records = _read_json_store_with_demo(MEETING_RECORDS_PATH, [])
    wanted_domain = _domain_key(domain)
    if not isinstance(records, list):
        records = []
    if wanted_domain != "all":
        records = [item for item in records if _domain_key(item.get("domain", "dev")) == wanted_domain]
    if profile_id:
        records = [item for item in records if str(item.get("profileId") or item.get("candidateId") or "") == str(profile_id)]
    records = sorted(records, key=lambda item: item.get("updatedAt") or item.get("meetingAt") or item.get("createdAt") or "", reverse=True)
    return {"ok": True, "records": records[: max(1, min(limit, 500))]}


def _profile_completion_status_for_onboarding(profile_id: str, profile_data: dict) -> dict:
    profile_data = profile_data if isinstance(profile_data, dict) else {}
    core_profile = profile_data.get("profile") or {}
    skills = profile_data.get("skills") or []
    technical_skills = profile_data.get("technicalSkills") or []
    portfolio_experience = profile_data.get("portfolioExperience") or []
    personality = profile_data.get("personality") or []
    cultural_experience = profile_data.get("culturalExperience") or []

    def _level_value(item):
        try:
            return float((item or {}).get("level") or 0)
        except (TypeError, ValueError):
            return 0

    has_regular_profile = bool(core_profile.get("title")) and bool(
        core_profile.get("description")
        or skills
        or technical_skills
        or any(item and (item.get("description") or item.get("mainrole")) for item in portfolio_experience)
    )
    has_personality = any(item and item.get("title") and item.get("score") for item in personality)
    has_culture = any(item and item.get("title") and _level_value(item) > 0 for item in cultural_experience)
    checks = [has_regular_profile, has_personality, has_culture]
    missing = []
    if not has_regular_profile:
        missing.append("regular profile")
    if not has_personality:
        missing.append("personality survey")
    if not has_culture:
        missing.append("culture profile")
    return {
        "profileId": str(profile_id or ""),
        "complete": all(checks),
        "state": "complete" if all(checks) else "partial" if any(checks) else "missing",
        "missing": missing,
        "hasRegularProfile": has_regular_profile,
        "hasPersonality": has_personality,
        "hasCulture": has_culture,
        "name": " ".join(
            part for part in [core_profile.get("firstName"), core_profile.get("lastName")] if part
        ).strip(),
        "email": core_profile.get("email") or "",
        "title": core_profile.get("title") or "",
    }


def _completed_profile_for_onboarding(profile_id: str, domain: str) -> tuple[dict, dict, str]:
    clean_domain = _domain_key(domain)
    if not profile_id:
        raise HTTPException(
            status_code=400,
            detail="Select a completed profile before creating onboarding.",
        )
    try:
        actual_domain = _domain_key(candidates.getCandidateDomain(profile_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} was not found.") from exc
    if clean_domain != "all" and actual_domain != clean_domain:
        raise HTTPException(
            status_code=400,
            detail=f"Profile {profile_id} belongs to {actual_domain}, not {clean_domain}.",
        )
    try:
        profile_data = candidates.getProfile(profile_id)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Could not load profile {profile_id}.") from exc
    completion = _profile_completion_status_for_onboarding(profile_id, profile_data)
    if not completion["complete"]:
        missing = ", ".join(completion["missing"]) or "profile completion pieces"
        raise HTTPException(
            status_code=400,
            detail=f"Profile must be complete before onboarding. Missing: {missing}.",
        )
    return profile_data, completion, actual_domain


@app.post("/api/onboarding/start")
def start_onboarding(
    profile_id: str = Form(default=""),
    candidate_name: str = Form(default=""),
    email: str = Form(default=""),
    title: str = Form(default=""),
    domain: str = Form(default="dev"),
    start_day: str = Form(default=""),
    source_record_json: str = Form(default="{}"),
):
    profile_data, completion, actual_domain = _completed_profile_for_onboarding(profile_id, domain)
    core_profile = profile_data.get("profile") or {}
    profile_name = completion.get("name") or candidate_name or email or "Candidate"
    profile_email = completion.get("email") or email or ""
    profile_title = completion.get("title") or title or ""

    records = _read_json_store(ONBOARDING_RECORDS_PATH, {})
    now = _now_utc()
    token = ""
    for existing_token, record in records.items():
        if profile_id and record.get("profile_id") == profile_id:
            token = existing_token
            break
        if profile_email and (record.get("email") or "").lower() == profile_email.lower():
            token = existing_token
            break
    token = token or _safe_token("ONB")
    try:
        source_record = json.loads(source_record_json) if source_record_json else {}
    except Exception:
        source_record = {"raw": source_record_json}
    record = records.get(token, {})
    record.update({
        "token": token,
        "profile_id": profile_id,
        "candidate_name": profile_name,
        "email": profile_email,
        "title": profile_title,
        "domain": actual_domain,
        "start_day": start_day or record.get("start_day", ""),
        "status": "hire_started",
        "profile_completion": completion,
        "profile_source": {
            "firstName": core_profile.get("firstName") or "",
            "lastName": core_profile.get("lastName") or "",
            "publicUrl": core_profile.get("publicUrl") or "",
            "linkedinUrl": core_profile.get("linkedinUrl") or "",
            "city": core_profile.get("city") or "",
            "state": core_profile.get("state") or "",
            "country": core_profile.get("country") or "",
        },
        "source_record": source_record if isinstance(source_record, dict) else {"value": source_record},
        "recipient": os.getenv("HEIDI_NAME", "Heidi at DevReady"),
        "recipient_email": os.getenv("HEIDI_EMAIL", "heidi@devready.io"),
        "created_at": record.get("created_at") or now,
        "updated_at": now,
    })
    records[token] = record
    _write_json_store(ONBOARDING_RECORDS_PATH, records)

    events = _read_json_store(WORKFLOW_EVENTS_PATH, [])
    events.insert(0, {
        "id": _safe_token("EVT"),
        "profile_id": profile_id,
        "candidate_name": profile_name,
        "email": profile_email,
        "domain": actual_domain,
        "event_type": "hire_onboarding_started",
        "status": "hire_started",
        "notes": "Onboarding link created from completed profile.",
        "payload": {"onboarding_token": token},
        "created_at": now,
        "updated_at": now,
    })
    _write_json_store(WORKFLOW_EVENTS_PATH, events[:1000])
    return {
        "ok": True,
        "record": record,
        "onboarding_link": f"/ui/pages/onboarding.html?token={token}",
        "time_entry_link": f"/ui/pages/time-entry.html?token={token}",
    }


@app.get("/api/onboarding/admin")
def get_onboarding_admin(domain: str = "all"):
    records = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {})
    clean_domain = _domain_key(domain)
    people = []
    for token, record in records.items():
        record_domain = _domain_key(record.get("domain", "dev"))
        if clean_domain != "all" and record_domain != clean_domain:
            continue
        item = dict(record)
        item["token"] = token
        item["onboarding_link"] = f"/ui/pages/onboarding.html?token={token}"
        item["time_entry_link"] = f"/ui/pages/time-entry.html?token={token}"
        people.append(item)
    people.sort(
        key=lambda item: (
            item.get("updated_at") or item.get("created_at") or "",
            item.get("candidate_name") or item.get("legal_name") or item.get("email") or "",
        ),
        reverse=True,
    )
    return {
        "ok": True,
        "people": people,
        "count": len(people),
    }


@app.get("/api/candidate/portal")
def candidate_portal(
    profile_id: str = "",
    email: str = "",
    domain: str = "dev",
):
    clean_domain = _domain_key(domain)
    profile_id = _safe_action_text(profile_id, 80)
    email_key = _normalize_user_key(email)
    onboarding = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {})
    matching_onboarding = None
    for token, record in onboarding.items():
        record_domain = _domain_key(record.get("domain", "dev"))
        record_profile = str(record.get("profile_id") or "")
        record_email = _normalize_user_key(record.get("email", ""))
        if record_domain != clean_domain:
            continue
        if profile_id and record_profile == profile_id:
            matching_onboarding = {**record, "token": token}
            break
        if email_key and record_email == email_key:
            matching_onboarding = {**record, "token": token}
            break

    notes_count = 0
    role_feedback_count = 0
    if profile_id:
        notes_data = _read_profile_notes_store()
        record = _profile_notes_record(notes_data, profile_id, clean_domain)
        notes_count = len(record.get("notes", []))
        role_feedback_count = len([
            link
            for link in notes_data.get("links", {}).values()
            if str(link.get("profile_id")) == profile_id and _domain_key(link.get("domain")) == clean_domain
        ])

    links = {
        "profile": f"/ui/pages/profile-preview-edit.html?domain={clean_domain}&profileId={quote_plus(profile_id)}" if profile_id else "",
        "status": f"/ui/pages/candidate-status.html?domain={clean_domain}",
        "onboarding": "",
        "time_entry": "",
    }
    if matching_onboarding:
        token = matching_onboarding.get("token", "")
        links["onboarding"] = f"/ui/pages/onboarding.html?token={quote_plus(token)}"
        links["time_entry"] = f"/ui/pages/time-entry.html?token={quote_plus(token)}"
    return {
        "ok": True,
        "domain": clean_domain,
        "profile_id": profile_id,
        "email": email,
        "onboarding": matching_onboarding,
        "links": links,
        "counts": {
            "private_notes": notes_count,
            "role_feedback_links": role_feedback_count,
        },
    }


@app.get("/api/onboarding/candidates")
def get_onboarding_candidates(domain: str = "dev", limit: int = 250):
    clean_domain = _domain_key(domain)
    safe_limit = max(10, min(int(limit or 250), 500))
    try:
        ready = candidates.listOnboardingReadyProfiles(clean_domain, safe_limit)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Candidate list failed: {exc}")
    people = ready.get("profiles", [])[:safe_limit] if isinstance(ready, dict) else []
    skipped_incomplete = ready.get("skipped_incomplete", 0) if isinstance(ready, dict) else 0
    return {
        "ok": True,
        "domain": clean_domain,
        "candidates": people,
        "count": len(people),
        "skipped_incomplete": skipped_incomplete,
        "require_completed_profile": True,
    }


@app.get("/api/onboarding/{token}")
def get_onboarding(token: str):
    records = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {})
    record = records.get(token)
    if not record:
        raise HTTPException(status_code=404, detail="Onboarding record not found.")
    return {"ok": True, "record": record}


@app.post("/api/onboarding/{token}")
def submit_onboarding(
    token: str,
    legal_name: str = Form(default=""),
    preferred_name: str = Form(default=""),
    email: str = Form(default=""),
    phone: str = Form(default=""),
    home_address: str = Form(default=""),
    start_day: str = Form(default=""),
    bank_name: str = Form(default=""),
    account_type: str = Form(default=""),
    routing_number: str = Form(default=""),
    account_last4: str = Form(default=""),
    payroll_packet_confirmed: str = Form(default="false"),
    emergency_contact: str = Form(default=""),
    notes: str = Form(default=""),
):
    records = _read_json_store(ONBOARDING_RECORDS_PATH, {})
    record = records.get(token)
    if not record:
        raise HTTPException(status_code=404, detail="Onboarding record not found.")
    now = _now_utc()
    record.update({
        "legal_name": legal_name,
        "preferred_name": preferred_name,
        "email": email or record.get("email", ""),
        "phone": phone,
        "home_address": home_address,
        "start_day": start_day or record.get("start_day", ""),
        "bank_name": bank_name,
        "account_type": account_type,
        "routing_number": routing_number,
        "account_last4": account_last4[-4:] if account_last4 else "",
        "payroll_packet_confirmed": str(payroll_packet_confirmed).lower() in {"true", "1", "yes", "on"},
        "emergency_contact": emergency_contact,
        "notes": notes,
        "status": "paperwork_submitted",
        "submitted_at": now,
        "updated_at": now,
    })
    records[token] = record
    _write_json_store(ONBOARDING_RECORDS_PATH, records)
    return {
        "ok": True,
        "record": record,
        "message": f"Onboarding saved and queued for {record.get('recipient', 'Heidi at DevReady')}.",
    }


@app.post("/api/time-entry")
def submit_time_entry(
    token: str = Form(default=""),
    profile_id: str = Form(default=""),
    candidate_name: str = Form(default=""),
    email: str = Form(default=""),
    domain: str = Form(default=""),
    week_start: str = Form(default=""),
    entries_json: str = Form(default=""),
    work_date: str = Form(default=""),
    hours: str = Form(default=""),
    client: str = Form(default=""),
    project: str = Form(default=""),
    summary: str = Form(default=""),
    blockers: str = Form(default=""),
):
    entries = _read_json_store(TIME_ENTRIES_PATH, [])
    onboarding = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {}).get(token, {}) if token else {}
    now = _now_utc()
    person_profile_id = profile_id or onboarding.get("profile_id", "")
    person_name = candidate_name or onboarding.get("candidate_name", "") or onboarding.get("legal_name", "")
    person_email = email or onboarding.get("email", "")
    entry_domain = domain or onboarding.get("domain", "dev")
    resource_context = _resource_for_time_person({
        "profile_id": person_profile_id,
        "token": token,
        "email": person_email,
        "candidate_name": person_name,
        "domain": entry_domain,
    })
    entry_client = client or onboarding.get("client", "") or resource_context.get("client", "")
    entry_project = project or onboarding.get("project", "") or resource_context.get("project", "") or resource_context.get("role", "")
    recipient = os.getenv("HEIDI_NAME", "Heidi at DevReady")
    recipient_email = os.getenv("HEIDI_EMAIL", "heidi@devready.io")

    def clean_hours(value):
        try:
            parsed = float(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        return round(max(0, min(parsed, 24)), 2)

    submitted_entries = []
    if entries_json:
        try:
            daily_rows = json.loads(entries_json)
        except Exception:
            raise HTTPException(status_code=400, detail="entries_json must be valid JSON.")
        if not isinstance(daily_rows, list):
            raise HTTPException(status_code=400, detail="entries_json must be a list of daily entries.")
        for row in daily_rows:
            if not isinstance(row, dict):
                continue
            row_hours = clean_hours(row.get("hours"))
            row_summary = str(row.get("summary") or "").strip()
            row_date = str(row.get("work_date") or "").strip()
            if row_hours <= 0 and not row_summary:
                continue
            submitted_entries.append({
                "id": f"{_safe_token('TIM')}-{len(submitted_entries) + 1}",
                "token": token,
                "profile_id": person_profile_id,
                "candidate_name": person_name,
                "email": person_email,
                "domain": entry_domain,
                "week_start": week_start,
                "work_date": row_date,
                "hours": row_hours,
                "client": entry_client,
                "project": entry_project,
                "summary": row_summary,
                "blockers": str(row.get("blockers") or blockers or "").strip(),
                "status": "submitted_to_devready",
                "recipient": recipient,
                "recipient_email": recipient_email,
                "created_at": now,
                "updated_at": now,
            })
    else:
        submitted_entries.append({
            "id": f"{_safe_token('TIM')}-1",
            "token": token,
            "profile_id": person_profile_id,
            "candidate_name": person_name,
            "email": person_email,
            "domain": entry_domain,
            "week_start": week_start,
            "work_date": work_date,
            "hours": clean_hours(hours),
            "client": entry_client,
            "project": entry_project,
            "summary": summary,
            "blockers": blockers,
            "status": "submitted_to_devready",
            "recipient": recipient,
            "recipient_email": recipient_email,
            "created_at": now,
            "updated_at": now,
        })

    if not submitted_entries:
        raise HTTPException(status_code=400, detail="Add hours or a short description for at least one day.")

    entries = submitted_entries + entries
    _write_json_store(TIME_ENTRIES_PATH, entries[:2000])
    return {
        "ok": True,
        "entries": submitted_entries,
        "entry": submitted_entries[0],
        "message": f"Time entry recorded for {recipient}.",
    }


@app.get("/api/time-entry/admin")
def get_time_entry_admin(
    domain: str = "all",
    week_start: str = "",
    status: str = "all",
):
    entries = _read_json_store_with_demo(TIME_ENTRIES_PATH, [])
    clean_domain = _domain_key(domain)
    domain_entries = [
        entry for entry in entries
        if clean_domain == "all" or _domain_key(entry.get("domain", "dev")) == clean_domain
    ]
    filtered = []
    for entry in domain_entries:
        if week_start and entry.get("week_start") != week_start:
            continue
        if status != "all" and entry.get("status", "") != status:
            continue
        filtered.append(entry)

    groups = {}
    for entry in filtered:
        key = "|".join([
            entry.get("week_start") or "",
            entry.get("profile_id") or "",
            entry.get("token") or "",
            entry.get("email") or "",
        ])
        group = groups.setdefault(key, {
            "week_start": entry.get("week_start") or "",
            "profile_id": entry.get("profile_id") or "",
            "token": entry.get("token") or "",
            "candidate_name": entry.get("candidate_name") or "Staff member",
            "email": entry.get("email") or "",
            "domain": entry.get("domain", "dev"),
            "client": entry.get("client") or "",
            "project": entry.get("project") or "",
            "status": entry.get("status") or "submitted_to_devready",
            "processed_at": "",
            "processed_by": "",
            "processed_reference": "",
            "processed_note": "",
            "total_hours": 0,
            "entries": [],
        })
        try:
            group["total_hours"] += float(entry.get("hours") or 0)
        except (TypeError, ValueError):
            pass
        group["entries"].append(entry)
        if entry.get("status") == "processed_for_payment":
            group["status"] = "processed_for_payment"
        if entry.get("processed_at") and (
            not group.get("processed_at") or str(entry.get("processed_at")) > str(group.get("processed_at"))
        ):
            group["processed_at"] = entry.get("processed_at")
        if entry.get("processed_by"):
            group["processed_by"] = entry.get("processed_by")
        if entry.get("processed_reference"):
            group["processed_reference"] = entry.get("processed_reference")
        if entry.get("processed_note"):
            group["processed_note"] = entry.get("processed_note")

    grouped = list(groups.values())
    for group in grouped:
        group["total_hours"] = round(group["total_hours"], 2)
        group["entries"].sort(key=lambda item: item.get("work_date") or "")
    grouped.sort(key=lambda item: (item.get("week_start") or "", item.get("candidate_name") or ""), reverse=True)

    candidate_totals = {}
    for entry in domain_entries:
        key = "|".join([
            entry.get("profile_id") or "",
            entry.get("token") or "",
            entry.get("email") or "",
            entry.get("candidate_name") or "Staff member",
        ])
        total = candidate_totals.setdefault(key, {
            "profile_id": entry.get("profile_id") or "",
            "token": entry.get("token") or "",
            "candidate_name": entry.get("candidate_name") or "Staff member",
            "email": entry.get("email") or "",
            "domain": entry.get("domain", "dev"),
            "total_hours": 0,
            "processed_hours": 0,
            "open_hours": 0,
            "weeks": set(),
            "latest_week": "",
        })
        try:
            hours_value = float(entry.get("hours") or 0)
        except (TypeError, ValueError):
            hours_value = 0
        total["total_hours"] += hours_value
        if entry.get("status") == "processed_for_payment":
            total["processed_hours"] += hours_value
        else:
            total["open_hours"] += hours_value
        if entry.get("week_start"):
            total["weeks"].add(entry.get("week_start"))
            if str(entry.get("week_start")) > str(total.get("latest_week") or ""):
                total["latest_week"] = entry.get("week_start")

    candidate_total_rows = []
    for row in candidate_totals.values():
        row["total_hours"] = round(row["total_hours"], 2)
        row["processed_hours"] = round(row["processed_hours"], 2)
        row["open_hours"] = round(row["open_hours"], 2)
        row["week_count"] = len(row["weeks"])
        row.pop("weeks", None)
        candidate_total_rows.append(row)
    candidate_total_rows.sort(key=lambda item: (item.get("total_hours") or 0, item.get("candidate_name") or ""), reverse=True)

    return {
        "ok": True,
        "groups": grouped,
        "entries": filtered,
        "total_hours": round(sum(group["total_hours"] for group in grouped), 2),
        "processed_hours": round(
            sum(group["total_hours"] for group in grouped if group.get("status") == "processed_for_payment"),
            2,
        ),
        "staff_count": len(grouped),
        "candidate_totals": candidate_total_rows,
        "candidate_total_hours": round(sum(row["total_hours"] for row in candidate_total_rows), 2),
    }


def _money_float(value, default: float = 0) -> float:
    try:
        return round(float(str(value or "").replace("$", "").replace(",", "").strip() or default), 2)
    except (TypeError, ValueError):
        return round(default, 2)


def _accounting_store() -> dict:
    db_store = _accounting_store_db()
    if isinstance(db_store, dict):
        db_store.setdefault("resources", [])
        db_store.setdefault("invoices", [])
        db_store.setdefault("expenses", [])
        return db_store
    fixture = _read_demo_fixture(os.path.basename(ACCOUNTING_RECORDS_PATH), {})
    local = _read_json_store(ACCOUNTING_RECORDS_PATH, {})
    store = {}
    for source in [fixture if isinstance(fixture, dict) else {}, local if isinstance(local, dict) else {}]:
        for key in ["resources", "invoices", "expenses"]:
            existing = {str(item.get("id") or json.dumps(item, sort_keys=True)): item for item in store.get(key, []) if isinstance(item, dict)}
            for item in source.get(key, []) if isinstance(source.get(key, []), list) else []:
                if isinstance(item, dict):
                    existing[str(item.get("id") or json.dumps(item, sort_keys=True))] = item
            store[key] = list(existing.values())
    store.setdefault("resources", [])
    store.setdefault("invoices", [])
    store.setdefault("expenses", [])
    return store


def _accounting_domain_rows(rows: list, domain: str) -> list:
    clean_domain = _domain_key(domain)
    return [
        item for item in rows if clean_domain == "all" or _domain_key(item.get("domain", "dev")) == clean_domain
    ]


def _date_in_period(value: str, period_start: str = "", period_end: str = "") -> bool:
    raw = _safe_action_text(value, 40)[:10]
    if not raw:
        return True
    if period_start and raw < period_start[:10]:
        return False
    if period_end and raw > period_end[:10]:
        return False
    return True


def _resource_lookup(resources: list) -> dict:
    resource_keyed = {}
    for resource in resources:
        for key in [resource.get("profile_id"), resource.get("token"), resource.get("email"), resource.get("name")]:
            if key:
                resource_keyed[str(key).strip().lower()] = resource
    return resource_keyed


def _resource_for_time_entry(entry: dict, resource_keyed: dict) -> dict:
    for key in [entry.get("profile_id"), entry.get("token"), entry.get("email"), entry.get("candidate_name")]:
        if key and str(key).strip().lower() in resource_keyed:
            return resource_keyed[str(key).strip().lower()]
    return {}


def _resource_for_time_person(person: dict) -> dict:
    try:
        clean_domain = _domain_key((person or {}).get("domain", "dev"))
        resources = _accounting_domain_rows(_accounting_store().get("resources", []), clean_domain)
        return _resource_for_time_entry(person or {}, _resource_lookup(resources))
    except Exception:
        return {}


def _time_row_from_entry(entry: dict, resource: dict) -> dict:
    try:
        hours = float(entry.get("hours") or 0)
    except (TypeError, ValueError):
        hours = 0
    bill_rate = _money_float((resource or {}).get("bill_rate"))
    cost_rate = _money_float((resource or {}).get("cost_rate"))
    entry_cost = round(hours * cost_rate, 2)
    entry_billable = round(hours * bill_rate, 2)
    return {
        "id": entry.get("id", ""),
        "candidate_name": entry.get("candidate_name") or (resource or {}).get("name") or "Staff member",
        "email": entry.get("email") or (resource or {}).get("email") or "",
        "hours": round(hours, 2),
        "bill_rate": bill_rate,
        "cost_rate": cost_rate,
        "billable_value": entry_billable,
        "labor_cost": entry_cost,
        "status": entry.get("status") or "",
        "week_start": entry.get("week_start") or "",
        "work_date": entry.get("work_date") or entry.get("week_start") or "",
        "crm_customer_id": entry.get("crm_customer_id") or (resource or {}).get("crm_customer_id") or "",
        "client": entry.get("client") or (resource or {}).get("client") or "",
        "project": entry.get("project") or "",
        "summary": entry.get("summary") or "",
        "token": entry.get("token") or (resource or {}).get("token") or "",
        "profile_id": entry.get("profile_id") or (resource or {}).get("profile_id") or "",
    }


def _accounting_summary_for_domain(domain: str = "dev", period_start: str = "", period_end: str = "") -> dict:
    store = _accounting_store()
    clean_domain = _domain_key(domain)
    crm_customers = _crm_customer_rows(clean_domain)
    crm_by_id = {customer["id"].lower(): customer for customer in crm_customers}
    crm_by_name = {customer["name"].lower(): customer for customer in crm_customers}

    def _linked_customer(value: str = "", crm_id: str = "") -> dict:
        clean_id = _safe_action_text(crm_id, 120).lower()
        clean_value = _safe_action_text(value, 240).lower()
        return crm_by_id.get(clean_id) or crm_by_name.get(clean_value) or {}

    def _with_customer_link(row: dict) -> dict:
        copied = dict(row or {})
        customer = _linked_customer(copied.get("client"), copied.get("crm_customer_id"))
        if customer:
            copied["crm_customer_id"] = customer["id"]
            copied["client"] = customer["name"]
            copied.setdefault("client_email", customer.get("email", ""))
        return copied

    resources = [_with_customer_link(item) for item in _accounting_domain_rows(store.get("resources", []), clean_domain)]
    invoices_all = [_with_customer_link(item) for item in _accounting_domain_rows(store.get("invoices", []), clean_domain)]
    expenses_all = _accounting_domain_rows(store.get("expenses", []), clean_domain)
    time_entries_all = _accounting_domain_rows(_read_json_store_with_demo(TIME_ENTRIES_PATH, []), clean_domain)
    invoices = [
        invoice for invoice in invoices_all
        if _date_in_period(invoice.get("invoice_date") or invoice.get("created_at"), period_start, period_end)
    ]
    expenses = [
        expense for expense in expenses_all
        if _date_in_period(expense.get("date") or expense.get("created_at"), period_start, period_end)
    ]
    time_entries = [
        entry for entry in time_entries_all
        if _date_in_period(entry.get("work_date") or entry.get("week_start") or entry.get("created_at"), period_start, period_end)
    ]
    resource_keyed = _resource_lookup(resources)
    onboarding = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {})
    if isinstance(onboarding, dict):
        onboarding_people = []
        for token, record in onboarding.items():
            if clean_domain != "all" and _domain_key(record.get("domain", "dev")) != clean_domain:
                continue
            person = {**record, "token": token}
            linked_resource = _resource_for_time_entry(
                {
                    "profile_id": person.get("profile_id", ""),
                    "token": token,
                    "email": person.get("email", ""),
                    "candidate_name": person.get("candidate_name") or person.get("legal_name", ""),
                },
                resource_keyed,
            )
            if linked_resource:
                person["resource_id"] = linked_resource.get("id", "")
                person["crm_customer_id"] = linked_resource.get("crm_customer_id", "")
                person["client"] = linked_resource.get("client", "")
                person["client_email"] = linked_resource.get("client_email", "")
                person["bill_rate"] = linked_resource.get("bill_rate", "")
                person["cost_rate"] = linked_resource.get("cost_rate", "")
                person["resource_status"] = linked_resource.get("status", "")
                person["resource_notes"] = linked_resource.get("notes", "")
            onboarding_people.append(person)
    else:
        onboarding_people = []

    labor_cost = 0.0
    billable_value = 0.0
    time_rows = []
    for entry in time_entries:
        row = _time_row_from_entry(entry, _resource_for_time_entry(entry, resource_keyed))
        labor_cost += row["labor_cost"]
        billable_value += row["billable_value"]
        time_rows.append(row)

    invoice_total = round(sum(_money_float(invoice.get("total")) for invoice in invoices if invoice.get("status") != "void"), 2)
    paid_total = round(sum(_money_float(invoice.get("total")) for invoice in invoices if invoice.get("status") == "paid"), 2)
    receivable_total = round(sum(_money_float(invoice.get("total")) for invoice in invoices if invoice.get("status") in {"draft", "sent", "viewed", "due", "overdue"}), 2)
    expense_total = round(sum(_money_float(expense.get("amount")) for expense in expenses), 2)
    gross_profit = round(invoice_total - labor_cost, 2)
    net_income = round(gross_profit - expense_total, 2)
    unpaid_labor = round(sum(row["labor_cost"] for row in time_rows if row.get("status") != "processed_for_payment"), 2)
    assets = round(paid_total + receivable_total, 2)
    liabilities = unpaid_labor
    equity = round(assets - liabilities, 2)

    return {
        "ok": True,
        "domain": clean_domain,
        "period": {"start": period_start, "end": period_end},
        "resources": resources,
        "invoices": invoices,
        "crm_customers": [
            {key: customer.get(key, "") for key in ["id", "name", "email", "address", "contact", "owner", "domain"]}
            for customer in crm_customers
        ],
        "expenses": expenses,
        "time_rows": time_rows,
        "onboarding_people": onboarding_people,
        "pnl": {
            "revenue": invoice_total,
            "billable_value_from_time": round(billable_value, 2),
            "labor_cost": round(labor_cost, 2),
            "expenses": expense_total,
            "gross_profit": gross_profit,
            "net_income": net_income,
        },
        "income_statement": {
            "service_revenue": invoice_total,
            "cost_of_services": round(labor_cost, 2),
            "gross_profit": gross_profit,
            "operating_expenses": expense_total,
            "operating_income": net_income,
            "net_income": net_income,
        },
        "balance_sheet": {
            "cash": paid_total,
            "accounts_receivable": receivable_total,
            "assets": assets,
            "unpaid_labor": unpaid_labor,
            "liabilities": liabilities,
            "equity": equity,
        },
        "counts": {
            "resources": len(resources),
            "invoices": len(invoices),
            "time_rows": len(time_rows),
            "onboarding_people": len(onboarding_people),
            "crm_customers": len(crm_customers),
        },
    }


@app.get("/api/accounting/summary")
def get_accounting_summary(domain: str = "dev", period_start: str = "", period_end: str = ""):
    return _accounting_summary_for_domain(domain, period_start, period_end)


@app.post("/api/accounting/resource")
def save_accounting_resource(
    resource_id: str = Form(default=""),
    domain: str = Form(default="dev"),
    crm_customer_id: str = Form(default=""),
    profile_id: str = Form(default=""),
    token: str = Form(default=""),
    name: str = Form(default=""),
    email: str = Form(default=""),
    role: str = Form(default=""),
    client: str = Form(default=""),
    bill_rate: str = Form(default="0"),
    cost_rate: str = Form(default="0"),
    start_date: str = Form(default=""),
    status: str = Form(default="active"),
    notes: str = Form(default=""),
):
    store = _accounting_store()
    now = _now_utc()
    resource_id = resource_id or _safe_token("RES")
    crm_customer = _crm_customer_for_value(domain, crm_customer_id or client)
    if not crm_customer:
        raise HTTPException(status_code=400, detail="Select a customer from Atlas before saving resource financials.")
    resource = {
        "id": resource_id,
        "domain": _domain_key(domain),
        "crm_customer_id": crm_customer["id"],
        "profile_id": _safe_action_text(profile_id, 80),
        "token": _safe_action_text(token, 120),
        "name": _safe_action_text(name, 240),
        "email": _safe_action_text(email, 240),
        "role": _safe_action_text(role, 240),
        "client": crm_customer["name"],
        "client_email": crm_customer.get("email", ""),
        "bill_rate": _money_float(bill_rate),
        "cost_rate": _money_float(cost_rate),
        "start_date": _safe_action_text(start_date, 40),
        "status": _safe_action_text(status, 40) or "active",
        "notes": _safe_action_text(notes, 1200),
        "created_at": now,
        "updated_at": now,
    }
    existing = False
    for index, item in enumerate(store["resources"]):
        if item.get("id") == resource_id:
            resource["created_at"] = item.get("created_at") or now
            store["resources"][index] = resource
            existing = True
            break
    if not existing:
        store["resources"].insert(0, resource)
    _write_json_store(ACCOUNTING_RECORDS_PATH, store)
    _upsert_accounting_resource_db(resource)
    return {"ok": True, "resource": resource, "summary": _accounting_summary_for_domain(domain)}


@app.post("/api/accounting/invoice")
def save_accounting_invoice(
    invoice_id: str = Form(default=""),
    domain: str = Form(default="dev"),
    crm_customer_id: str = Form(default=""),
    client: str = Form(default=""),
    client_email: str = Form(default=""),
    client_address: str = Form(default=""),
    invoice_number: str = Form(default=""),
    invoice_date: str = Form(default=""),
    due_date: str = Form(default=""),
    status: str = Form(default="draft"),
    payment_terms: str = Form(default="Net 15"),
    po_number: str = Form(default=""),
    period_start: str = Form(default=""),
    period_end: str = Form(default=""),
    time_entry_ids_json: str = Form(default="[]"),
    line_items_json: str = Form(default="[]"),
    notes: str = Form(default=""),
):
    store = _accounting_store()
    now = _now_utc()
    invoice_id = invoice_id or _safe_token("INV")
    crm_customer = _crm_customer_for_value(domain, crm_customer_id or client)
    if not crm_customer:
        raise HTTPException(status_code=400, detail="Select a customer from Atlas before saving an invoice.")
    try:
        line_items = json.loads(line_items_json or "[]")
    except Exception:
        raise HTTPException(status_code=400, detail="line_items_json must be valid JSON.")
    if not isinstance(line_items, list):
        raise HTTPException(status_code=400, detail="line_items_json must be a list.")
    try:
        time_entry_ids = json.loads(time_entry_ids_json or "[]")
    except Exception:
        time_entry_ids = []
    if not isinstance(time_entry_ids, list):
        time_entry_ids = []
    clean_items = []
    subtotal = 0.0
    for item in line_items:
        if not isinstance(item, dict):
            continue
        qty = _money_float(item.get("qty"), 0)
        rate = _money_float(item.get("rate"), 0)
        amount = _money_float(item.get("amount"), round(qty * rate, 2))
        if not item.get("description") and amount <= 0:
            continue
        clean = {
            "description": _safe_action_text(item.get("description"), 500),
            "qty": qty,
            "rate": rate,
            "amount": amount,
            "time_entry_id": _safe_action_text(item.get("time_entry_id"), 120),
            "consultant": _safe_action_text(item.get("consultant"), 240),
            "work_date": _safe_action_text(item.get("work_date"), 40),
            "summary": _safe_action_text(item.get("summary"), 500),
        }
        subtotal += amount
        clean_items.append(clean)
    total = round(subtotal, 2)
    invoice = {
        "id": invoice_id,
        "domain": _domain_key(domain),
        "crm_customer_id": crm_customer["id"],
        "client": crm_customer["name"],
        "client_email": crm_customer.get("email") or _safe_action_text(client_email, 240),
        "client_address": crm_customer.get("address") or _safe_action_text(client_address, 1200),
        "invoice_number": _safe_action_text(invoice_number, 80) or invoice_id,
        "invoice_date": _safe_action_text(invoice_date, 40),
        "due_date": _safe_action_text(due_date, 40),
        "status": _safe_action_text(status, 40) or "draft",
        "payment_terms": _safe_action_text(payment_terms, 120),
        "po_number": _safe_action_text(po_number, 120),
        "period_start": _safe_action_text(period_start, 40),
        "period_end": _safe_action_text(period_end, 40),
        "time_entry_ids": [_safe_action_text(item, 120) for item in time_entry_ids],
        "line_items": clean_items,
        "subtotal": total,
        "tax": 0,
        "total": total,
        "notes": _safe_action_text(notes, 1200),
        "created_at": now,
        "updated_at": now,
    }
    existing = False
    for index, item in enumerate(store["invoices"]):
        if item.get("id") == invoice_id:
            invoice["created_at"] = item.get("created_at") or now
            store["invoices"][index] = invoice
            existing = True
            break
    if not existing:
        store["invoices"].insert(0, invoice)
    _write_json_store(ACCOUNTING_RECORDS_PATH, store)
    _upsert_accounting_invoice_db(invoice)
    return {"ok": True, "invoice": invoice, "summary": _accounting_summary_for_domain(domain)}


@app.post("/api/accounting/invoice/{invoice_id}/status")
def update_accounting_invoice_status(invoice_id: str, status: str = Form(default="sent")):
    store = _accounting_store()
    allowed = {"draft", "sent", "viewed", "due", "paid", "overdue", "void"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported invoice status.")
    updated = None
    for invoice in store["invoices"]:
        if invoice.get("id") == invoice_id:
            invoice["status"] = status
            invoice["updated_at"] = _now_utc()
            if status == "sent":
                invoice["sent_at"] = invoice.get("sent_at") or invoice["updated_at"]
            if status == "viewed":
                invoice["viewed_at"] = invoice.get("viewed_at") or invoice["updated_at"]
            if status == "paid":
                invoice["paid_at"] = invoice.get("paid_at") or invoice["updated_at"]
            updated = invoice
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    _write_json_store(ACCOUNTING_RECORDS_PATH, store)
    _upsert_accounting_invoice_db(updated)
    return {"ok": True, "invoice": updated, "summary": _accounting_summary_for_domain(updated.get("domain", "dev"))}


def _invoice_workbench(domain: str = "dev", client: str = "", period_start: str = "", period_end: str = "") -> dict:
    clean_domain = _domain_key(domain)
    store = _accounting_store()
    resources = _accounting_domain_rows(store.get("resources", []), clean_domain)
    resource_keyed = _resource_lookup(resources)
    time_entries = _accounting_domain_rows(_read_json_store_with_demo(TIME_ENTRIES_PATH, []), clean_domain)
    approved_statuses = {"approved_for_payment", "processed_for_payment", "approved"}
    rows = []
    crm_customers = _crm_customer_rows(clean_domain)
    crm_by_name = {customer["name"].lower(): customer for customer in crm_customers}
    crm_by_id = {customer["id"].lower(): customer for customer in crm_customers}
    selected_crm = _crm_customer_for_value(clean_domain, client)
    selected_client_name = selected_crm.get("name", "")
    clients = {customer["name"]: 0.0 for customer in crm_customers}
    consultants_by_client = {}
    clean_client = selected_client_name.lower()

    def crm_for_row(value: str = "", crm_id: str = "") -> dict:
        clean_id = _safe_action_text(crm_id, 120).lower()
        clean_value = _safe_action_text(value, 240).lower()
        return crm_by_id.get(clean_id) or crm_by_name.get(clean_value) or {}

    for resource in resources:
        crm_customer = crm_for_row(resource.get("client"), resource.get("crm_customer_id"))
        if not crm_customer:
            continue
        resource_client = crm_customer["name"]
        clients.setdefault(resource_client, 0.0)
        consultants_by_client.setdefault(resource_client, {})
        consultant_key = (
            _safe_action_text(resource.get("email"), 240).lower()
            or _safe_action_text(resource.get("profile_id"), 80)
            or _safe_action_text(resource.get("name"), 240).lower()
        )
        consultants_by_client[resource_client][consultant_key] = {
            "name": _safe_action_text(resource.get("name"), 240),
            "email": _safe_action_text(resource.get("email"), 240),
            "role": _safe_action_text(resource.get("role"), 240),
            "profile_id": _safe_action_text(resource.get("profile_id"), 80),
            "bill_rate": _money_float(resource.get("bill_rate")),
            "cost_rate": _money_float(resource.get("cost_rate")),
            "status": _safe_action_text(resource.get("status"), 80) or "active",
        }
    for entry in time_entries:
        if (entry.get("status") or "") not in approved_statuses:
            continue
        if not _date_in_period(entry.get("work_date") or entry.get("week_start") or entry.get("created_at"), period_start, period_end):
            continue
        row = _time_row_from_entry(entry, _resource_for_time_entry(entry, resource_keyed))
        crm_customer = crm_for_row(row.get("client"), row.get("crm_customer_id") or row.get("client_id"))
        if not crm_customer:
            continue
        row_client = crm_customer["name"]
        row["client"] = row_client
        row["crm_customer_id"] = crm_customer["id"]
        clients[row_client] = clients.get(row_client, 0) + row["billable_value"]
        consultants_by_client.setdefault(row_client, {})
        consultant_key = (row.get("email") or row.get("candidate_name") or "").lower()
        existing_consultant = consultants_by_client[row_client].get(consultant_key, {})
        consultants_by_client[row_client][consultant_key] = {
            "name": row["candidate_name"],
            "email": row["email"],
            "role": existing_consultant.get("role", ""),
            "profile_id": row.get("profile_id") or existing_consultant.get("profile_id", ""),
            "bill_rate": row["bill_rate"],
            "cost_rate": row["cost_rate"],
            "status": existing_consultant.get("status", "active"),
        }
        if clean_client and row_client.lower() != clean_client:
            continue
        rows.append(row)
    invoices = [
        invoice for invoice in _accounting_domain_rows(store.get("invoices", []), clean_domain)
        if invoice.get("client") or invoice.get("line_items")
    ]
    for invoice in invoices:
        crm_customer = crm_for_row(invoice.get("client"), invoice.get("crm_customer_id"))
        if crm_customer:
            clients.setdefault(crm_customer["name"], 0.0)
    customers = []
    for crm_customer in crm_customers:
        name = crm_customer["name"]
        value = clients.get(name, 0.0)
        client_invoices = [
            invoice for invoice in invoices
            if (crm_for_row(invoice.get("client"), invoice.get("crm_customer_id")) or {}).get("name", "").lower() == name.lower()
        ]
        po_numbers = []
        payment_terms = []
        for invoice in client_invoices:
            po = _safe_action_text(invoice.get("po_number"), 120)
            terms = _safe_action_text(invoice.get("payment_terms"), 120)
            if po and po not in po_numbers:
                po_numbers.append(po)
            if terms and terms not in payment_terms:
                payment_terms.append(terms)
        customers.append(
            {
                "id": crm_customer["id"],
                "name": name,
                "approved_billable": round(value, 2),
                "email": crm_customer.get("email") or (client_invoices[0].get("client_email") if client_invoices else "") or "",
                "address": crm_customer.get("address") or (client_invoices[0].get("client_address") if client_invoices else "") or "",
                "contact": crm_customer.get("contact") or "",
                "owner": crm_customer.get("owner") or "",
                "po_numbers": po_numbers,
                "payment_terms": payment_terms,
                "consultants": list(consultants_by_client.get(name, {}).values()),
            }
        )
    invoice_rows = []
    for invoice in invoices:
        crm_customer = crm_for_row(invoice.get("client"), invoice.get("crm_customer_id"))
        if not crm_customer:
            continue
        copied = dict(invoice)
        copied["crm_customer_id"] = crm_customer["id"]
        copied["client"] = crm_customer["name"]
        copied["client_email"] = crm_customer.get("email") or copied.get("client_email") or ""
        copied["client_address"] = crm_customer.get("address") or copied.get("client_address") or ""
        consultants = set()
        for item in copied.get("line_items", []) or []:
            if isinstance(item, dict):
                consultant = _safe_action_text(item.get("consultant"), 240)
                if not consultant and item.get("description"):
                    parts = str(item.get("description")).split(" - ")
                    if len(parts) >= 2:
                        consultant = _safe_action_text(parts[1], 240)
                if consultant:
                    consultants.add(consultant)
        copied["consultants"] = sorted(consultants)
        copied["line_count"] = len(copied.get("line_items", []) or [])
        invoice_rows.append(copied)
    line_items = [
        {
            "description": f"{row.get('work_date') or row.get('week_start')} - {row.get('candidate_name')} - {row.get('project') or 'Consulting services'}",
            "qty": row["hours"],
            "rate": row["bill_rate"],
            "amount": row["billable_value"],
            "time_entry_id": row["id"],
            "consultant": row["candidate_name"],
            "work_date": row.get("work_date") or row.get("week_start"),
            "summary": row.get("summary") or "",
        }
        for row in rows
        if clean_client and row.get("bill_rate")
    ]
    return {
        "ok": True,
        "domain": clean_domain,
        "client": selected_client_name,
        "crm_customer_id": selected_crm.get("id", ""),
        "period": {"start": period_start, "end": period_end},
        "customers": customers,
        "consultants": list(consultants_by_client.get(selected_client_name, {}).values()) if clean_client else [],
        "time_rows": rows,
        "line_items": line_items,
        "invoices": sorted(invoice_rows, key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True),
    }


@app.get("/api/invoices/workbench")
def get_invoice_workbench(domain: str = "dev", client: str = "", period_start: str = "", period_end: str = ""):
    return _invoice_workbench(domain, client, period_start, period_end)


@app.post("/api/invoices/from-time")
def save_invoice_from_time(
    invoice_id: str = Form(default=""),
    domain: str = Form(default="dev"),
    crm_customer_id: str = Form(default=""),
    client: str = Form(default=""),
    client_email: str = Form(default=""),
    client_address: str = Form(default=""),
    invoice_number: str = Form(default=""),
    invoice_date: str = Form(default=""),
    due_date: str = Form(default=""),
    status: str = Form(default="draft"),
    payment_terms: str = Form(default="Net 15"),
    po_number: str = Form(default=""),
    period_start: str = Form(default=""),
    period_end: str = Form(default=""),
    time_entry_ids_json: str = Form(default="[]"),
    notes: str = Form(default=""),
):
    crm_customer = _crm_customer_for_value(domain, crm_customer_id or client)
    if not crm_customer:
        raise HTTPException(status_code=400, detail="Select a customer from Atlas before saving an invoice.")
    workbench = _invoice_workbench(domain, crm_customer["id"], period_start, period_end)
    line_items = workbench.get("line_items", [])
    try:
        selected_time_ids = json.loads(time_entry_ids_json or "[]")
    except Exception:
        selected_time_ids = []
    if isinstance(selected_time_ids, list) and selected_time_ids:
        selected_set = {str(item) for item in selected_time_ids if item}
        line_items = [item for item in line_items if str(item.get("time_entry_id") or "") in selected_set]
    if not line_items:
        raise HTTPException(status_code=400, detail="No approved billable time with consultant bill rates was found for this customer and period.")
    return save_accounting_invoice(
        invoice_id=invoice_id,
        domain=domain,
        crm_customer_id=crm_customer["id"],
        client=crm_customer["name"],
        client_email=crm_customer.get("email") or client_email,
        client_address=crm_customer.get("address") or client_address,
        invoice_number=invoice_number or f"DR-{datetime.utcnow().strftime('%Y%m%d%H%M')}",
        invoice_date=invoice_date,
        due_date=due_date,
        status=status,
        payment_terms=payment_terms,
        po_number=po_number,
        period_start=period_start,
        period_end=period_end,
        time_entry_ids_json=json.dumps([item.get("time_entry_id") for item in line_items if item.get("time_entry_id")]),
        line_items_json=json.dumps(line_items),
        notes=notes,
    )


@app.post("/api/time-entry/{entry_id}/status")
def update_time_entry_status(
    entry_id: str,
    status: str = Form(default="processed_for_payment"),
    processed_by: str = Form(default=""),
    processed_reference: str = Form(default=""),
    processed_note: str = Form(default=""),
):
    entries = _read_json_store(TIME_ENTRIES_PATH, [])
    allowed = {"submitted_to_devready", "approved_for_payment", "processed_for_payment", "needs_review"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported time entry status.")
    updated = None
    now = _now_utc()
    for entry in entries:
        if entry.get("id") == entry_id:
            entry["status"] = status
            entry["updated_at"] = now
            if status == "processed_for_payment":
                entry["processed_at"] = now
                entry["processed_by"] = processed_by.strip() or entry.get("processed_by") or "HR"
                entry["processed_reference"] = processed_reference.strip()
                entry["processed_note"] = processed_note.strip()
            elif status in {"submitted_to_devready", "approved_for_payment", "needs_review"}:
                entry.pop("processed_at", None)
                entry.pop("processed_by", None)
                entry.pop("processed_reference", None)
                entry.pop("processed_note", None)
            updated = entry
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Time entry not found.")
    _write_json_store(TIME_ENTRIES_PATH, entries)
    return {"ok": True, "entry": updated}


@app.get("/api/time-entry/{token}")
def get_time_entries(token: str):
    entries = _read_json_store_with_demo(TIME_ENTRIES_PATH, [])
    onboarding = _read_json_store_with_demo(ONBOARDING_RECORDS_PATH, {}).get(token, {}) if token else {}
    resource = _resource_for_time_person({
        "profile_id": onboarding.get("profile_id", ""),
        "token": token,
        "email": onboarding.get("email", ""),
        "candidate_name": onboarding.get("candidate_name") or onboarding.get("legal_name", ""),
        "domain": onboarding.get("domain", "dev"),
    })
    record = dict(onboarding)
    if resource:
        record.setdefault("client", resource.get("client", ""))
        record.setdefault("project", resource.get("project", "") or resource.get("role", ""))
        record.setdefault("bill_rate", resource.get("bill_rate", ""))
        record.setdefault("cost_rate", resource.get("cost_rate", ""))
    return {
        "token": token,
        "record": record,
        "resource": resource,
        "entries": [entry for entry in entries if entry.get("token") == token],
    }


from azureUtils.routes import azureEndpoints, aiChatEndpoints, azureJobEndpoints
from openAI.routes import aiEndpoints
from calendar_router import router as calendar_router

app.include_router(azureEndpoints.router)
app.include_router(aiChatEndpoints.router)
app.include_router(azureJobEndpoints.router)
app.include_router(aiEndpoints.router)
app.include_router(calendar_router)

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse('<meta http-equiv="refresh" content="0; url=/ui/index.html">')

@app.get("/{page_name}.html")
def legacy_page_redirect(page_name: str):
    return RedirectResponse(f"/ui/pages/{page_name}.html")
