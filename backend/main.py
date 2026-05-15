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


from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import os, shutil, traceback, json, base64, hashlib, hmac
from typing import Optional
from datetime import datetime, timedelta
from openAI import pageAgents

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
from jd_match import normalize_jd, match, azureMatch
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
}
DB_PATH = DOMAIN_DB_PATHS["dev"]
UPLOAD_DIR = "uploads"
EXPORT_DIR = "exports"
DATA_DIR = "data"
PROFILE_BADGES_PATH = os.path.join(DATA_DIR, "profile_badges.json")
ONBOARDING_RECORDS_PATH = os.path.join(DATA_DIR, "onboarding_records.json")
TIME_ENTRIES_PATH = os.path.join(DATA_DIR, "time_entries.json")
WORKFLOW_EVENTS_PATH = os.path.join(DATA_DIR, "workflow_events.json")
INTERVIEW_ARCHIVE_PATH = os.path.join(DATA_DIR, "interview_archive.json")
CRM_RECORDS_PATH = os.path.join(DATA_DIR, "crm_records.json")
MEETING_RECORDS_PATH = os.path.join(DATA_DIR, "meeting_records.json")
ACCESS_USERS_PATH = os.path.join(DATA_DIR, "access_users.json")
ACCESS_CANDIDATES_PATH = os.path.join(DATA_DIR, "access_candidates.json")
ADMIN_SESSION_TOKENS = {}

MENU_ITEMS = [
    {"key": "talent", "label": "Talent", "href": "find-candidate.html"},
    {"key": "find_in", "label": "Find Candidates (In)", "href": "match-role.html"},
    {"key": "find_out", "label": "Find Candidates (Out)", "href": "mine-candidate-external.html"},
    {"key": "profiles", "label": "Profiles", "href": "profile-preview.html"},
    {"key": "job_descriptions", "label": "Job Descriptions", "href": "job-descriptions.html"},
    {"key": "crm", "label": "CRM", "href": "crm.html"},
    {"key": "meet", "label": "Meet", "href": "meet.html"},
    {"key": "interviews", "label": "Interviews", "href": "schedule-interview.html?interview=ready"},
    {"key": "time_link", "label": "Time Link", "href": "time-admin.html"},
    {"key": "test_challenge", "label": "Test Challenge", "href": "test-challenge.html"},
    {"key": "ai_cert", "label": "Get AI Certified", "href": "ai-cert.html"},
    {"key": "badges", "label": "View Badges", "href": "badge-catalog.html"},
    {"key": "agents", "label": "Agents", "href": "agents.html"},
    {"key": "meridian", "label": "Meridian", "href": "https://meridian-mvp-production.up.railway.app/"},
    {"key": "admin", "label": "Admin", "href": "admin.html"},
]
DEFAULT_INTERNAL_MENU = [
    "talent",
    "find_in",
    "find_out",
    "profiles",
    "job_descriptions",
    "crm",
    "meet",
    "interviews",
    "time_link",
    "test_challenge",
    "ai_cert",
    "badges",
    "agents",
    "meridian",
]
DEFAULT_CANDIDATE_MENU = ["test_challenge", "ai_cert", "badges"]
SUPER_MENU = [item["key"] for item in MENU_ITEMS]


def _domain_key(domain: str = "dev") -> str:
    value = (domain or "dev").strip().lower()
    if value in {"all", "*"}:
        return "all"
    if value in {"technology", "tech", "devready", "dev"}:
        return "dev"
    if value in {"engineer", "engineering", "build", "buildready"}:
        return "engineer"
    if value in {"law", "legal", "legalready"}:
        return "law"
    return "dev"


def _storage_domain(domain: str = "dev") -> str:
    key = _domain_key(domain)
    if key == "dev":
        return "technology"
    if key in {"engineer", "law"}:
        return key
    return ""


def _domain_db_path(domain: str = "dev") -> str:
    return DOMAIN_DB_PATHS.get(_domain_key(domain), DOMAIN_DB_PATHS["dev"])


def _domain_db_items(domain: str = "dev"):
    key = _domain_key(domain)
    if key == "all":
        return list(DOMAIN_DB_PATHS.items())
    return [(key, DOMAIN_DB_PATHS.get(key, DOMAIN_DB_PATHS["dev"]))]


def _profile_db_path(profile_id: str, domain: str = "") -> str:
    for _, db_path in _domain_db_items(domain or "all"):
        if storage.get_profile(db_path, profile_id):
            return db_path
    return _domain_db_path(domain or "dev")


