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


from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import os, shutil, traceback, json
from typing import Optional
from datetime import datetime

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
DB_PATH = "devready.db"
UPLOAD_DIR = "uploads"
EXPORT_DIR = "exports"
DATA_DIR = "data"
PROFILE_BADGES_PATH = os.path.join(DATA_DIR, "profile_badges.json")
ONBOARDING_RECORDS_PATH = os.path.join(DATA_DIR, "onboarding_records.json")
TIME_ENTRIES_PATH = os.path.join(DATA_DIR, "time_entries.json")
WORKFLOW_EVENTS_PATH = os.path.join(DATA_DIR, "workflow_events.json")
ACCESS_USERS_PATH = os.path.join(DATA_DIR, "access_users.json")
ACCESS_CANDIDATES_PATH = os.path.join(DATA_DIR, "access_candidates.json")

MENU_ITEMS = [
    {"key": "talent", "label": "Talent", "href": "find-candidate.html"},
    {"key": "find_in", "label": "Find Candidates (In)", "href": "match-role.html"},
    {"key": "find_out", "label": "Find Candidates (Out)", "href": "mine-candidate-external.html"},
    {"key": "profiles", "label": "Profiles", "href": "profile-preview.html"},
    {"key": "job_descriptions", "label": "Job Descriptions", "href": "job-descriptions.html"},
    {"key": "crm", "label": "CRM", "href": "crm.html"},
    {"key": "meet", "label": "Meet", "href": "meet.html"},
    {"key": "test_challenge", "label": "Test Challenge", "href": "test-challenge.html"},
    {"key": "ai_cert", "label": "Get AI Certified", "href": "ai-cert.html"},
    {"key": "badges", "label": "View Badges", "href": "badge-catalog.html"},
    {"key": "meridian", "label": "Meridian", "href": "https://meridian-mvp-production.up.railway.app/"},
    {"key": "admin", "label": "Admin", "href": "admin.html"},
    {"key": "legacy", "label": "Legacy Dashboard", "href": "https://dev.readyplatform.io/dashboards/vetter"},
]
DEFAULT_INTERNAL_MENU = [
    "talent",
    "find_in",
    "find_out",
    "profiles",
    "job_descriptions",
    "crm",
    "meet",
    "test_challenge",
    "ai_cert",
    "badges",
    "meridian",
]
DEFAULT_CANDIDATE_MENU = ["test_challenge", "ai_cert", "badges"]
SUPER_MENU = [item["key"] for item in MENU_ITEMS]


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
    os.replace(tmp_path, path)


def _now_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_token(prefix: str = "ONB") -> str:
    return new_id(prefix).replace(" ", "").replace("/", "-")


def _normalize_user_key(value: str) -> str:
    return (value or "").strip().lower()


def _seed_access_users() -> dict:
    users = _read_json_store(ACCESS_USERS_PATH, {})
    if users:
        return users
    now = _now_utc()
    email = os.getenv("DEVREADY_ADMIN_EMAIL", "Darrin.Joncas@gmail.com")
    username = os.getenv("DEVREADY_ADMIN_USERNAME", "DJ")
    user_id = _safe_token("USR")
    users[user_id] = {
        "id": user_id,
        "username": username,
        "display_name": "Darrin Joncas",
        "email": email,
        "role": "super_user",
        "status": "active",
        "allowed_menu": SUPER_MENU,
        "created_at": now,
        "updated_at": now,
    }
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
) -> tuple[str, bool]:
    profile_id = (profile_id or "").strip()
    candidate_name = (candidate_name or "").strip()
    email = (email or "").strip()
    title = (title or "").strip() or _normalize_cert_title(level, certificate_id)

    if not profile_id:
        try:
            existing_candidates = candidates.searchCandidatesByNameEmail(email, limit=1, domain="all") if email else []
            for row in existing_candidates:
                if (row.get("email") or "").strip().lower() == email.lower():
                    return str(row.get("id")), False
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
                domain=os.getenv("DEVREADY_CANDIDATE_DOMAIN", "dev"),
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

    profile = storage.get_profile(DB_PATH, profile_id) if profile_id else None
    if not profile and email:
        profile = storage.get_profile_by_email(DB_PATH, email)
        profile_id = (profile.get("meta", {}) or {}).get("profile_id", "") if profile else profile_id

    created = False
    now = datetime.utcnow().isoformat() + "Z"
    if not profile:
        profile = empty_devready_profile()
        profile.setdefault("meta", {})["profile_id"] = profile_id or new_id("DRP")
        profile["meta"]["domain"] = "technology"
        profile["meta"]["source"] = "ai_certification"
        profile.setdefault("contact", {})["full_name"] = candidate_name or email or "AI Certified Candidate"
        profile["contact"]["email"] = email
        profile.setdefault("summary", {})["headline"] = title
        profile["summary"]["overview"] = (
            f"Profile auto-created from AI certification handoff. Certificate earned: {level or title}."
        )
        created = True
    else:
        profile.setdefault("meta", {})["domain"] = profile.get("meta", {}).get("domain") or "technology"
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
    storage.upsert_profile(DB_PATH, profile)
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
storage.init_db(DB_PATH)

