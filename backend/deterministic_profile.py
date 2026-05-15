import re
from profile_schema import empty_devready_profile
from skill_lexicon import SKILL_GROUPS, SENIORITY_HINTS

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
_LINKEDIN_RE = re.compile(r"(https?://(www\.)?linkedin\.com/[^\s]+)", re.IGNORECASE)
_NAME_SKIP_WORDS = {
    "resume",
    "curriculum vitae",
    "cv",
    "profile",
    "summary",
    "professional summary",
    "experience",
    "work experience",
    "education",
    "skills",
    "technical skills",
}
_RESUME_SKILL_SECTIONS = {
    "skills",
    "technical skills",
    "core competencies",
    "competencies",
    "technical software",
    "software",
    "tools",
    "certifications",
}
_SECTION_HEADINGS = {
    "professional summary",
    "summary",
    "professional experience",
    "experience",
    "work experience",
    "employment history",
    "career history",
    "education",
    "select project experience",
    "selected projects",
    "projects",
    "professional affiliations",
}
_LOW_SIGNAL_ROLE_SKILLS = {"teams", "microsoft teams", "sharepoint", "microsoft excel", "excel"}

_MONTH_WORDS = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    "jul(?:y)?|aug(?:ust)?|sep(?:tember)?|sept|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_YEAR_RANGE_RE = re.compile(
    rf"(?:\b(?:{_MONTH_WORDS})\s+)?(?P<start>(?:19|20)\d{{2}})\s*(?:-|to|through|–|—)\s*(?:(?:\b(?:{_MONTH_WORDS})\s+)?(?P<finish>(?:19|20)\d{{2}})|(?P<present>present|current|now))",
    re.IGNORECASE,
)
_SINGLE_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_BULLET_RE = re.compile(r"^\s*[-*•]\s*")

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def _term_in_text(term: str, text: str) -> bool:
    clean_term = _norm(term)
    clean_text = _norm(text)
    if not clean_term or not clean_text:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(clean_term)}(?![a-z0-9])", clean_text) is not None

def extract_skills(text: str):
    found = {k:set() for k in SKILL_GROUPS.keys()}
    for group, skills in SKILL_GROUPS.items():
        for sk in skills:
            if _term_in_text(sk, text):
                found[group].add(sk)
    return {k: sorted(v) for k,v in found.items()}

def extract_resume_skill_terms(text: str, limit: int = 80):
    terms = []
    seen = set()

    def add_term(value: str):
        clean = re.sub(r"\s+", " ", _BULLET_RE.sub("", value or "")).strip(" ,;:|")
        key = _norm(clean)
        if not clean or key in seen:
            return
        if len(clean) > 80 or len(clean) < 2:
            return
        if key in _SECTION_HEADINGS or key in _RESUME_SKILL_SECTIONS:
            return
        if len(clean.split()) > 4:
            return
        seen.add(key)
        terms.append(clean)

    for group_terms in extract_skills(text or "").values():
        for term in group_terms:
            add_term(term)

    in_skill_section = False
    for raw_line in (text or "").splitlines():
        line = _clean_resume_line(raw_line)
        key = _norm(line)
        if not line:
            continue
        if key in _RESUME_SKILL_SECTIONS:
            in_skill_section = True
            continue
        if in_skill_section and key in _SECTION_HEADINGS:
            in_skill_section = False
        if not in_skill_section:
            continue
        for part in re.split(r"[,;•|]", line):
            add_term(part)
        if len(terms) >= limit:
            break

    return terms[:limit]

def extract_name(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in lines[:25]:
        lower = _norm(line)
        if lower in _NAME_SKIP_WORDS:
            continue
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line):
            continue
        if "linkedin" in lower or "github" in lower or "http" in lower or "@" in line:
            continue

        cleaned = re.sub(r"[^A-Za-zÀ-ÿ' .-]", " ", line)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-|")
        words = cleaned.split()
        if 2 <= len(words) <= 5 and len(cleaned) <= 80:
            return cleaned

    email = _EMAIL_RE.search(text or "")
    if email:
        local = email.group(0).split("@", 1)[0]
        return re.sub(r"[._-]+", " ", local).title().strip() or "Candidate"
    return "Candidate"