def _jd_db_path(jd_id: str, domain: str = "") -> str:
    for _, db_path in _domain_db_items(domain or "all"):
        if storage.get_jd(db_path, jd_id):
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


def _now_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_token(prefix: str = "ONB") -> str:
    return new_id(prefix).replace(" ", "").replace("/", "-")


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
    if role == "super_user" or _normalize_user_key(email).endswith("@devready.io"):
        return SUPER_MENU
    return DEFAULT_CANDIDATE_MENU if role == "candidate" else DEFAULT_INTERNAL_MENU


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
        "created_at": user.get("created_at", ""),
        "updated_at": user.get("updated_at", ""),
    }


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
    try:
        if os.path.exists(PROFILE_BADGES_PATH):
            with open(PROFILE_BADGES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        traceback.print_exc()
    return {}


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

app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


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
def access_menu():
    return {
        "items": MENU_ITEMS,
        "default_internal_menu": DEFAULT_INTERNAL_MENU,
        "default_candidate_menu": DEFAULT_CANDIDATE_MENU,
        "super_menu": SUPER_MENU,
    }


@app.post("/api/agents/ask")
def ask_agent(
    agent_key: str = Form(default="talent"),
    message: str = Form(default=""),
    context_json: str = Form(default="{}"),
    domain: str = Form(default="dev"),
    admin_token: str = Form(default=""),
    numa_change_mode: str = Form(default="off"),
):
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


@app.post("/api/access/login")
def access_login(
    username: str = Form(default=""),
    email: str = Form(default=""),
    password: str = Form(default=""),
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
    user["last_login_at"] = now
    user["updated_at"] = now
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(user), "menu_items": MENU_ITEMS}


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
    role = "candidate" if login_type == "candidate" else "internal"
    user_id = _safe_token("USR")
    users[user_id] = {
        "id": user_id,
        "username": username or email,
        "display_name": display_name or username or email,
        "email": email,
        "role": role,
        "status": "active",
        "allowed_menu": _default_menu_for_user(role, email),
        "password_hash": _password_hash(password),
        "created_at": now,
        "updated_at": now,
        "last_login_at": now,
    }
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(users[user_id]), "menu_items": MENU_ITEMS}


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
    role = role if role in {"super_user", "internal", "candidate"} else "internal"
    status = status if status in {"active", "blocked"} else "active"
    try:
        allowed_menu = json.loads(allowed_menu_json or "[]")
    except Exception:
        allowed_menu = []
    allowed_keys = {item["key"] for item in MENU_ITEMS}
    allowed_menu = [key for key in allowed_menu if key in allowed_keys]
    if role == "super_user":
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
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return p


@app.get("/api/profiles/{profile_id}/html", response_class=HTMLResponse)
def get_profile_html(profile_id: str, domain: str = ""):
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(profile_to_html(p))


@app.get("/api/profiles/{profile_id}/docx")
def get_profile_docx(profile_id: str, domain: str = ""):
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
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

