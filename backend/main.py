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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import os, shutil, traceback
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
from jd_match import normalize_jd, match
from profile_schema import new_id
import storage
from renderers import profile_to_html, profile_to_docx, jd_to_html, jd_to_docx, match_report_to_html, match_report_to_docx

VERSION = "v2.8.6"
DB_PATH = "devready.db"
UPLOAD_DIR = "uploads"
EXPORT_DIR = "exports"


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
            st = "pdf" if "pdf" in ft else ("docx" if "docx" in ft else "pdf")

        raw = ingest(st, path)
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


@app.post("/api/match/run")
def run_match(domain: str = Form("technology"), jd_id: str = Form(None), top_k: int = Form(30)):
    jd = storage.get_jd(DB_PATH, jd_id) if jd_id else storage.get_latest_jd(DB_PATH, domain=domain)
    if not jd or not jd.get("jd_skills"):
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    jd_skills = jd["jd_skills"]

    profiles = storage.list_profiles(DB_PATH, domain=domain)
    ranked = []
    for row in profiles:
        p = storage.get_profile(DB_PATH, row["profile_id"])
        score, parts = match((p or {}).get("skills", {}), jd_skills)
        ranked.append({
            "profile_id": row["profile_id"],
            "name": row.get("full_name",""),
            "email": row.get("email",""),
            "score": score,
            "top_matches": top_matches_from_parts(parts),
            "breakdown": parts
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {"jd": {"jd_id": jd["jd_id"], "company": jd.get("company",""), "title": jd.get("title",""), "created_at": jd.get("created_at","")}, "results": ranked[:top_k]}


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

import peopleDataLabs.peopleSearch as peopleDataLabs

@app.post("/api/peopleLabs/search")
def people_labs_search(
    skills: str = Form(...),
    location: str = Form(default=None)
):
    print(f"Received PeopleLabs search request with skills: {skills}")
    skills_list = [s.strip() for s in skills.split(",") if s.strip()]

    if location == None or len(location.strip()) < 1:
        print(f"Searching PeopleLabs for skills: {skills}, location: {location}")
        outcome =peopleDataLabs.searchSkills(skills_list)
        #print('Results: ' + str(outcome))
    else:
        print(f"Searching PeopleLabs for skills: {skills}, location: {location}")
        outcome = peopleDataLabs.searchSkillsAndLocation(skills_list, location, 1)
        #print('Results: ' + str(outcome))

    return {"status": "success", "returnMessage": "Successfully searched PeopleDataLabs!", "results": outcome['data'] }

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse('<meta http-equiv="refresh" content="0; url=/ui/index.html">')