app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")


@app.get("/api/debug/dbinfo")
def dbinfo():
    try:
        jds_all = storage.list_jds(DB_PATH, domain=None)
        jds_tech = storage.list_jds(DB_PATH, domain="technology")
        profs_all = storage.list_profiles(DB_PATH, domain=None)
        profs_tech = storage.list_profiles(DB_PATH, domain="technology")
        return {
            "db_path": DB_PATH,
            "job_descriptions_all": len(jds_all),
            "job_descriptions_technology": len(jds_tech),
            "profiles_all": len(profs_all),
            "profiles_technology": len(profs_tech),
            "jd_domains": sorted(list({(x.get("domain") or "") for x in jds_all})),
            "profile_domains": sorted(list({(x.get("domain") or "") for x in profs_all})),
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


@app.post("/api/access/login")
def access_login(
    username: str = Form(default=""),
    email: str = Form(default=""),
    login_type: str = Form(default="internal"),
):
    users = _seed_access_users()
    username = (username or "").strip()
    email = (email or "").strip()
    if not username and not email:
        raise HTTPException(status_code=400, detail="Enter a username or email.")

    user = _find_access_user(users, username=username, email=email)
    now = _now_utc()
    if user and user.get("status") == "blocked":
        raise HTTPException(status_code=403, detail="This user is blocked. Contact a DevReady admin.")
    if not user:
        user_id = _safe_token("USR")
        role = "candidate" if login_type == "candidate" else "internal"
        user = {
            "id": user_id,
            "username": username or email,
            "display_name": username or email,
            "email": email,
            "role": role,
            "status": "active",
            "allowed_menu": DEFAULT_CANDIDATE_MENU if role == "candidate" else DEFAULT_INTERNAL_MENU,
            "created_at": now,
            "updated_at": now,
        }
        users[user_id] = user
    user["last_login_at"] = now
    user["updated_at"] = now
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(user), "menu_items": MENU_ITEMS}


@app.get("/api/admin/users")
def admin_users():
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


@app.post("/api/admin/users")
def admin_save_user(
    user_id: str = Form(default=""),
    username: str = Form(default=""),
    display_name: str = Form(default=""),
    email: str = Form(default=""),
    role: str = Form(default="internal"),
    status: str = Form(default="active"),
    allowed_menu_json: str = Form(default="[]"),
):
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
    elif not allowed_menu:
        allowed_menu = DEFAULT_CANDIDATE_MENU if role == "candidate" else DEFAULT_INTERNAL_MENU

    user_id = user_id or _safe_token("USR")
    existing = users.get(user_id, {})
    users[user_id] = {
        "id": user_id,
        "username": username or existing.get("username", "") or email,
        "display_name": display_name or existing.get("display_name", "") or username or email,
        "email": email or existing.get("email", ""),
        "role": role,
        "status": status,
        "allowed_menu": allowed_menu,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "last_login_at": existing.get("last_login_at", ""),
    }
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(users[user_id])}


@app.post("/api/admin/users/{user_id}/block")
def admin_block_user(user_id: str, blocked: str = Form(default="true")):
    users = _seed_access_users()
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    users[user_id]["status"] = "blocked" if str(blocked).lower() in {"true", "1", "yes", "on"} else "active"
    users[user_id]["updated_at"] = _now_utc()
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "user": _public_user(users[user_id])}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: str):
    users = _seed_access_users()
    if user_id not in users:
        raise HTTPException(status_code=404, detail="User not found.")
    deleted = users.pop(user_id)
    _write_json_store(ACCESS_USERS_PATH, users)
    return {"ok": True, "deleted": _public_user(deleted)}


