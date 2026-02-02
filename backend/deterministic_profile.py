import re
from profile_schema import empty_devready_profile
from skill_lexicon import SKILL_GROUPS, SENIORITY_HINTS

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_LINKEDIN_RE = re.compile(r"(https?://(www\.)?linkedin\.com/[^\s]+)", re.IGNORECASE)

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def extract_skills(text: str):
    t = _norm(text)
    found = {k:set() for k in SKILL_GROUPS.keys()}
    for group, skills in SKILL_GROUPS.items():
        for sk in skills:
            if _norm(sk) in t:
                found[group].add(sk)
    return {k: sorted(v) for k,v in found.items()}

def build_profile_from_text(text: str):
    p = empty_devready_profile()
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    p["debug"]["text_lines"] = len(lines)
    p["debug"]["text_chars"] = len(text or "")

    name = ""
    for ln in lines[:12]:
        if "@" in ln:
            continue
        if len(ln) > 80:
            continue
        if ln.lower() in {"resume","curriculum vitae","cv"}:
            continue
        name = ln
        break
    p["contact"]["full_name"] = name or "Candidate"

    m = _EMAIL_RE.search(text or "")
    if m:
        p["contact"]["email"] = m.group(0)

    pm = _PHONE_RE.search(text or "")
    if pm:
        p["contact"]["phone"] = pm.group(0).strip()

    lm = _LINKEDIN_RE.search(text or "")
    if lm:
        p["contact"]["linkedin"] = lm.group(1)

    p["summary"]["headline"] = (lines[1] if len(lines) > 1 else "").strip()[:120]
    p["summary"]["overview"] = "Structured DevReady profile created from resume text (deterministic extraction)."

    skills = extract_skills(text or "")
    p["skills"].update(skills)

    tnorm = _norm(text)
    senior_hits = sum(1 for h in SENIORITY_HINTS if h in tnorm)

    def score_bucket(bucket):
        c = len(p["skills"][bucket])
        base = min(10, 2 + c)
        if senior_hits >= 2:
            base = min(10, base + 1)
        return base, f"Matched {c} keywords in {bucket}. Seniority hints: {senior_hits}."

    b_score, b_rat = score_bucket("backend")
    f_score, f_rat = score_bucket("frontend")
    cd_score, cd_rat = score_bucket("cloud_devops")
    d_score, d_rat = score_bucket("data")
    t_score, t_rat = score_bucket("testing")

    overall = round(min(10, (b_score + f_score + cd_score + d_score + t_score) / 5), 1)

    p["scores"]["backend"]["score"], p["scores"]["backend"]["rationale"] = b_score, b_rat
    p["scores"]["frontend"]["score"], p["scores"]["frontend"]["rationale"] = f_score, f_rat
    p["scores"]["cloud_devops"]["score"], p["scores"]["cloud_devops"]["rationale"] = cd_score, cd_rat
    p["scores"]["data"]["score"], p["scores"]["data"]["rationale"] = d_score, d_rat
    p["scores"]["testing"]["score"], p["scores"]["testing"]["rationale"] = t_score, t_rat
    p["scores"]["overall_technical"]["score"] = overall
    p["scores"]["overall_technical"]["rationale"] = "Deterministic roll-up of skill buckets (AI enrichment in next step)."

    return p