def _clean_resume_line(line: str) -> str:
    return re.sub(r"\s+", " ", _BULLET_RE.sub("", line or "")).strip(" |")

def extract_portfolio(text: str, limit: int = 8):
    lines = [_clean_resume_line(ln) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    resume_skills = extract_resume_skill_terms(text or "")
    items = []
    section_active = False

    for index, line in enumerate(lines):
        lower = _norm(line)
        if lower in {"experience", "work experience", "professional experience", "employment history", "career history"}:
            section_active = True
            continue
        if section_active and lower in {"education", "skills", "technical skills", "certifications", "projects"}:
            section_active = False

        date_match = _YEAR_RANGE_RE.search(line)
        single_year = _SINGLE_YEAR_RE.search(line)
        if not date_match and not (section_active and single_year):
            continue

        start_year = int(date_match.group("start")) if date_match else int(single_year.group(1))
        finish_raw = date_match.group("finish") if date_match else None
        present_raw = date_match.group("present") if date_match else None
        is_present = str(finish_raw or present_raw or "").lower() in {"present", "current", "now"}
        finish_year = None
        if finish_raw and not is_present:
            finish_year = int(finish_raw)

        title_part = _YEAR_RANGE_RE.sub("", line).strip(" ,-|–—")
        previous = lines[index - 1] if index > 0 else ""
        before_previous = lines[index - 2] if index > 1 else ""
        previous_is_role = (
            previous
            and not _YEAR_RANGE_RE.search(previous)
            and _norm(previous)
            not in {"experience", "work experience", "professional experience", "employment history", "career history"}
            and len(previous) <= 90
        )

        company = ""
        role = previous if previous_is_role else ""

        if not title_part and previous:
            company = re.split(r"\s+[–—-]\s+|,\s+", previous, maxsplit=1)[0].strip() or previous
            if before_previous and not _YEAR_RANGE_RE.search(before_previous) and len(before_previous) <= 90:
                role = before_previous
            else:
                role = "Role not listed"

        if company and role:
            pass
        elif previous_is_role and title_part:
            company_part = re.split(r"\s+[–—-]\s+|,\s+", title_part, maxsplit=1)[0].strip()
            company = company_part or title_part
        else:
            separators = [" at ", " - ", " | ", " — ", " – ", ", "]
            for sep in separators:
                if sep in title_part:
                    left, right = [part.strip() for part in title_part.split(sep, 1)]
                    if sep == " at ":
                        role, company = left, right
                    else:
                        role, company = left, right
                    break
            if not role:
                role = title_part or "Role not listed"
            if not company and previous_is_role:
                company = previous

        highlights = []
        for follow in lines[index + 1 : index + 6]:
            follow_lower = _norm(follow)
            if _YEAR_RANGE_RE.search(follow) or follow_lower in {"education", "skills", "technical skills", "certifications"}:
                break
            if len(follow) > 20:
                highlights.append(follow)
            if len(highlights) >= 5:
                break

        description = " ".join(highlights).strip()
        if not description:
            description = role
        role_text = " ".join([role, company, description, " ".join(highlights)])
        role_skills = [
            skill
            for skill in resume_skills
            if _norm(skill) not in _LOW_SIGNAL_ROLE_SKILLS and _term_in_text(skill, role_text)
        ]
        if not role_skills:
            role_skills = [
                skill
                for skill in resume_skills
                if _norm(skill) not in _LOW_SIGNAL_ROLE_SKILLS
                and any(piece in role_text for piece in _norm(skill).split() if len(piece) >= 5)
            ][:8]

        items.append(
            {
                "companyName": company or "Company not listed",
                "mainRole": role or "Role not listed",
                "description": description,
                "startDate": start_year,
                "finishDate": finish_year,
                "isPresent": is_present,
                "skills": role_skills[:12],
                "features": highlights[:5],
            }
        )
        if len(items) >= limit:
            break

    return items

def build_profile_from_text(text: str):
    p = empty_devready_profile()
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    p["debug"]["text_lines"] = len(lines)
    p["debug"]["text_chars"] = len(text or "")

    p["contact"]["full_name"] = extract_name(text)

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
    p["portfolio"] = extract_portfolio(text or "")

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