@app.post("/api/admin/users/{user_id}/send-login")
def admin_send_login_info(user_id: str):
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
    candidate_id: str = Form(default=""),
    candidate_email: str = Form(default=""),
    action: str = Form(default="block"),
    notes: str = Form(default=""),
):
    key = candidate_id or candidate_email
    if not key:
        raise HTTPException(status_code=400, detail="Enter a candidate id or email.")
    records = _read_json_store(ACCESS_CANDIDATES_PATH, {})
    now = _now_utc()
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
        profile.setdefault("meta", {})["domain"] = domain

        storage.upsert_profile(DB_PATH, profile)
        pid = profile.get("meta", {}).get("profile_id", "")

        return JSONResponse({"profile_id": pid, "profile": profile})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.get("/api/profiles")
def list_profiles(domain: str = "technology"):
    return storage.list_profiles(DB_PATH, domain=domain)

@app.post("/api/profiles/skillSearch")
def search_profiles(domain: str = Form("technology"), skills: str = Form("")):
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    return storage.list_profiles(DB_PATH, domain=domain, skills_filter=skill_list)


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str):
    p = storage.get_profile(DB_PATH, profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return p


@app.get("/api/profiles/{profile_id}/html", response_class=HTMLResponse)
def get_profile_html(profile_id: str):
    p = storage.get_profile(DB_PATH, profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(profile_to_html(p))


@app.get("/api/profiles/{profile_id}/docx")
def get_profile_docx(profile_id: str):
    p = storage.get_profile(DB_PATH, profile_id)
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
                profile.setdefault("meta", {})["domain"] = domain
                storage.upsert_profile(DB_PATH, profile)
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

        storage.upsert_jd(DB_PATH, jd_id, company, title, domain, created_at, jd_text, skills)
        return {"jd_id": jd_id, "company": company, "title": title, "domain": domain, "created_at": created_at, "jd_text": jd_text, "jd_skills": skills}
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
        storage.upsert_jd(DB_PATH, jd_id, company, title, domain, created_at, jd_text, skills)
        return {"jd_id": jd_id, "company": company, "title": title, "domain": domain, "created_at": created_at, "jd_skills": skills, "jd_text": jd_text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@app.get("/api/profile/list")
def profile_list(domain: str = "technology"):
    if domain in ("all","*","",None):
        return storage.list_profiles(DB_PATH, domain=None)
    return storage.list_profiles(DB_PATH, domain=domain)

@app.get("/api/profile/count")
def profile_count(domain: str = "technology"):
    if domain in ("all","*","",None):
        return storage.count_profiles(DB_PATH, domain=None)
    return storage.count_profiles(DB_PATH, domain=domain)

@app.get("/api/profile/count/recent")
def profile_count_recent(domain: str = "technology"):
    if domain in ("all","*","",None):
        return storage.count_profiles_recent(DB_PATH, domain=None)
    return storage.count_profiles_recent(DB_PATH, domain=domain)

# Used to search for profiles with the search bar
@app.post("/api/profile/search")
def profile_search(domain: str = Form(default="technology"), search_string: str = Form(default="")):
    if domain in ("all","*","",None):
        return storage.search_profiles(DB_PATH, domain=None, search_string=search_string, limit=5)
    return storage.search_profiles(DB_PATH, domain=domain, search_string=search_string, limit=5)

@app.post("/api/profile/pageCount")
def profile_page_count(domain: str = Form(default="technology"), search_string: str = Form(default=""), pageLimit: int = Form(default=10)):
    print(f"Calculating page count for domain='{domain}' with search_string='{search_string}'")
    if domain in ("all","*","",None):
        return storage.search_profiles_page_count(DB_PATH, domain=None, search_string=search_string, pageLimit=pageLimit)
    return storage.search_profiles_page_count(DB_PATH, domain=domain, search_string=search_string, pageLimit=pageLimit)

@app.post("/api/profile/pageSearch")
def profile_page_search(domain: str = Form(default="technology"), search_string: str = Form(default=""), currentPage: int = Form(default=0), pageLimit: int = Form(default=10)):
    print(f"Searching profiles for domain='{domain}' with search_string='{search_string}' on page {currentPage} with pageLimit {pageLimit}")

    currentPage = currentPage - 1  # adjust for 0-based indexing in backend

    if domain in ("all","*","",None):
        return storage.search_profiles_full(DB_PATH, domain=None, search_string=search_string, currentPage=currentPage, pageLimit=pageLimit)
    return storage.search_profiles_full(DB_PATH, domain=domain, search_string=search_string, currentPage=currentPage, pageLimit=pageLimit)

@app.get("/api/profile/{profile_id}")
def profile_get(profile_id: str):
    p = storage.get_profile(DB_PATH, profile_id)
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
    # NOTE: storage.list_jds() already falls back to ALL if domain-filter yields empty,
    # so your dropdown never goes blank just because domain changed.
    if domain in ("all","*","",None):
        return storage.list_jds(DB_PATH, domain=None)
    return storage.list_jds(DB_PATH, domain=domain)


@app.get("/api/jd/{jd_id}")
def jd_get(jd_id: str):
    jd = storage.get_jd(DB_PATH, jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return jd


@app.get("/api/jd/{jd_id}/html", response_class=HTMLResponse)
def jd_html(jd_id: str):
    jd = storage.get_jd(DB_PATH, jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    return HTMLResponse(jd_to_html(jd))


@app.get("/api/jd/{jd_id}/docx")
def jd_docx(jd_id: str):
    jd = storage.get_jd(DB_PATH, jd_id)
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    out = os.path.join(EXPORT_DIR, f"{jd_id}.docx")
    jd_to_docx(jd, out)
    filename = f"Job_Description_{jd.get('company','Company').replace(' ','_')}_{jd.get('title','Role').replace(' ','_')}.docx"
    return FileResponse(out, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=filename)


@app.get("/api/jd/latest")
def jd_latest(domain: str = "technology", jd_id: Optional[str] = None):
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd:
        return {"jd_id": "", "company":"", "title": "", "domain": domain, "created_at": "", "jd_text": "", "jd_skills": {}}
    return jd

from openAI import externalPeopleSearch
import peopleDataLabs.peopleSearch as peopleDataLabs
from azureUtils.storage import candidates

@app.post("/api/match/run")
def run_match(domain: str = Form("technology"), jd_id: str = Form(None), top_k: int = Form(10)):
    # TODO: Set up job descriptions in the database
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
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
        storage.upsert_jd(DB_PATH, jd["jd_id"], jd.get("company",""), jd.get("title",""), jd.get("domain",""), jd.get("created_at",""), jd["jd_text"], {"ai_extracted_skills": peopleDataSkills})
    
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
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")

    p = storage.get_profile(DB_PATH, profile_id)
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
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")

    p = storage.get_profile(DB_PATH, profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    match_score, breakdown = match((p or {}).get("skills", {}), jd["jd_skills"])
    questions = build_interview_questions(p, jd, breakdown)
    return {"profile_id": profile_id, "jd_id": jd.get("jd_id",""), "questions": questions}


@app.post("/api/match/explain")
def explain(profile_id: str = Form(...), domain: str = Form("technology"), jd_id: str = Form("")):
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    p = storage.get_profile(DB_PATH, profile_id)
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
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    p = storage.get_profile(DB_PATH, profile_id)
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
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    p = storage.get_profile(DB_PATH, profile_id)
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
    entry = {
        "id": _safe_token("TIM"),
        "token": token,
        "profile_id": profile_id or onboarding.get("profile_id", ""),
        "candidate_name": candidate_name or onboarding.get("candidate_name", "") or onboarding.get("legal_name", ""),
        "email": email or onboarding.get("email", ""),
        "work_date": work_date,
        "hours": hours,
        "client": client,
        "project": project,
        "summary": summary,
        "blockers": blockers,
        "status": "submitted_to_devready",
        "recipient": os.getenv("HEIDI_NAME", "Heidi at DevReady"),
        "recipient_email": os.getenv("HEIDI_EMAIL", "heidi@devready.io"),
        "created_at": now,
    }
    entries.insert(0, entry)
    _write_json_store(TIME_ENTRIES_PATH, entries[:2000])
    return {
        "ok": True,
        "entry": entry,
        "message": f"Time entry recorded for {entry['recipient']}.",
    }


@app.get("/api/time-entry/{token}")
def get_time_entries(token: str):
    entries = _read_json_store(TIME_ENTRIES_PATH, [])
    return {
        "token": token,
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