@app.get("/api/profile/{profile_id}")
def profile_get(profile_id: str, domain: str = ""):
    p = storage.get_profile(_profile_db_path(profile_id, domain), profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return p


@app.get("/api/profile/{profile_id}/html", response_class=HTMLResponse)
def profile_html(profile_id: str):
    # Reuse the canonical /api/profiles/{id}/html implementation
    return get_profile_html(profile_id)


@app.get("/api/profile/{profile_id}/docx")
def profile_docx(profile_id: str):
    # Reuse the canonical /api/profiles/{id}/docx implementation
    return get_profile_docx(profile_id)


@app.get("/api/jd/list")
def jd_list(domain: str = "technology"):
    if domain in ("all","*","",None):
        rows = []
        for key, db_path in DOMAIN_DB_PATHS.items():
            rows.extend(storage.list_jds(db_path, domain=_storage_domain(key)))
        return rows
    return storage.list_jds(_domain_db_path(domain), domain=_storage_domain(domain))


@app.get("/api/jd/{jd_id}")
def jd_get(jd_id: str, domain: str = ""):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return jd


@app.get("/api/jd/{jd_id}/html", response_class=HTMLResponse)
def jd_html(jd_id: str, domain: str = ""):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return HTMLResponse(jd_to_html(jd))


@app.get("/api/jd/{jd_id}/docx")
def jd_docx(jd_id: str, domain: str = ""):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    out = os.path.join(EXPORT_DIR, f"{jd_id}.docx")
    jd_to_docx(jd, out)
    filename = f"Job_Description_{jd.get('company','Company').replace(' ','_')}_{jd.get('title','Role').replace(' ','_')}.docx"
    return FileResponse(out, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=filename)


@app.get("/api/jd/latest")
def jd_latest(domain: str = "technology", jd_id: Optional[str] = None):
    jd = storage.get_jd(_jd_db_path(jd_id, domain), jd_id) if jd_id else storage.get_latest_jd(_domain_db_path(domain), domain=_storage_domain(domain))
    if not jd:
        return {"jd_id": "", "company":"", "title": "", "domain": _storage_domain(domain), "created_at": "", "jd_text": "", "jd_skills": {}}
    return jd

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
            "recommended_personal_email": row["recommended_personal_email"],
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
    archive = _read_json_store(INTERVIEW_ARCHIVE_PATH, [])
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
def list_crm_records(domain: str = "dev", limit: int = 200):
    records = _read_json_store(CRM_RECORDS_PATH, [])
    wanted_domain = _domain_key(domain)
    if not isinstance(records, list):
        records = []
    if wanted_domain != "all":
        records = [item for item in records if _domain_key(item.get("domain", "dev")) == wanted_domain]
    records = sorted(records, key=lambda item: item.get("updatedAt") or item.get("createdAt") or "", reverse=True)
    return {"ok": True, "records": records[: max(1, min(limit, 500))]}


@app.get("/api/meetings/archive")
def list_meeting_records(domain: str = "dev", profile_id: str = "", limit: int = 200):
    records = _read_json_store(MEETING_RECORDS_PATH, [])
    wanted_domain = _domain_key(domain)
    if not isinstance(records, list):
        records = []
    if wanted_domain != "all":
        records = [item for item in records if _domain_key(item.get("domain", "dev")) == wanted_domain]
    if profile_id:
        records = [item for item in records if str(item.get("profileId") or item.get("candidateId") or "") == str(profile_id)]
    records = sorted(records, key=lambda item: item.get("updatedAt") or item.get("meetingAt") or item.get("createdAt") or "", reverse=True)
    return {"ok": True, "records": records[: max(1, min(limit, 500))]}


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
    records = _read_json_store(ONBOARDING_RECORDS_PATH, {})
    now = _now_utc()
    token = ""
    for existing_token, record in records.items():
        if profile_id and record.get("profile_id") == profile_id:
            token = existing_token
            break
        if email and (record.get("email") or "").lower() == email.lower():
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
        "candidate_name": candidate_name or record.get("candidate_name", ""),
        "email": email or record.get("email", ""),
        "title": title or record.get("title", ""),
        "domain": domain or record.get("domain", "dev"),
        "start_day": start_day or record.get("start_day", ""),
        "status": "hire_started",
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
        "candidate_name": candidate_name,
        "email": email,
        "domain": domain or "dev",
        "event_type": "hire_onboarding_started",
        "status": "hire_started",
        "notes": "Onboarding link created.",
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
    records = _read_json_store(ONBOARDING_RECORDS_PATH, {})
    people = []
    for token, record in records.items():
        record_domain = record.get("domain", "dev")
        if domain != "all" and record_domain != domain:
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


@app.get("/api/onboarding/{token}")
def get_onboarding(token: str):
    records = _read_json_store(ONBOARDING_RECORDS_PATH, {})
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
    onboarding = _read_json_store(ONBOARDING_RECORDS_PATH, {}).get(token, {}) if token else {}
    now = _now_utc()
    person_profile_id = profile_id or onboarding.get("profile_id", "")
    person_name = candidate_name or onboarding.get("candidate_name", "") or onboarding.get("legal_name", "")
    person_email = email or onboarding.get("email", "")
    entry_domain = domain or onboarding.get("domain", "dev")
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
                "client": client,
                "project": project,
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
            "client": client,
            "project": project,
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
    entries = _read_json_store(TIME_ENTRIES_PATH, [])
    domain_entries = [
        entry for entry in entries
        if domain == "all" or entry.get("domain", "dev") == domain
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
    entries = _read_json_store(TIME_ENTRIES_PATH, [])
    onboarding = _read_json_store(ONBOARDING_RECORDS_PATH, {}).get(token, {}) if token else {}
    return {
        "token": token,
        "record": onboarding,
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
