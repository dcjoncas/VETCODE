from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from datetime import datetime, timezone
import traceback
import os
import re
import json
import requests
from urllib.parse import quote_plus, urlparse
from openai import OpenAI
from azureUtils.storage import jobs, candidates
from jd_match import normalize_jd, azureJobMatch, normalize_all_skills
from openAI import externalPeopleSearch
import peopleDataLabs.peopleSearch as peopleDataLabs
from legalSources import braveSearch, coreSignal, courtListener
from resumeProcessing.processing import ingest

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

router = APIRouter(
    prefix="/api/azureJobs",
    tags=["azure", "jobs"]
)

def _domain_key(domain: str = "dev") -> str:
    value = (domain or "dev").strip().lower()
    if value in {"technology", "tech", "devready", "dev"}:
        return "dev"
    if value in {"engineer", "engineering", "build", "buildready"}:
        return "engineer"
    if value in {"law", "legal", "legalready"}:
        return "law"
    return "dev"

def _safe_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []

def _external_result_limit(value, default: int = 10) -> int:
    try:
        limit = int(value)
    except Exception:
        limit = default
    return max(1, min(limit, 50))

def _searchable_job_skills(job_skills: list[str], limit: int = 10):
    preferred = []
    fallback = []
    noisy = {"clean", "performance", "pm", "flows", "rere"}
    replacements = {
        "python3": "Python",
        "python / django": "Python Django",
        "javascript (vanilla)": "JavaScript",
        "java,": "Java",
        "git/gitlab/github": "GitHub GitLab",
        "git hub": "GitHub",
        "github / gitlab": "GitHub GitLab",
        ".net /.net core": ".NET Core",
        "microsoft azure": "Azure",
        "google cloud platform (big query)": "Google Cloud BigQuery",
        "aws/aurora postgresql": "AWS PostgreSQL",
        "databases sql and nosql": "SQL NoSQL",
    }
    known_terms = [
        ("python", "Python"),
        ("django", "Django"),
        ("javascript", "JavaScript"),
        ("java", "Java"),
        ("c++", "C++"),
        (".net", ".NET"),
        ("aws", "AWS"),
        ("azure", "Azure"),
        ("gcp", "GCP"),
        ("google cloud", "Google Cloud"),
        ("github", "GitHub"),
        ("gitlab", "GitLab"),
        ("sql", "SQL"),
        ("postgres", "PostgreSQL"),
        ("redshift", "Redshift"),
        ("bigquery", "BigQuery"),
        ("big query", "BigQuery"),
        ("chakra", "Chakra UI"),
        ("code review", "Code Review"),
        ("full-stack", "Full Stack"),
        ("web application", "Web Application"),
    ]

    for raw in _safe_list(job_skills):
        lower = raw.lower().strip()
        if lower in noisy:
            continue
        if any(ch.isdigit() for ch in lower) and not any(tech in lower for tech in ["c++", ".net", "s3"]):
            continue
        if len(lower) < 4 and lower not in {"c++", "sql", "aws", "gcp"}:
            continue
        if not any(ch.isalpha() for ch in lower):
            continue
        skill = replacements.get(lower)
        if not skill:
            skill = next((label for token, label in known_terms if token in lower), raw.strip())
        target = preferred if any(token in lower for token, _label in known_terms) or lower in replacements else fallback
        if skill not in preferred and skill not in fallback:
            target.append(skill)

    normalized = preferred + fallback
    return normalized[:limit] or _safe_list(job_skills)[:limit]

def _skill_terms(skill: str):
    lower = str(skill or "").lower().strip()
    replacements = {
        "python3": "python",
        "js": "javascript",
        "nodejs": "node",
        "node.js": "node",
        "postgres": "postgresql",
        "big query": "bigquery",
        "google cloud": "gcp",
        "microsoft azure": "azure",
        "amazon web services": "aws",
        "git hub": "github",
    }
    terms = {lower, replacements.get(lower, lower)}
    for token in re.split(r"[\s,/&()+|:-]+", lower):
        if token in {"aws", "gcp", "sql", "c++", "c#", ".net"} or len(token) >= 4:
            terms.add(replacements.get(token, token))
    return {term for term in terms if term}

def _terms_have_soft_match(skill_terms: set[str], candidate_terms: set[str]):
    blocked_pairs = {
        ("java", "javascript"),
        ("javascript", "java"),
        ("java", "node"),
        ("node", "java"),
    }
    for term in skill_terms:
        if len(term) < 4:
            continue
        for candidate_term in candidate_terms:
            if (term, candidate_term) in blocked_pairs:
                continue
            if term in candidate_term or candidate_term in term:
                return True
    return False

def _text_mentions_skill(skill: str, text: str):
    lower_skill = str(skill or "").lower().strip()
    lower_text = str(text or "").lower()
    if lower_skill == "java":
        return bool(re.search(r"(?<![a-z0-9])java(?![a-z0-9])", lower_text))
    if lower_skill == "javascript":
        return bool(re.search(r"(?<![a-z0-9])(javascript|js)(?![a-z0-9])", lower_text))
    return lower_skill in lower_text

def _skill_weight(skill: str):
    lower = str(skill or "").lower()
    core_tokens = [
        "python", "django", "java", "javascript", "typescript", "react", "node", "c++", "c#",
        ".net", "sql", "postgres", "mysql", "mongodb", "aws", "azure", "gcp", "google cloud",
        "kubernetes", "docker", "redshift", "bigquery", "snowflake", "github", "gitlab",
    ]
    generic_tokens = [
        "clean", "performance", "code review", "web application", "full stack", "pm",
        "product owner", "collaboration", "problem solving", "flows",
    ]
    if any(token in lower for token in core_tokens):
        return 1.5
    if any(token in lower for token in generic_tokens):
        return 0.75
    return 1.0

def _score_band(score: int):
    if score >= 75:
        return "Strong match"
    if score >= 50:
        return "Qualified match"
    if score > 0:
        return "Below threshold"
    return "No measurable match"

def _deterministic_fit_reason(candidate_name: str, score: int, top_matches: list[str], score_details: dict, culture_match: int = -1):
    matched = _safe_list(top_matches)
    missing = _safe_list((score_details or {}).get("missing"))[:5]
    scoring_skills = _safe_list((score_details or {}).get("scoring_skills"))[:8]
    band = (score_details or {}).get("band") or _score_band(score)
    matched_count = (score_details or {}).get("matched_count", len(matched))
    required_count = (score_details or {}).get("required_count", len(scoring_skills))
    matched_weight = (score_details or {}).get("matched_weight", 0)
    required_weight = (score_details or {}).get("required_weight", 0)

    if score >= 75:
        decision = "Strong fit"
    elif score >= 50:
        decision = "Fit"
    elif score > 0:
        decision = "Below 50% review"
    else:
        decision = "Not enough evidence"

    reason_bits = [
        f"{band}: matched {matched_count} of {required_count} weighted JD signals",
    ]
    if required_weight:
        reason_bits.append(f"({matched_weight}/{required_weight} weighted coverage)")
    if matched:
        reason_bits.append("matched " + ", ".join(matched[:5]))
    if missing:
        reason_bits.append("missing or not found: " + ", ".join(missing))
    if culture_match >= 0:
        reason_bits.append(f"culture match {culture_match}%")
    if score < 50:
        reason_bits.append("Below 50% means the stored profile does not show enough required JD signals yet; verify resume/chat before rejecting.")
    return {
        "fit_decision": decision,
        "fit_reason": ". ".join(reason_bits) + ".",
        "score_formula": "Score = weighted matched JD signals / weighted searchable JD signals.",
        "scoring_skills": scoring_skills,
    }

def _ai_fit_explanations(jd: dict, scoring_skills: list[str], rows: list[dict]):
    if not os.getenv("OPENAI_API_KEY", "").strip() or not rows:
        return {}
    payload = {
        "job": {
            "company": jd.get("company", ""),
            "title": jd.get("title", ""),
            "skills_used_for_scoring": scoring_skills,
            "description_excerpt": (jd.get("jd_text") or jd.get("description") or "")[:2500],
        },
        "candidates": [
            {
                "profile_id": row.get("profile_id", ""),
                "name": row.get("name", ""),
                "score": row.get("score", 0),
                "matched": row.get("top_matches", []),
                "missing": (row.get("score_details") or {}).get("missing", []),
                "candidate_skills": row.get("breakdown", [])[:40] if isinstance(row.get("breakdown"), list) else row.get("breakdown", {}),
                "culture_match": row.get("culture_match", -1),
            }
            for row in rows[:20]
        ],
    }
    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MATCH_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You explain recruiting match scores. Return strict JSON only. "
                        "For each candidate, give concise, evidence-based reasoning. "
                        "Do not invent skills. If score is below 50, clearly state why it is below threshold and what to validate."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return JSON with this shape: "
                        "{\"items\":[{\"profile_id\":\"...\",\"fit_decision\":\"Strong fit|Fit|Below 50% review|Not enough evidence\","
                        "\"fit_reason\":\"one or two sentences\",\"score_formula\":\"brief formula explanation\"}]}.\n\n"
                        + json.dumps(payload, ensure_ascii=False)
                    ),
                },
            ],
        )
        content = (response.choices[0].message.content or "{}").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content).strip()
        parsed = json.loads(content)
        return {
            str(item.get("profile_id")): item
            for item in parsed.get("items", [])
            if item.get("profile_id")
        }
    except Exception as exc:
        print(f"AI match explanation failed: {exc}")
        return {}

def _rank_external_skill_match(raw_skills: list[str], job_skills: list[str], scoring_skills: list[str] | None = None):
    candidate_skills = _safe_list(raw_skills)
    skill_basis = _safe_list(scoring_skills)[:12] if scoring_skills else _searchable_job_skills(_safe_list(job_skills), 12)
    matched = []
    missing = []
    candidate_terms = set()
    for skill in candidate_skills:
        candidate_terms.update(_skill_terms(skill))

    total_weight = 0.0
    matched_weight = 0.0

    for skill in skill_basis:
        skill_terms = _skill_terms(skill)
        weight = _skill_weight(skill)
        total_weight += weight
        matched_skill = bool(skill_terms & candidate_terms)
        if not matched_skill:
            matched_skill = _terms_have_soft_match(skill_terms, candidate_terms)
        if matched_skill:
            matched.append(skill)
            matched_weight += weight
        else:
            missing.append(skill)

    unique_matches = list(dict.fromkeys(matched))
    score = round((matched_weight / max(total_weight, 1.0)) * 100)
    if len(skill_basis) > 1 and len(unique_matches) < 2 and score >= 50:
        score = 49
    details = {
        "formula": "weighted matched JD signals / weighted searchable JD signals",
        "matched_count": len(unique_matches),
        "required_count": len(skill_basis),
        "matched_weight": round(matched_weight, 2),
        "required_weight": round(total_weight, 2),
        "band": _score_band(score),
        "scoring_skills": skill_basis,
        "missing": missing[:8],
    }
    return score, unique_matches, details

LAWYER_TITLE_DEFAULTS = ["associate attorney", "attorney", "lawyer", "counsel"]
LAWYER_PRACTICE_TERMS = [
    "professional liability",
    "civil litigation",
    "litigation defense",
    "insurance defense",
    "professional negligence",
    "architects and engineers",
    "real estate malpractice",
    "broker malpractice",
    "insurance agent malpractice",
    "accounting malpractice",
    "malpractice",
    "depositions",
    "trial preparation",
]
LAWYER_SUPPORTING_TERMS = {
    "civil litigation",
    "litigation defense",
    "malpractice",
    "depositions",
    "trial preparation",
}


def _split_external_terms(value, limit: int = 20):
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = re.split(r"[,;\n]+", str(value or ""))
    cleaned = []
    for item in raw:
        term = str(item or "").strip()
        if term and term.lower() not in {existing.lower() for existing in cleaned}:
            cleaned.append(term)
        if len(cleaned) >= limit:
            break
    return cleaned


def _lawyer_search_criteria(
    jd: dict,
    titles=None,
    practice_areas=None,
    locations=None,
    region: str = "",
    min_years: int = 0,
    strict_locations: bool | None = None,
):
    title = str(jd.get("title") or "")
    description = str(jd.get("description") or jd.get("jd_text") or "")
    combined = f"{title}\n{description}"
    lower = combined.lower()

    resolved_titles = _split_external_terms(titles, 8) or list(LAWYER_TITLE_DEFAULTS)
    resolved_practice = _split_external_terms(practice_areas, 12)
    if not resolved_practice:
        resolved_practice = [term for term in LAWYER_PRACTICE_TERMS if term in lower]
    if not resolved_practice:
        resolved_practice = ["civil litigation", "litigation"]
    required_practice = [
        term
        for term in resolved_practice
        if term.strip().lower() not in LAWYER_SUPPORTING_TERMS
    ] or list(resolved_practice)

    resolved_locations = _split_external_terms(locations, 12)
    if not resolved_locations:
        title_location = re.search(r"\bin\s+(.+)$", title, flags=re.IGNORECASE)
        if title_location:
            resolved_locations.extend(_split_external_terms(title_location.group(1), 12))
        for city in re.findall(r"(?m)^\s*([A-Za-z][A-Za-z .'-]+),\s*CA\b", description):
            if city.lower() not in {value.lower() for value in resolved_locations}:
                resolved_locations.append(city.strip())

    resolved_region = str(region or "").strip()
    if not resolved_region and ("california" in lower or ", ca" in lower):
        resolved_region = "California"
    if not resolved_region:
        resolved_region = "California"

    try:
        resolved_years = max(0, min(int(min_years or 0), 60))
    except (TypeError, ValueError):
        resolved_years = 0
    if not resolved_years:
        years_match = re.search(r"\b(\d{1,2})\s*\+?\s*years?\b", combined, flags=re.IGNORECASE)
        if years_match:
            resolved_years = max(0, min(int(years_match.group(1)), 60))

    if strict_locations is None:
        resolved_strict_locations = bool(resolved_locations)
    else:
        resolved_strict_locations = bool(strict_locations)

    return {
        "titles": resolved_titles,
        "practiceAreas": resolved_practice,
        "requiredPracticeAreas": required_practice,
        "locations": resolved_locations,
        "region": resolved_region,
        "minYears": resolved_years,
        "strictLocations": resolved_strict_locations,
    }


def _lawyer_match_score(row: dict, criteria: dict):
    title = str(row.get("job_title") or row.get("headline") or "")
    region = str(row.get("location_region") or "")
    locality = str(row.get("location_locality") or "")
    try:
        years = int(row.get("inferred_years_experience") or 0)
    except (TypeError, ValueError):
        years = 0

    evidence = " ".join(
        [
            title,
            str(row.get("headline") or ""),
            str(row.get("summary") or ""),
            str(row.get("job_summary") or ""),
            " ".join(_safe_list(row.get("skills"))),
            " ".join(
                str(item.get("summary") or "")
                for item in row.get("experience", [])
                if isinstance(item, dict)
            ),
        ]
    ).lower()

    title_matches = [term for term in criteria.get("titles", []) if term.lower() in title.lower()]
    scoring_practice = criteria.get("requiredPracticeAreas") or criteria.get("practiceAreas", [])
    practice_matches = [term for term in scoring_practice if term.lower() in evidence]
    region_match = not criteria.get("region") or criteria["region"].lower() == region.lower()
    target_locations = criteria.get("locations", [])
    location_match = not target_locations or locality.lower() in {item.lower() for item in target_locations}
    years_match = not criteria.get("minYears") or years >= int(criteria.get("minYears") or 0)

    title_points = 30 if title_matches else 0
    practice_target = max(1, min(3, len(scoring_practice)))
    practice_points = round(35 * min(len(practice_matches) / practice_target, 1), 1)
    region_points = 15 if region_match else 0
    location_points = 10 if location_match else 0
    years_points = 10 if years_match else 0
    score = round(title_points + practice_points + region_points + location_points + years_points)

    matched = []
    if title_matches:
        matched.append("Attorney title")
    matched.extend(practice_matches[:5])
    if region_match and criteria.get("region"):
        matched.append(criteria["region"])
    if location_match and target_locations:
        matched.append(locality)
    if years_match and criteria.get("minYears"):
        matched.append(f"{years}+ years experience")

    missing = []
    if not title_matches:
        missing.append("Current attorney/lawyer title")
    if not practice_matches:
        missing.append("Professional liability or litigation evidence")
    if not region_match:
        missing.append(criteria.get("region") or "Required region")
    if target_locations and not location_match:
        missing.append("Target office city")
    if criteria.get("minYears") and not years_match:
        missing.append(f"{criteria['minYears']}+ years experience")

    return score, list(dict.fromkeys(matched)), {
        "formula": "30 title + 35 practice evidence + 15 state + 10 target city + 10 experience",
        "matched_count": len(list(dict.fromkeys(matched))),
        "required_count": 5,
        "matched_weight": score,
        "required_weight": 100,
        "band": _score_band(score),
        "scoring_skills": scoring_practice,
        "missing": missing,
        "components": {
            "attorneyTitle": title_points,
            "practiceEvidence": practice_points,
            "state": region_points,
            "targetCity": location_points,
            "experience": years_points,
        },
    }


def _pdl_source_audit(response: dict, criteria: dict | None = None, query_mode: str = "skills"):
    rows = response.get("data", []) if isinstance(response, dict) else []
    requested_size = int(response.get("requested_size") or len(rows)) if isinstance(response, dict) else len(rows)
    effective_size = int(response.get("effective_size") or requested_size) if isinstance(response, dict) else requested_size
    credit_limited = bool(response.get("credit_limited")) if isinstance(response, dict) else False
    return {
        "provider": "People Data Labs",
        "queryExecuted": True,
        "queryMode": query_mode,
        "apiStatus": int(response.get("status") or 200) if isinstance(response, dict) else 200,
        "totalMatches": int(response.get("total") or 0) if isinstance(response, dict) else 0,
        "recordsReturned": len(rows) if isinstance(rows, list) else 0,
        "recordsReviewed": len(rows) if isinstance(rows, list) else 0,
        "estimatedCreditsUsed": len(rows) if isinstance(rows, list) else 0,
        "costLabel": "record credits",
        "creditLimited": credit_limited,
        "requestedPageSize": requested_size,
        "effectivePageSize": effective_size,
        "hasMore": bool(response.get("scroll_token")) if isinstance(response, dict) else False,
        "executedAt": datetime.now(timezone.utc).isoformat(),
        "criteria": criteria or {},
        "contactData": "No personal email, phone, or street address requested in discovery search.",
        "legalReadiness": "California Bar status must be verified before permanent use or outreach.",
        "linkedInMode": "Profile link only; no LinkedIn scraping was performed.",
        "statusMessage": (
            f"PDL rejected the requested {requested_size}-record page, so VETCODE retried with "
            f"{effective_size} record to preserve a real result without exceeding available credits."
            if credit_limited
            else ""
        ),
    }


def _pdl_pagination(response: dict, page_size: int):
    token = str(response.get("scroll_token") or "").strip() if isinstance(response, dict) else ""
    effective_size = int(response.get("effective_size") or page_size) if isinstance(response, dict) else page_size
    requested_size = int(response.get("requested_size") or page_size) if isinstance(response, dict) else page_size
    pagination = {
        "pageSize": effective_size,
        "hasNext": bool(token),
        "nextScrollToken": token,
        "costLabel": f"up to {effective_size} record credits",
    }
    if effective_size < requested_size:
        pagination["requestedPageSize"] = requested_size
        pagination["creditLimited"] = True
    return pagination


def _provider_label(source: str) -> str:
    return {
        "pdl": "People Data Labs",
        "github": "GitHub Public API",
        "coresignal": "Coresignal",
        "brave": "Brave Search",
        "courtlistener": "CourtListener / RECAP",
    }.get(source, "External provider")


def _provider_search_error_response(
    source: str,
    error: Exception,
    page_size: int,
    criteria: dict | None = None,
    extra: dict | None = None,
):
    provider = _provider_label(source)
    upstream_status = getattr(error, "status_code", None)
    status_code = 503 if upstream_status == 503 else 502
    error_code = "provider_search_failed"
    detail = f"{provider} search could not be completed. No candidate records were returned or reviewed."
    action_label = "Review provider account"
    action_url = ""
    retryable = status_code == 503

    if source == "pdl" and upstream_status == 402:
        status_code = 402
        error_code = "provider_credits_required"
        detail = (
            "People Data Labs does not have enough Person Search credits to return the requested page. "
            "Add or renew credits in the PDL API Dashboard, then retry. No candidate records were returned "
            "or reviewed, and no TEMP profiles were created."
        )
        action_label = "Open PDL usage and billing"
        action_url = "https://dashboard.peopledatalabs.com/"
        retryable = False
    elif upstream_status == 429:
        status_code = 429
        error_code = "provider_rate_limited"
        detail = f"{provider} has reached its request limit. No candidate records were returned or reviewed."
        action_label = "Try again later"
        retryable = True

    query_executed = upstream_status is not None or "not configured" not in str(error).lower()
    content = {
        "detail": detail,
        "code": error_code,
        "source": source,
        "results": [],
        "sourceAudit": {
            "provider": provider,
            "queryExecuted": query_executed,
            "queryCompleted": False,
            "apiStatus": upstream_status,
            "totalMatches": None,
            "recordsReturned": 0,
            "recordsReviewed": 0,
            "estimatedCreditsUsed": 0,
            "costLabel": "no credits used",
            "criteria": criteria or {},
            "statusMessage": detail,
            "error": str(error),
        },
        "providerStatus": {
            "code": error_code,
            "upstreamStatus": upstream_status,
            "retryable": retryable,
            "actionLabel": action_label,
            "actionUrl": action_url,
            "alternatives": [
                {
                    "source": "coresignal",
                    "label": "Coresignal",
                    "configured": coreSignal.configured(),
                    "role": "professional candidate discovery",
                },
                {
                    "source": "brave",
                    "label": "Brave Search",
                    "configured": braveSearch.configured(),
                    "role": "public web research only",
                },
            ],
        },
        "pagination": {
            "pageSize": page_size,
            "hasNext": False,
            "nextScrollToken": "",
            "costLabel": "request failed before records were returned",
        },
    }
    content.update(extra or {})
    return JSONResponse(status_code=status_code, content=content)


def _provider_page(value: str, default: int, maximum: int) -> int:
    clean = str(value or "").strip()
    if not clean:
        return default
    if len(clean) > 4 or not clean.isdigit():
        raise HTTPException(status_code=400, detail="The provider page token is invalid.")
    return max(default, min(int(clean), maximum))


def _provider_pagination(response: dict, page_size: int, cost_label: str):
    next_page = response.get("next_page") if isinstance(response, dict) else None
    return {
        "pageSize": page_size,
        "hasNext": next_page is not None,
        "nextScrollToken": str(next_page) if next_page is not None else "",
        "costLabel": cost_label,
    }


def _provider_source_audit(
    provider: str,
    response: dict,
    criteria: dict | None,
    query_mode: str,
    cost_label: str,
):
    rows = response.get("data", []) if isinstance(response, dict) else []
    returned = len(rows) if isinstance(rows, list) else 0
    credits = int(response.get("credits_used") or response.get("requests_used") or 1)
    return {
        "provider": provider,
        "queryExecuted": True,
        "queryMode": query_mode,
        "apiStatus": int(response.get("status") or 200),
        "totalMatches": int(response.get("total") or returned),
        "recordsReturned": returned,
        "recordsReviewed": returned,
        "estimatedCreditsUsed": credits,
        "costLabel": cost_label,
        "hasMore": bool(response.get("has_more")),
        "executedAt": datetime.now(timezone.utc).isoformat(),
        "criteria": criteria or {},
        "contactData": "No personal email, phone, or street address requested in discovery search.",
        "legalReadiness": "California Bar status and candidate identity require manual verification.",
        "linkedInMode": "Professional profile links may be returned; no LinkedIn scraping was performed.",
    }


def _location_fields(location: str, criteria: dict | None):
    clean = str(location or "")
    region = "California" if re.search(r"\bCalifornia\b|,\s*CA(?:\s|,|$)", clean, re.IGNORECASE) else ""
    locality = ""
    for city in (criteria or {}).get("locations", []):
        if str(city).lower() in clean.lower():
            locality = str(city)
            break
    return locality, region


def _coresignal_row(
    row: dict,
    job_skills: list[str],
    scoring_skills: list[str],
    lawyer_criteria: dict | None = None,
):
    location = str(row.get("location") or row.get("experience_location") or "")
    locality, region = _location_fields(location, lawyer_criteria)
    headline = str(row.get("headline") or row.get("title") or "")
    mapped = {
        "id": row.get("id"),
        "full_name": row.get("full_name"),
        "job_title": row.get("title") or headline,
        "job_company_name": row.get("company_name") or "",
        "location_name": location,
        "location_locality": locality,
        "location_region": region,
        "location_country": row.get("country") or "",
        "summary": " ".join(
            value
            for value in [headline, str(row.get("company_industry") or "")]
            if value
        ),
        "skills": [
            value
            for value in [headline, row.get("title"), row.get("company_industry")]
            if value
        ],
        "linkedin_url": row.get("profile_url") or "",
    }
    result = _people_data_row(mapped, job_skills, scoring_skills, lawyer_criteria)
    result.update(
        {
            "source": "coresignal",
            "source_label": "Coresignal profile preview",
            "result_type": "professional_profile_preview",
            "summary": headline,
            "profile_data": {
                "connections_count": row.get("connections_count") or 0,
                "follower_count": row.get("follower_count") or 0,
                "experience_count": row.get("experience_count") or 0,
                "company_website": row.get("company_website") or "",
            },
        }
    )
    result["verification"].pop("pdl_job_last_verified", None)
    result["verification"]["coresignal_preview"] = "unverified_public_profile_data"
    return result


def _brave_law_query(criteria: dict) -> str:
    def phrase(value) -> str:
        return str(value or "").replace('"', " ").strip()[:45]

    titles = " OR ".join(f'"{phrase(term)}"' for term in criteria.get("titles", [])[:3])
    practices = " OR ".join(
        f'"{phrase(term)}"' for term in criteria.get("requiredPracticeAreas", [])[:3]
    )
    locations = " OR ".join(f'"{phrase(term)}"' for term in criteria.get("locations", [])[:3])
    parts = [f"({titles})" if titles else '"attorney"']
    if practices:
        parts.append(f"({practices})")
    if locations:
        parts.append(f"({locations})")
    if criteria.get("region"):
        parts.append(f'"{phrase(criteria["region"])}"')
    parts.extend(['("attorney bio" OR "lawyer profile")', "-jobs", "-careers", "-site:linkedin.com"])
    return " ".join(parts)


def _brave_direct_query(query: str) -> str:
    without_linkedin_scope = re.sub(
        r"(?i)(?:^|\s)[+-]?site:(?:www\.)?linkedin\.com\b",
        " ",
        str(query or ""),
    )
    return " ".join(without_linkedin_scope.split()) + " -site:linkedin.com"


def _brave_row(
    row: dict,
    job_skills: list[str],
    scoring_skills: list[str],
    lawyer_criteria: dict | None = None,
):
    name = str(row.get("title") or "Public legal profile").strip()
    description = str(row.get("description") or "").strip()
    profile_url = str(row.get("url") or "").strip()
    if lawyer_criteria:
        score, matched, details = _lawyer_match_score(
            {"job_title": name, "summary": description, "skills": []},
            lawyer_criteria,
        )
    else:
        score, matched, details = _rank_external_skill_match(
            [name, description], job_skills, scoring_skills
        )
    domain = urlparse(profile_url).netloc.lower().removeprefix("www.") if profile_url else ""
    return {
        "source": "brave",
        "source_label": "Brave public web result",
        "source_id": profile_url,
        "result_type": "public_web_evidence",
        "name": name,
        "email": "",
        "title": "Public legal profile or evidence page",
        "company": domain,
        "location": "",
        "profile_url": profile_url,
        "avatar_url": "",
        "summary": description,
        "skills": matched,
        "score": score,
        "match_band": details["band"],
        "score_details": details,
        "top_matches": matched,
        "verification": {
            "california_bar_status": "not_verified" if lawyer_criteria else "not_applicable",
            "california_bar_search_url": (
                "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch?FreeText="
                + quote_plus(name)
                if lawyer_criteria
                else ""
            ),
            "public_page_identity": "not_verified",
            "linkedin_scan": "not_performed",
        },
        "profile_data": {"web_description": description, "web_domain": domain},
    }


def _courtlistener_row(row: dict, searched_name: str):
    evidence_type = str(row.get("evidenceType") or "court_record")
    evidence_label = "RECAP docket" if evidence_type == "recap_docket" else "Published opinion"
    title = str(row.get("title") or "Court record").strip()
    court = str(row.get("court") or "").strip()
    docket_number = str(row.get("docketNumber") or "").strip()
    date_filed = str(row.get("dateFiled") or "").strip()
    return {
        "source": "courtlistener",
        "source_label": "CourtListener / RECAP",
        "source_id": str(row.get("url") or docket_number or title),
        "result_type": "court_record_evidence",
        "name": title,
        "email": "",
        "title": evidence_label,
        "company": court,
        "location": date_filed,
        "profile_url": str(row.get("url") or ""),
        "avatar_url": "",
        "summary": str(row.get("snippet") or "").strip(),
        "skills": [],
        "score": 0,
        "match_band": "Research evidence",
        "score_details": {
            "formula": "Court records are not used for candidate-fit scoring.",
            "band": "Research evidence",
            "missing": [],
        },
        "top_matches": [],
        "verification": {
            "identity_status": "not_verified",
            "role_in_matter": "not_verified",
            "california_bar_status": "not_verified",
            "california_bar_search_url": (
                "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch?FreeText="
                + quote_plus(searched_name)
            ),
            "linkedin_scan": "not_performed",
        },
        "profile_data": {
            "searched_name": searched_name,
            "evidence_type": evidence_type,
            "docket_number": docket_number,
            "date_filed": date_filed,
            "attorney_field": str(row.get("attorney") or ""),
        },
    }


def _courtlistener_attorney_row(row: dict, criteria: dict):
    name = str(row.get("name") or "CourtListener attorney lead").strip()
    evidence = [item for item in row.get("evidence", []) if isinstance(item, dict)]
    first_evidence = evidence[0] if evidence else {}
    evidence_count = len(evidence)
    matched_terms = _safe_list(row.get("matchedPracticeAreas"))
    courts = _safe_list(row.get("courts"))
    case_titles = [str(item.get("title") or "").strip() for item in evidence if item.get("title")]
    case_summary = "; ".join(case_titles[:3])
    summary = (
        f"Listed as an attorney in {evidence_count} CourtListener docket"
        f"{'s' if evidence_count != 1 else ''} returned by the selected JD query."
    )
    if case_summary:
        summary += f" Supporting records: {case_summary}."
    return {
        "source": "courtlistener",
        "source_label": "CourtListener / RECAP",
        "source_id": "courtlistener-attorney:" + str(row.get("attorneyId") or name.lower()),
        "result_type": "court_attorney_lead",
        "name": name,
        "email": str(row.get("email") or ""),
        "title": "Attorney listed in matching court records",
        "company": "",
        "location": ", ".join(courts[:3]),
        "profile_url": str(first_evidence.get("url") or ""),
        "avatar_url": "",
        "summary": summary,
        "skills": [],
        "score": 0,
        "match_band": "Court-data lead",
        "score_details": {
            "formula": "Court-docket association is not candidate-fit scoring.",
            "band": "Court-data lead",
            "missing": [
                "Current employer and role",
                f"{int(criteria.get('minYears') or 0)}+ years experience"
                if criteria.get("minYears")
                else "Years of experience",
                "Current location",
                "California Bar standing",
            ],
        },
        "top_matches": [f"Court query: {term}" for term in matched_terms[:4]],
        "verification": {
            "identity_status": "not_verified",
            "role_in_matter": "listed_on_matching_docket_not_individually_confirmed",
            "california_bar_status": "not_verified",
            "california_bar_search_url": (
                "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch?FreeText="
                + quote_plus(name)
            ),
            "current_employment": "not_verified",
            "linkedin_scan": "not_performed",
        },
        "profile_data": {
            "courtlistener_attorney_id": str(row.get("attorneyId") or ""),
            "evidence_count": evidence_count,
            "evidence_records": evidence[:8],
            "matched_practice_areas": matched_terms,
            "query_practice_areas": _safe_list(
                criteria.get("requiredPracticeAreas") or criteria.get("practiceAreas")
            ),
            "courts": courts,
            "phone": str(row.get("phone") or ""),
            "contact_raw": str(row.get("contactRaw") or ""),
            "discovered_from_jd": True,
        },
    }


def _person_name_tokens(value: str) -> list[str]:
    titles_and_suffixes = {"esq", "esquire", "jr", "sr", "ii", "iii", "iv"}
    tokens = re.findall(r"[a-z]+", str(value or "").lower())
    return [token for token in tokens if token not in titles_and_suffixes]


def _person_names_align(searched_name: str, returned_name: str) -> bool:
    searched = _person_name_tokens(searched_name)
    returned = _person_name_tokens(returned_name)
    if len(searched) < 2 or len(returned) < 2:
        return False
    if searched == returned:
        return True
    if searched[0] != returned[0] or searched[-1] != returned[-1]:
        return False
    searched_middle = [token[0] for token in searched[1:-1] if token]
    returned_middle = [token[0] for token in returned[1:-1] if token]
    return not searched_middle or not returned_middle or searched_middle == returned_middle


def _people_data_row(
    row: dict,
    job_skills: list[str],
    scoring_skills: list[str],
    lawyer_criteria: dict | None = None,
):
    skills = _safe_list(row.get("skills"))
    if lawyer_criteria:
        score, top_matches, score_details = _lawyer_match_score(row, lawyer_criteria)
    else:
        score, top_matches, score_details = _rank_external_skill_match(skills, job_skills, scoring_skills)
    first = row.get("first_name") or ""
    last = row.get("last_name") or ""
    name = (first + " " + last).strip() or row.get("full_name") or "Unknown candidate"
    linkedin_url = row.get("linkedin_url") or ""
    if linkedin_url and not linkedin_url.startswith("http"):
        linkedin_url = "https://www." + linkedin_url.lstrip("/")
    email = row.get("work_email") or ""
    if not isinstance(email, str):
        email = ""
    location = row.get("location_name") or ", ".join([v for v in [row.get("location_locality"), row.get("location_region"), row.get("location_country")] if isinstance(v, str) and v])
    if not isinstance(location, str):
        location = ""

    return {
        "source": "pdl",
        "source_label": "People Data Labs",
        "source_id": row.get("id") or "",
        "name": name,
        "email": email,
        "title": row.get("job_title") or row.get("title") or "",
        "company": row.get("job_company_name") or "",
        "location": location,
        "profile_url": linkedin_url,
        "avatar_url": "",
        "summary": row.get("summary") or row.get("headline") or "",
        "skills": skills,
        "years_experience": row.get("inferred_years_experience") or 0,
        "job_last_verified": row.get("job_last_verified") or "",
        "score": score,
        "match_band": score_details["band"],
        "score_details": score_details,
        "top_matches": top_matches,
        "verification": {
            "california_bar_status": "not_verified" if lawyer_criteria else "not_applicable",
            "california_bar_search_url": (
                "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch?FreeText="
                + quote_plus(name)
                if lawyer_criteria
                else ""
            ),
            "pdl_job_last_verified": row.get("job_last_verified") or "",
            "linkedin_scan": "not_performed",
        },
        "profile_data": {
            "experience": row.get("experience", [])[:5] if isinstance(row.get("experience"), list) else [],
            "education": row.get("education", [])[:3] if isinstance(row.get("education"), list) else [],
            "certifications": row.get("certifications", [])[:5] if isinstance(row.get("certifications"), list) else [],
            "github_url": row.get("github_url") or "",
            "headline": row.get("headline") or "",
            "job_summary": row.get("job_summary") or "",
            "industry": row.get("industry") or "",
            "location": {
                "name": row.get("location_name") or "",
                "locality": row.get("location_locality") or "",
                "region": row.get("location_region") or "",
                "country": row.get("location_country") or "",
            },
        },
    }

def _github_headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def _github_search(job_skills: list[str], scoring_skills: list[str], size: int = 5):
    size = _external_result_limit(size)
    selected_skills = _safe_list(scoring_skills)[:8]
    seen_logins = set()
    search_queries = []
    candidate_logins = []
    candidate_pool_size = max(size * 8, 30)

    for skill in selected_skills:
        if len(candidate_logins) >= candidate_pool_size:
            break

        normalized_terms = _skill_terms(skill)
        language_term = "Java" if "java" in normalized_terms else skill
        repo_query = (
            f"language:{language_term} stars:>1"
            if len(normalized_terms & {"java", "python", "javascript", "typescript", "c#", "c++"}) > 0
            else f"{skill} stars:>3"
        )
        search_queries.append(repo_query)
        repo_search_response = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": repo_query, "sort": "stars", "order": "desc", "per_page": 10},
            headers=_github_headers(),
            timeout=12,
        )
        if repo_search_response.status_code >= 400:
            raise Exception(f"GitHub repository search failed with status {repo_search_response.status_code}: {repo_search_response.text[:180]}")

        for repo in repo_search_response.json().get("items", []):
            owner = (repo.get("owner") or {}).get("login")
            if owner and owner not in seen_logins:
                seen_logins.add(owner)
                candidate_logins.append(owner)
            if len(candidate_logins) >= candidate_pool_size:
                break

        if len(candidate_logins) >= candidate_pool_size:
            break

        query = f"{skill} in:bio type:user"
        search_queries.append(query)
        search_response = requests.get(
            "https://api.github.com/search/users",
            params={"q": query, "per_page": min(max(size * 3, size), 50)},
            headers=_github_headers(),
            timeout=12,
        )
        if search_response.status_code >= 400:
            raise Exception(f"GitHub search failed with status {search_response.status_code}: {search_response.text[:180]}")

        search_items = search_response.json().get("items", [])
        for item in search_items:
            login = item.get("login")
            if not login or login in seen_logins:
                continue
            seen_logins.add(login)
            candidate_logins.append(login)
            if len(candidate_logins) >= candidate_pool_size:
                break

    enriched_rows = []
    for login in candidate_logins:
        if len(enriched_rows) >= size:
            break
        if not login:
            continue

        user_response = requests.get(
            f"https://api.github.com/users/{login}",
            headers=_github_headers(),
            timeout=12,
        )
        user = user_response.json() if user_response.status_code == 200 else item
        if (user.get("type") or "").lower() != "user":
            continue

        repo_response = requests.get(
            f"https://api.github.com/users/{login}/repos",
            params={"sort": "updated", "per_page": 8},
            headers=_github_headers(),
            timeout=12,
        )
        repos = repo_response.json() if repo_response.status_code == 200 else []
        if not isinstance(repos, list):
            repos = []

        repo_skills = []
        for repo in repos:
            repo_skills.extend(_safe_list(repo.get("topics")))
            if repo.get("language"):
                repo_skills.append(repo.get("language"))

        repo_text = " ".join(
            [
                " ".join(
                    [
                        str(repo.get("name") or ""),
                        str(repo.get("description") or ""),
                        str(repo.get("language") or ""),
                        " ".join(_safe_list(repo.get("topics"))),
                    ]
                )
                for repo in repos
            ]
        )
        candidate_text = " ".join([str(user.get("bio") or ""), repo_text]).lower()
        inferred_skills = list(dict.fromkeys(
            [skill for skill in job_skills if _text_mentions_skill(skill, candidate_text)]
            + [skill for skill in selected_skills if _text_mentions_skill(skill, candidate_text)]
            + repo_skills
        ))
        score, top_matches, score_details = _rank_external_skill_match(inferred_skills, job_skills, selected_skills)

        enriched_rows.append({
            "source": "github",
            "source_label": "GitHub",
            "source_id": login,
            "name": user.get("name") or login,
            "email": user.get("email") or "",
            "title": "",
            "company": user.get("company") or "",
            "location": user.get("location") or "",
            "profile_url": user.get("html_url") or f"https://github.com/{login}",
            "avatar_url": user.get("avatar_url") or "",
            "summary": user.get("bio") or "",
            "skills": inferred_skills,
            "score": score,
            "match_band": score_details["band"],
            "score_details": score_details,
            "top_matches": top_matches,
            "repo_count": user.get("public_repos") or len(repos),
            "recent_repos": [
                {
                    "name": repo.get("name"),
                    "language": repo.get("language"),
                    "description": repo.get("description"),
                    "url": repo.get("html_url"),
                }
                for repo in repos[:5]
            ],
            "profile_data": {
                "github_login": login,
                "bio": user.get("bio") or "",
                "blog": user.get("blog") or "",
                "followers": user.get("followers") or 0,
                "public_repos": user.get("public_repos") or len(repos),
                "repos": [
                    {
                        "name": repo.get("name"),
                        "language": repo.get("language"),
                        "topics": _safe_list(repo.get("topics")),
                        "description": repo.get("description"),
                        "stars": repo.get("stargazers_count") or 0,
                        "url": repo.get("html_url"),
                    }
                    for repo in repos[:8]
                ],
            },
            "search_queries": search_queries,
        })

    enriched_rows.sort(key=lambda row: row["score"], reverse=True)
    return enriched_rows

def _github_direct_search(search_query: str, search_terms: list[str], size: int = 5):
    size = _external_result_limit(size)
    query = (search_query or "").strip()
    selected_skills = _safe_list(search_terms)[:8]
    seen_logins = set()
    candidate_logins = []
    search_queries = []

    if selected_skills:
        seeded_rows = _github_search(selected_skills, selected_skills, max(size, 10))
        for row in seeded_rows:
            login = row.get("source_id")
            if login and login not in seen_logins:
                seen_logins.add(login)
                candidate_logins.append(login)

    if query:
        direct_query = f"{query} type:user"
        search_queries.append(direct_query)
        search_response = requests.get(
            "https://api.github.com/search/users",
            params={"q": direct_query, "per_page": min(max(size * 3, size), 50)},
            headers=_github_headers(),
            timeout=12,
        )
        if search_response.status_code >= 400:
            raise Exception(f"GitHub direct search failed with status {search_response.status_code}: {search_response.text[:180]}")
        for item in search_response.json().get("items", []):
            login = item.get("login")
            if login and login not in seen_logins:
                seen_logins.add(login)
                candidate_logins.append(login)
            if len(candidate_logins) >= max(size * 3, 10):
                break

    enriched_rows = []
    for login in candidate_logins:
        if len(enriched_rows) >= size:
            break
        user_response = requests.get(
            f"https://api.github.com/users/{login}",
            headers=_github_headers(),
            timeout=12,
        )
        user = user_response.json() if user_response.status_code == 200 else {}
        if (user.get("type") or "").lower() != "user":
            continue

        repo_response = requests.get(
            f"https://api.github.com/users/{login}/repos",
            params={"sort": "updated", "per_page": 8},
            headers=_github_headers(),
            timeout=12,
        )
        repos = repo_response.json() if repo_response.status_code == 200 else []
        if not isinstance(repos, list):
            repos = []

        repo_skills = []
        for repo in repos:
            repo_skills.extend(_safe_list(repo.get("topics")))
            if repo.get("language"):
                repo_skills.append(repo.get("language"))

        repo_text = " ".join(
            [
                " ".join(
                    [
                        str(repo.get("name") or ""),
                        str(repo.get("description") or ""),
                        str(repo.get("language") or ""),
                        " ".join(_safe_list(repo.get("topics"))),
                    ]
                )
                for repo in repos
            ]
        )
        candidate_text = " ".join([str(user.get("name") or ""), str(user.get("login") or ""), str(user.get("bio") or ""), repo_text]).lower()
        inferred_skills = list(dict.fromkeys(
            [skill for skill in selected_skills if _text_mentions_skill(skill, candidate_text)]
            + repo_skills
        ))
        score, top_matches, score_details = _rank_external_skill_match(inferred_skills, selected_skills, selected_skills)

        enriched_rows.append({
            "source": "github",
            "source_label": "GitHub",
            "source_id": login,
            "name": user.get("name") or login,
            "email": user.get("email") or "",
            "title": "",
            "company": user.get("company") or "",
            "location": user.get("location") or "",
            "profile_url": user.get("html_url") or f"https://github.com/{login}",
            "avatar_url": user.get("avatar_url") or "",
            "summary": user.get("bio") or "",
            "skills": inferred_skills or selected_skills,
            "score": score,
            "match_band": score_details["band"],
            "score_details": score_details,
            "top_matches": top_matches,
            "repo_count": user.get("public_repos") or len(repos),
            "recent_repos": [
                {
                    "name": repo.get("name"),
                    "language": repo.get("language"),
                    "description": repo.get("description"),
                    "url": repo.get("html_url"),
                }
                for repo in repos[:5]
            ],
            "profile_data": {
                "github_login": login,
                "bio": user.get("bio") or "",
                "blog": user.get("blog") or "",
                "followers": user.get("followers") or 0,
                "public_repos": user.get("public_repos") or len(repos),
                "repos": [
                    {
                        "name": repo.get("name"),
                        "language": repo.get("language"),
                        "topics": _safe_list(repo.get("topics")),
                        "description": repo.get("description"),
                        "stars": repo.get("stargazers_count") or 0,
                        "url": repo.get("html_url"),
                    }
                    for repo in repos[:8]
                ],
            },
            "search_queries": search_queries,
        })

    enriched_rows.sort(key=lambda row: row["score"], reverse=True)
    return enriched_rows

def _get_job_skills(jd_id: str, domain: str = "dev"):
    domain = _domain_key(domain)
    jd = jobs.getJob(jd_id, domain)
    if not jd:
        raise HTTPException(status_code=400, detail="No job description found for this domain.")
    job_skills = list(dict.fromkeys(_safe_list(jd.get("skills"))))
    if not job_skills:
        job_skills = externalPeopleSearch.getPeopleSkills(jd.get("description") or "")
    return jd, job_skills

def _job_file_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in {".txt", ".md", ".text"}:
        return "text"
    if ext == ".doc":
        raise HTTPException(status_code=400, detail="Legacy .doc files are not supported. Please upload PDF, DOCX, or TXT.")
    raise HTTPException(status_code=400, detail="Unsupported job description file. Please upload PDF, DOCX, or TXT.")

async def _extract_job_file_text(file: UploadFile) -> str:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The uploaded job description file is empty.")

    file_type = _job_file_type(file.filename)
    try:
        if file_type in {"pdf", "docx"}:
            return ingest(file_type, raw).strip()
        return raw.decode("utf-8", errors="ignore").strip()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read job description file: {exc}")

@router.post("/createJob")
def jdCreate(company: str = Form(...), title: str = Form(...), jd_text: str = Form(...), domain: str = Form(default="dev")):
    domain = _domain_key(domain)
    print(f"Uploading {title} at {company}")
    try:
        # Deprecated
        # skills = normalize_jd(jd_text)
        flatSkills = normalize_all_skills(jd_text)

        # Get all skills from JD
        #for key, value in skills.items():
            #flatSkills.extend(value)

        flatSkills = list(set(flatSkills))  # unique skills

        created = jobs.uploadJob(company, title, domain, jd_text, flatSkills) or {}
        return {"jd_id": created.get("jd_id"), "company": company, "title": title, "domain": domain, "jd_skills": flatSkills, "jd_text": jd_text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": 'Failed to upload job description.', "trace": traceback.format_exc()})

@router.post("/uploadJob")
async def jdUpload(
    file: UploadFile = File(...),
    company: str = Form(...),
    title: str = Form(...),
    domain: str = Form(default="dev"),
):
    domain = _domain_key(domain)
    print(f"Uploading job description file {file.filename} for {title} at {company}")
    try:
        jd_text = await _extract_job_file_text(file)
        if not jd_text:
            raise HTTPException(status_code=400, detail="No readable job description text was found in that file.")

        flatSkills = list(set(normalize_all_skills(jd_text)))
        created = jobs.uploadJob(company, title, domain, jd_text, flatSkills) or {}
        return {
            "jd_id": created.get("jd_id"),
            "company": company,
            "title": title,
            "domain": domain,
            "jd_skills": flatSkills,
            "jd_text": jd_text,
            "source_file": file.filename,
        }
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Failed to upload job description file.", "trace": traceback.format_exc()})

@router.post("/updateJob/{jobId}")
def jdUpdate(jobId: str, company: str = Form(...), title: str = Form(...), jd_text: str = Form(...), domain: str = Form(default="dev")):
    domain = _domain_key(domain)
    print(f"Updating {title} at {company}")
    try:
        flatSkills = list(set(normalize_all_skills(jd_text)))
        updated = jobs.updateJob(jobId, company, title, domain, jd_text, flatSkills)
        if not updated.get("updated"):
            raise HTTPException(status_code=404, detail="Job not found for this domain.")
        return {
            "jd_id": updated.get("jd_id"),
            "company": company,
            "title": title,
            "domain": domain,
            "jd_skills": flatSkills,
            "jd_text": jd_text,
        }
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Failed to update job description.", "trace": traceback.format_exc()})

@router.get("/list/{domain}/{amount}")
def jd_list(domain: str = "dev", amount: int = 5):
    domain = _domain_key(domain)
    return jobs.listJobs(domain, amount)
    
@router.get("/list/search/{domain}/{query}/{amount}")
def jd_list(domain: str = "dev", query: str = '', amount: int = 5):
    domain = _domain_key(domain)
    return jobs.searchJobs(domain, query, amount)

@router.get("/getJob/{jobId}")
def jd_get(jobId: str, domain: str = "dev"):
    domain = _domain_key(domain)
    jd = jobs.getJob(jobId, domain)
    if not jd:
        raise HTTPException(status_code=404, detail="Job not found for this domain.")
    return jd

@router.delete("/deleteJob/{jobId}")
def jd_delete(jobId: str, domain: str = "dev"):
    domain = _domain_key(domain)
    try:
        deleted = jobs.deleteJob(jobId, domain)
        if not deleted.get("deleted"):
            raise HTTPException(status_code=404, detail="Job not found for this domain.")
        return deleted
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Failed to delete job description.", "trace": traceback.format_exc()})

@router.post("/match/run")
def run_match(domain: str = Form(default="dev"), jd_id: str = Form(None), top_k: int = Form(10), external_source: str = Form(default="none")):
    domain = _domain_key(domain)
    # TODO: Set up job descriptions in the database
    jd = jobs.getJob(jd_id, domain)

    if not jd:
        raise HTTPException(status_code=400, detail="No job description found for this domain.")
    
    peopleDataSkills = []
    if not jd["skills"]:
        peopleDataSkills = externalPeopleSearch.getPeopleSkills(jd["jd_text"])
        # TODO: Upload skills to database
    else:
        peopleDataSkills = jd["skills"]
    
    # TODO: Figure out why some jobs have duplicates on upload
    peopleDataSkills = list(set(peopleDataSkills))  # ensure unique skills
    scoringSkills = _searchable_job_skills(peopleDataSkills, 12)

    returnedExternalPeople = []

    # TODO: Get location search working
    print('No location extracted from JD. Running external search based on skills only.')
    try:
        if external_source == "pdl":
            returnedExternalPeople = peopleDataLabs.searchSkills(peopleDataSkills, 1)["data"]
        elif external_source == "github":
            pass  # TODO: Implement Github external search
        else:
            print('No external source selected or source not recognized. Skipping external search.')
    except Exception as e:
        print(f'Error during external people search: {e}')

    profiles = candidates.searchCandidatesBySkillId(jd["skillIds"], top_k, domain)

    ranked = []
    for row in profiles:
        #p = storage.get_profile(DB_PATH, row["profile_id"])
        #score, parts = match((p or {}).get("skills", {}), jd_skills)
        #score, parts = azureJobMatch(row['skillMatches'],peopleDataSkills)

        print(f"Matching profile {row['id']} - {row['firstName']} {row['lastName']}")

        score, top_matches, score_details = _rank_external_skill_match(row['skillMatches'], peopleDataSkills, scoringSkills)

        print(f"Total matched skills: {score_details['matched_count']} out of {score_details['required_count']}")
        print(f"Weighted match score: {score}")

        print("\n")
        # Set empty and negative values for easy existance checking
        personalityDifferences = []
        averageDifference = -1
        percentageNum = -1

        for personality in row.get('personality') or []:
            # Get the stat that matches the current one
            matchingStat = next((i for i in jd['personalities'] if i['title'] == personality['title']),None)
            if matchingStat and matchingStat.get('score') is not None and personality.get('score') is not None:
                personalityDifferences.append(abs(matchingStat['score']-personality['score']))

        if len(personalityDifferences)>0:
            averageDifference = sum(personalityDifferences)/len(personalityDifferences)
            # numbers closer to zero are better and scale is of 5, so take percentage out of five, then subtract from 1 to determine closeness to zero
            percentageNum = round((1-(averageDifference/5))*100)
        
        deterministic_reason = _deterministic_fit_reason(
            f"{row['firstName']} {row['lastName']}",
            score,
            top_matches,
            score_details,
            percentageNum,
        )

        ranked.append({
            "profile_id": row["id"],
            "name": row["firstName"] + ' ' + row["lastName"],
            "email": row["email"],
            "score": score,
            "match_band": score_details["band"],
            "score_details": score_details,
            "fit_decision": deterministic_reason["fit_decision"],
            "fit_reason": deterministic_reason["fit_reason"],
            "score_formula": deterministic_reason["score_formula"],
            "top_matches": top_matches,
            "breakdown": row['skillMatches'],
            'culture_match': percentageNum
        })

    ranked.sort(key=lambda x: (x["score"], x["culture_match"]), reverse=True)
    ai_reasons = _ai_fit_explanations(jd, scoringSkills, ranked[:top_k])
    for row in ranked[:top_k]:
        ai_reason = ai_reasons.get(str(row.get("profile_id")))
        if ai_reason:
            row["fit_decision"] = ai_reason.get("fit_decision") or row.get("fit_decision")
            row["fit_reason"] = ai_reason.get("fit_reason") or row.get("fit_reason")
            row["score_formula"] = ai_reason.get("score_formula") or row.get("score_formula")
            row["fit_reason_source"] = "ai"
        else:
            row["fit_reason_source"] = "deterministic"

    rankedExternal = []
    for row in returnedExternalPeople:
        #score, parts = azureJobMatch(row['skills'],peopleDataSkills)
        

        inferredSalary = None
        if "inferred_salary" in row:
            inferredSalary = row["inferred_salary"]

        score, top_matches, score_details = _rank_external_skill_match(row['skills'], peopleDataSkills, scoringSkills)
        
        deterministic_reason = _deterministic_fit_reason(
            f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
            score,
            top_matches,
            score_details,
            -1,
        )

        rankedExternal.append({
            "profile_id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "recommended_personal_email": row.get("work_email") or "",
            "linkedin_url": row["linkedin_url"],
            "inferred_salary": inferredSalary,
            "score": score,
            "match_band": score_details["band"],
            "score_details": score_details,
            "fit_decision": deterministic_reason["fit_decision"],
            "fit_reason": deterministic_reason["fit_reason"],
            "score_formula": deterministic_reason["score_formula"],
            "top_matches": top_matches,
            "breakdown": row['skills']
        })

    rankedExternal.sort(key=lambda x: x["score"], reverse=True)
    return {"jd": {"jd_id": jd["jd_id"], "company": jd.get("company",""), "title": jd.get("title",""), "created_at": jd.get("created_at","")}, "results": ranked[:top_k], "externalMatches": rankedExternal, "skillList": peopleDataSkills, "scoringSkills": scoringSkills}

@router.get("/external/providers")
def external_provider_status():
    return {
        "providers": {
            "pdl": {
                "label": "People Data Labs",
                "ready": bool(os.getenv("PDL_API_KEY", "").strip()),
                "role": "professional_discovery",
                "environmentVariable": "PDL_API_KEY",
                "signupUrl": "https://dashboard.peopledatalabs.com/",
                "usageUrl": "https://dashboard.peopledatalabs.com/",
            },
            "coresignal": {
                "label": "Coresignal",
                "ready": coreSignal.configured(),
                "role": "professional_discovery_comparison",
                "environmentVariable": "CORESIGNAL_API_KEY",
                "signupUrl": "https://dashboard.coresignal.com/sign-up",
            },
            "brave": {
                "label": "Brave Search",
                "ready": braveSearch.configured(),
                "role": "public_web_evidence",
                "environmentVariable": "BRAVE_SEARCH_API_KEY",
                "signupUrl": "https://api-dashboard.search.brave.com/app/keys",
            },
            "courtlistener": {
                "label": "CourtListener / RECAP",
                "ready": courtListener.configured(),
                "role": "jd_court_attorney_discovery_and_name_research",
                "environmentVariable": "COURTLISTENER_API_TOKEN",
                "signupUrl": "https://www.courtlistener.com/sign-in/",
                "tokenUrl": "https://www.courtlistener.com/profile/api-token/",
            },
            "github": {
                "label": "GitHub Public API",
                "ready": True,
                "role": "public_code_evidence",
            },
        },
        "secretsExposed": False,
    }


@router.get("/external/criteria/{jd_id}")
def external_candidate_criteria(jd_id: str, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    jd, _job_skills = _get_job_skills(jd_id, clean_domain)
    return {
        "domain": clean_domain,
        "jd": {
            "jd_id": jd.get("jd_id"),
            "company": jd.get("company", ""),
            "title": jd.get("title", ""),
        },
        "criteria": _lawyer_search_criteria(jd) if clean_domain == "law" else {},
        "verificationSources": {
            "californiaBar": "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch",
            "linkedIn": "profile_link_only",
        },
    }


@router.post("/external/legal-evidence")
def external_candidate_legal_evidence(payload: dict = Body(...)):
    if _domain_key(payload.get("domain") or "law") != "law":
        raise HTTPException(status_code=400, detail="Court evidence review is available in LegalReady.")
    name = str(payload.get("name") or "").strip()
    try:
        size = max(1, min(int(payload.get("size") or 3), 5))
    except (TypeError, ValueError):
        size = 3
    try:
        evidence = courtListener.search_evidence(name, size=size)
    except courtListener.CourtListenerError as exc:
        status_code = 503 if exc.status_code == 503 else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    evidence["searchedAt"] = datetime.now(timezone.utc).isoformat()
    evidence["usedForScoring"] = False
    return evidence


@router.post("/external/court-lead/validate-profile")
def external_court_lead_validate_profile(payload: dict = Body(...)):
    domain = _domain_key(payload.get("domain") or "law")
    if domain != "law":
        raise HTTPException(status_code=400, detail="Court-lead validation is available in LegalReady.")
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    if candidate.get("source") != "courtlistener" or candidate.get("result_type") != "court_attorney_lead":
        raise HTTPException(status_code=400, detail="Select a CourtListener lawyer lead first.")

    previous_validation = (
        candidate.get("profile_validation")
        if isinstance(candidate.get("profile_validation"), dict)
        else {}
    )
    if previous_validation.get("status") in {
        "confirmed_profile_match",
        "needs_review",
        "no_match",
    }:
        return {
            "candidate": candidate,
            "profileValidation": previous_validation,
            "reused": True,
            "usedForCandidateScoring": False,
            "linkedinScraped": False,
        }

    name = _external_text(candidate.get("name"), 160)
    if len(_person_name_tokens(name)) < 2:
        raise HTTPException(status_code=400, detail="A complete lawyer name is required for validation.")

    jd_id = _external_text(payload.get("jd_id"), 80)
    criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    job_skills: list[str] = []
    if jd_id:
        jd, job_skills = _get_job_skills(jd_id, domain)
        supplied_criteria = criteria
        criteria = _lawyer_search_criteria(
            jd,
            titles=",".join(_safe_list(supplied_criteria.get("titles"))),
            practice_areas=",".join(_safe_list(supplied_criteria.get("practiceAreas"))),
            locations=",".join(_safe_list(supplied_criteria.get("locations"))),
            region=_external_text(supplied_criteria.get("region"), 100),
            min_years=supplied_criteria.get("minYears") or 0,
            strict_locations=supplied_criteria.get("strictLocations"),
        )
    search_skills = _searchable_job_skills(job_skills, 12)
    region = _external_text(criteria.get("region"), 100) or "California"

    try:
        response = peopleDataLabs.enrichPerson(
            name=name,
            region=region,
            country="United States",
            min_likelihood=8,
            required="linkedin_url",
        )
    except peopleDataLabs.PeopleDataLabsError as exc:
        provider_status = int(exc.status_code or 502)
        status_code = provider_status if provider_status in {400, 401, 402, 403, 429, 503} else 502
        raise HTTPException(
            status_code=status_code,
            detail=f"Professional profile validation failed: {str(exc)}",
        ) from exc

    checked_at = datetime.now(timezone.utc).isoformat()
    if response.get("status") != 200 or not isinstance(response.get("data"), dict):
        validation = {
            "status": "no_match",
            "provider": "People Data Labs Person Enrichment",
            "checkedAt": checked_at,
            "searchedName": name,
            "likelihood": 0,
            "exactNameMatch": False,
            "profileUrl": "",
            "fieldsAdded": [],
            "requestsUsed": 1,
            "successfulEnrichmentCredits": 0,
            "notice": (
                "No LinkedIn-linked PDL profile met the exact-name and California lookup threshold. "
                "The court lead remains unchanged."
            ),
            "linkedinMode": "Provider dataset lookup only; LinkedIn was not scraped.",
        }
        return {
            "candidate": {**candidate, "profile_validation": validation},
            "profileValidation": validation,
            "reused": False,
            "usedForCandidateScoring": False,
            "linkedinScraped": False,
        }

    mapped = _people_data_row(response["data"], job_skills, search_skills, criteria)
    returned_name = _external_text(mapped.get("name"), 160)
    try:
        likelihood = max(0, min(int(response.get("likelihood") or 0), 10))
    except (TypeError, ValueError):
        likelihood = 0
    profile_url = _external_text(mapped.get("profile_url"), 500)
    exact_name_match = _person_names_align(name, returned_name)
    confirmed = exact_name_match and likelihood >= 8 and bool(profile_url)
    status = "confirmed_profile_match" if confirmed else "needs_review"
    fields_added = []
    enriched_candidate = {**candidate}

    if confirmed:
        field_values = {
            "professional_profile_url": profile_url,
            "title": _external_text(mapped.get("title"), 200),
            "company": _external_text(mapped.get("company"), 200),
            "location": _external_text(mapped.get("location"), 240),
            "summary": _external_text(mapped.get("summary"), 1600),
            "years_experience": mapped.get("years_experience") or 0,
            "job_last_verified": _external_text(mapped.get("job_last_verified"), 80),
        }
        for field, value in field_values.items():
            if value:
                enriched_candidate[field] = value
                fields_added.append(field)
        if mapped.get("skills"):
            enriched_candidate["skills"] = _safe_list(mapped.get("skills"))[:30]
            fields_added.append("skills")
        original_profile_data = (
            candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
        )
        mapped_profile_data = (
            mapped.get("profile_data") if isinstance(mapped.get("profile_data"), dict) else {}
        )
        enriched_candidate["profile_data"] = {
            **original_profile_data,
            **mapped_profile_data,
            "pdl_person_id": _external_text(mapped.get("source_id"), 180),
        }
        enriched_candidate["source_label"] = "CourtListener / RECAP + People Data Labs"
        enriched_candidate["match_band"] = "Court-data lead + likely profile match"
        enriched_candidate["score"] = 0
        enriched_candidate["verification"] = {
            **(
                candidate.get("verification")
                if isinstance(candidate.get("verification"), dict)
                else {}
            ),
            "professional_profile_status": "likely_match",
            "pdl_identity_likelihood": likelihood,
            "linkedin_url_source": "People Data Labs",
            "linkedin_scan": "not_performed",
        }

    matched = response.get("matched") if isinstance(response.get("matched"), dict) else {}
    validation = {
        "status": status,
        "provider": "People Data Labs Person Enrichment",
        "checkedAt": checked_at,
        "searchedName": name,
        "returnedName": returned_name,
        "likelihood": likelihood,
        "likelihoodThreshold": 8,
        "exactNameMatch": exact_name_match,
        "profileUrl": profile_url,
        "fieldsAdded": fields_added,
        "matchedFields": sorted(str(key) for key in matched.keys())[:12],
        "requestsUsed": 1,
        "successfulEnrichmentCredits": 1,
        "notice": (
            "A likely LinkedIn-linked professional profile was found and selected provider fields were added. "
            "Current employment, JD fit, California Bar standing, and interest still require verification."
            if confirmed
            else "PDL returned a possible profile, but it did not meet the exact-name, profile-link, and likelihood threshold. No provider fields were merged."
        ),
        "linkedinMode": "Provider dataset lookup only; LinkedIn was not scraped.",
    }
    enriched_candidate["profile_validation"] = validation
    return {
        "candidate": enriched_candidate,
        "profileValidation": validation,
        "reused": False,
        "usedForCandidateScoring": False,
        "linkedinScraped": False,
    }


@router.post("/external/search")
def external_candidate_search(
    domain: str = Form(default="dev"),
    jd_id: str = Form(...),
    source: str = Form(default="pdl"),
    top_k: int = Form(default=10),
    titles: str = Form(default=""),
    practice_areas: str = Form(default=""),
    locations: str = Form(default=""),
    region: str = Form(default=""),
    min_years: int = Form(default=0),
    strict_locations: bool | None = Form(default=None),
    scroll_token: str = Form(default=""),
):
    domain = _domain_key(domain)
    top_k = _external_result_limit(top_k)
    jd, job_skills = _get_job_skills(jd_id, domain)
    search_skills = _searchable_job_skills(job_skills, 12)
    selected_source = (source or "pdl").strip().lower()
    results = []
    criteria = (
        _lawyer_search_criteria(
            jd,
            titles=titles,
            practice_areas=practice_areas,
            locations=locations,
            region=region,
            min_years=min_years,
            strict_locations=strict_locations,
        )
        if domain == "law"
        else None
    )
    source_audit = {}
    pagination = {
        "pageSize": top_k,
        "hasNext": False,
        "nextScrollToken": "",
        "costLabel": "no provider charge",
    }

    try:
        if selected_source == "pdl":
            if domain == "law":
                pdl_response = peopleDataLabs.searchLawyers(
                    titles=criteria["titles"],
                    practice_areas=criteria["requiredPracticeAreas"],
                    locations=criteria["locations"],
                    region=criteria["region"],
                    min_years=criteria["minYears"],
                    strict_locations=criteria["strictLocations"],
                    size=top_k,
                    scroll_token=scroll_token,
                )
                results = [
                    _people_data_row(row, job_skills, search_skills, criteria)
                    for row in pdl_response.get("data", [])
                ]
                source_audit = _pdl_source_audit(pdl_response, criteria, "lawyer")
                pagination = _pdl_pagination(pdl_response, top_k)
            else:
                pdl_response = peopleDataLabs.searchSkills(search_skills, top_k, scroll_token=scroll_token)
                results = [_people_data_row(row, job_skills, search_skills) for row in pdl_response.get("data", [])]
                source_audit = _pdl_source_audit(pdl_response, {"skills": search_skills}, "skills")
                pagination = _pdl_pagination(pdl_response, top_k)
        elif selected_source == "coresignal":
            page = _provider_page(scroll_token, 1, 100)
            core_response = coreSignal.search_people(
                titles=criteria["titles"] if criteria else search_skills,
                practice_areas=criteria["requiredPracticeAreas"] if criteria else search_skills,
                locations=criteria["locations"] if criteria else [],
                region=criteria["region"] if criteria else "",
                size=top_k,
                page=page,
            )
            results = [
                _coresignal_row(row, job_skills, search_skills, criteria)
                for row in core_response.get("data", [])
            ]
            source_audit = _provider_source_audit(
                "Coresignal",
                core_response,
                criteria or {"skills": search_skills},
                "employee_profile_preview",
                "search credits",
            )
            pagination = _provider_pagination(core_response, top_k, "1 search credit")
        elif selected_source == "brave":
            page = _provider_page(scroll_token, 0, 9)
            search_query = (
                _brave_law_query(criteria)
                if criteria
                else " ".join(search_skills[:8]) + " professional profile -site:linkedin.com"
            )
            brave_response = braveSearch.search_web(search_query, size=top_k, page=page)
            results = [
                _brave_row(row, job_skills, search_skills, criteria)
                for row in brave_response.get("data", [])
            ]
            source_audit = _provider_source_audit(
                "Brave Search",
                brave_response,
                criteria or {"skills": search_skills},
                "public_web_legal_evidence",
                "API requests",
            )
            source_audit["totalIsEstimate"] = True
            pagination = _provider_pagination(brave_response, top_k, "1 API request")
        elif selected_source == "courtlistener":
            if domain != "law" or not criteria:
                raise HTTPException(
                    status_code=400,
                    detail="CourtListener JD discovery is available in LegalReady with a selected job description.",
                )
            court_response = courtListener.search_attorneys_by_criteria(criteria, size=top_k)
            results = [
                _courtlistener_attorney_row(row, criteria)
                for row in court_response.get("results", [])
                if isinstance(row, dict)
            ][:top_k]
            requests_used = max(1, int(court_response.get("requestsUsed") or 1))
            attorneys_discovered = max(
                len(results), int(court_response.get("attorneysDiscovered") or 0)
            )
            source_audit = {
                "provider": "CourtListener / RECAP",
                "queryExecuted": bool(court_response.get("queryExecuted", True)),
                "queryCompleted": True,
                "queryMode": "jd_court_attorney_discovery",
                "totalMatches": attorneys_discovered,
                "recordsReturned": len(results),
                "recordsReviewed": len(results),
                "matchingDockets": int(court_response.get("matchingDockets") or 0),
                "docketsReviewed": int(court_response.get("docketsReviewed") or 0),
                "estimatedCreditsUsed": requests_used,
                "costLabel": "API requests (rate limited)",
                "executedAt": datetime.now(timezone.utc).isoformat(),
                "criteria": criteria,
                "courtIds": court_response.get("courtIds") or [],
                "practiceTerms": court_response.get("practiceTerms") or [],
                "providerCountIsEstimate": bool(court_response.get("countIsEstimate")),
                "identityVerified": False,
                "legalReadiness": court_response.get("notice") or "Verify every court-data lead.",
                "linkedInMode": "No LinkedIn data was requested or scanned.",
                "statusMessage": (
                    "Lawyer names were discovered from matching court dockets; "
                    "court association is not proof of current candidate fit."
                ),
            }
            pagination = {
                "pageSize": len(results),
                "hasNext": False,
                "nextScrollToken": "",
                "costLabel": f"{requests_used} API request{'s' if requests_used != 1 else ''}",
            }
        elif selected_source == "github":
            results = _github_search(job_skills, search_skills, top_k)
            source_audit = {
                "provider": "GitHub Public API",
                "queryExecuted": True,
                "queryMode": "public_code_evidence",
                "totalMatches": len(results),
                "recordsReturned": len(results),
                "recordsReviewed": len(results),
                "executedAt": datetime.now(timezone.utc).isoformat(),
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Select People Data Labs, Coresignal, Brave Search, CourtListener, or GitHub.",
            )
    except HTTPException:
        raise
    except Exception as e:
        return _provider_search_error_response(
            selected_source,
            e,
            top_k,
            criteria=criteria,
            extra={"jobSkills": job_skills},
        )

    results.sort(key=lambda row: row.get("score", 0), reverse=True)
    return {
        "jd": {
            "jd_id": jd["jd_id"],
            "company": jd.get("company", ""),
            "title": jd.get("title", ""),
        },
        "source": selected_source,
        "jobSkills": job_skills,
        "searchSkills": search_skills,
        "results": results[:top_k],
        "searchUsesJobDescription": True,
        "criteria": criteria or {},
        "sourceAudit": source_audit,
        "pagination": pagination,
    }

@router.post("/external/search-direct")
def external_candidate_search_direct(
    domain: str = Form(default="dev"),
    query: str = Form(...),
    source: str = Form(default="pdl"),
    top_k: int = Form(default=10),
    scroll_token: str = Form(default=""),
):
    domain = _domain_key(domain)
    top_k = _external_result_limit(top_k)
    clean_query = (query or "").strip()
    if not clean_query:
        raise HTTPException(status_code=400, detail="Enter a name, email, profile URL, or comma-separated skills.")

    search_terms = [
        term.strip()
        for term in re.split(r"[,;\n]+", clean_query)
        if term.strip()
    ]
    if not search_terms:
        search_terms = [clean_query]

    selected_source = (source or "pdl").strip().lower()
    source_audit = {}
    pagination = {
        "pageSize": top_k,
        "hasNext": False,
        "nextScrollToken": "",
        "costLabel": "no provider charge",
    }
    try:
        if selected_source == "pdl":
            pdl_response = peopleDataLabs.searchDirect(clean_query, top_k, scroll_token=scroll_token)
            results = [_people_data_row(row, search_terms, search_terms) for row in pdl_response.get("data", [])]
            source_audit = _pdl_source_audit(pdl_response, {"terms": search_terms}, "direct")
            pagination = _pdl_pagination(pdl_response, top_k)
        elif selected_source == "coresignal":
            page = _provider_page(scroll_token, 1, 100)
            core_response = coreSignal.search_people(
                titles=[],
                practice_areas=[],
                locations=[],
                size=top_k,
                page=page,
                direct_query=clean_query,
            )
            results = [
                _coresignal_row(row, search_terms, search_terms)
                for row in core_response.get("data", [])
            ]
            source_audit = _provider_source_audit(
                "Coresignal",
                core_response,
                {"terms": search_terms},
                "direct_profile_preview",
                "search credits",
            )
            pagination = _provider_pagination(core_response, top_k, "1 search credit")
        elif selected_source == "brave":
            page = _provider_page(scroll_token, 0, 9)
            brave_query = _brave_direct_query(clean_query)
            brave_response = braveSearch.search_web(brave_query, size=top_k, page=page)
            results = [
                _brave_row(row, search_terms, search_terms)
                for row in brave_response.get("data", [])
            ]
            source_audit = _provider_source_audit(
                "Brave Search",
                brave_response,
                {"terms": search_terms},
                "direct_public_web",
                "API requests",
            )
            source_audit["totalIsEstimate"] = True
            pagination = _provider_pagination(brave_response, top_k, "1 API request")
        elif selected_source == "courtlistener":
            if domain != "law":
                raise HTTPException(status_code=400, detail="CourtListener lawyer research is available in LegalReady.")
            court_response = courtListener.search_evidence(clean_query, size=min(top_k, 5))
            results = [
                _courtlistener_row(row, clean_query)
                for row in court_response.get("results", [])
                if isinstance(row, dict)
            ][:top_k]
            total_matches = sum(
                max(0, int(value or 0))
                for value in (court_response.get("counts") or {}).values()
            )
            requests_used = max(1, int(court_response.get("requestsUsed") or 1))
            source_audit = {
                "provider": "CourtListener / RECAP",
                "queryExecuted": bool(court_response.get("queryExecuted", True)),
                "queryCompleted": True,
                "queryMode": "lawyer_name_court_record_research",
                "totalMatches": total_matches,
                "recordsReturned": len(results),
                "recordsReviewed": len(results),
                "estimatedCreditsUsed": requests_used,
                "costLabel": "API requests (rate limited)",
                "executedAt": datetime.now(timezone.utc).isoformat(),
                "criteria": {"lawyerName": clean_query},
                "identityVerified": False,
                "legalReadiness": court_response.get("notice") or "Confirm identity and role in every matter.",
                "linkedInMode": "No LinkedIn data was requested or scanned.",
                "statusMessage": "Court records are research leads only and are not candidate profiles.",
            }
            pagination = {
                "pageSize": len(results),
                "hasNext": False,
                "nextScrollToken": "",
                "costLabel": f"{requests_used} API requests",
            }
        elif selected_source == "github":
            results = _github_direct_search(clean_query, search_terms, top_k)
            source_audit = {
                "provider": "GitHub Public API",
                "queryExecuted": True,
                "queryMode": "direct",
                "totalMatches": len(results),
                "recordsReturned": len(results),
                "recordsReviewed": len(results),
                "executedAt": datetime.now(timezone.utc).isoformat(),
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Select People Data Labs, Coresignal, Brave Search, CourtListener, or GitHub.",
            )
    except HTTPException:
        raise
    except Exception as e:
        return _provider_search_error_response(
            selected_source,
            e,
            top_k,
            criteria={"terms": search_terms},
            extra={"searchTerms": search_terms},
        )

    for row in results:
        row["search_mode"] = "direct"

    results.sort(key=lambda row: row.get("score", 0), reverse=True)
    return {
        "source": selected_source,
        "jobSkills": search_terms,
        "searchSkills": search_terms,
        "results": results[:top_k],
        "searchUsesJobDescription": False,
        "sourceAudit": source_audit,
        "pagination": pagination,
    }

def _external_text(value, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _external_year(value):
    match = re.search(r"\b(19\d{2}|20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _external_candidate_skills(candidate: dict, limit: int = 30) -> list[str]:
    ordered = _safe_list(candidate.get("top_matches")) + _safe_list(candidate.get("skills"))
    clean = []
    seen = set()
    for value in ordered:
        skill = _external_text(value, 100)
        key = skill.lower()
        if len(skill) < 2 or key in seen:
            continue
        seen.add(key)
        clean.append(skill)
        if len(clean) >= limit:
            break
    return clean


def _external_portfolio(candidate: dict) -> list[dict]:
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    experiences = profile_data.get("experience") if isinstance(profile_data.get("experience"), list) else []
    candidate_skills = _external_candidate_skills(candidate)
    portfolio = []
    seen = set()
    for experience in experiences[:8]:
        if not isinstance(experience, dict):
            continue
        company = experience.get("company") if isinstance(experience.get("company"), dict) else {}
        company_name = _external_text(company.get("name") or experience.get("company_name"), 180)
        role = _external_text(experience.get("title") or experience.get("job_title"), 180)
        summary = _external_text(experience.get("summary"), 1600)
        start_year = _external_year(experience.get("start_date"))
        finish_year = _external_year(experience.get("end_date"))
        dedupe_key = (company_name.lower(), role.lower(), start_year, finish_year)
        if dedupe_key in seen or not any([company_name, role, summary]):
            continue
        seen.add(dedupe_key)
        evidence_text = f"{role} {summary}".lower()
        role_skills = [skill for skill in candidate_skills if skill.lower() in evidence_text][:6]
        locations = experience.get("location_names") if isinstance(experience.get("location_names"), list) else []
        portfolio.append(
            {
                "companyName": company_name or "Organization not reported",
                "mainRole": role or "Role not reported",
                "description": summary or "No role summary was provided by the source.",
                "startDate": start_year,
                "finishDate": finish_year,
                "isPresent": bool(experience.get("is_primary")) or not bool(finish_year),
                "skills": role_skills,
                "features": [_external_text(location, 100) for location in locations[:2] if _external_text(location, 100)],
            }
        )
    return portfolio


def _external_education(candidate: dict) -> list[dict]:
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    education = profile_data.get("education") if isinstance(profile_data.get("education"), list) else []
    rows = []
    for item in education[:5]:
        if not isinstance(item, dict):
            continue
        school = item.get("school") if isinstance(item.get("school"), dict) else {}
        degrees = item.get("degrees") if isinstance(item.get("degrees"), list) else [item.get("degrees")]
        majors = item.get("majors") if isinstance(item.get("majors"), list) else [item.get("majors")]
        row = {
            "school": _external_text(school.get("name") or item.get("school_name"), 180),
            "degrees": [_external_text(value, 120) for value in degrees if _external_text(value, 120)],
            "majors": [_external_text(value, 120) for value in majors if _external_text(value, 120)],
            "startYear": _external_year(item.get("start_date")),
            "endYear": _external_year(item.get("end_date")),
        }
        if row["school"] or row["degrees"] or row["majors"]:
            rows.append(row)
    return rows


def _external_certifications(candidate: dict) -> list[str]:
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    certifications = profile_data.get("certifications") if isinstance(profile_data.get("certifications"), list) else []
    clean = []
    for item in certifications[:10]:
        value = (item.get("name") or item.get("title")) if isinstance(item, dict) else item
        title = _external_text(value, 180)
        if title and title.lower() not in {row.lower() for row in clean}:
            clean.append(title)
    return clean


def _external_court_evidence(candidate: dict) -> dict:
    if candidate.get("source") != "courtlistener" or candidate.get("result_type") != "court_attorney_lead":
        return {}
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    records = []
    for item in profile_data.get("evidence_records", [])[:8]:
        if not isinstance(item, dict):
            continue
        record = {
            "title": _external_text(item.get("title"), 240),
            "court": _external_text(item.get("court"), 180),
            "docketNumber": _external_text(item.get("docketNumber"), 100),
            "dateFiled": _external_text(item.get("dateFiled"), 40),
            "url": _external_text(item.get("url"), 500),
        }
        if any(record.values()):
            records.append(record)
    try:
        evidence_count = max(int(profile_data.get("evidence_count") or len(records)), len(records))
    except (TypeError, ValueError):
        evidence_count = len(records)
    return {
        "identityStatus": "not_verified",
        "associationStatus": "listed_on_matching_docket_not_individually_confirmed",
        "evidenceCount": evidence_count,
        "matchedPracticeAreas": _safe_list(profile_data.get("matched_practice_areas"))[:12],
        "queryPracticeAreas": _safe_list(profile_data.get("query_practice_areas"))[:12],
        "courts": _safe_list(profile_data.get("courts"))[:8],
        "records": records,
    }


def _external_profile_metadata(candidate: dict, source: str, enrichment: dict) -> dict:
    score_details = candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {}
    is_court_lead = source == "courtlistener" and candidate.get("result_type") == "court_attorney_lead"
    profile_validation = (
        candidate.get("profile_validation")
        if isinstance(candidate.get("profile_validation"), dict)
        else {}
    )
    verification = candidate.get("verification") if isinstance(candidate.get("verification"), dict) else {}
    try:
        match_score = round(float(candidate.get("score") or 0), 1)
    except (TypeError, ValueError):
        match_score = 0
    metadata = {
        "version": 2 if is_court_lead else 1,
        "source": _external_text(candidate.get("source_label") or source, 120),
        "sourceId": _external_text(candidate.get("source_id"), 180),
        "profileUrl": _external_text(candidate.get("profile_url"), 500),
        "recordType": _external_text(candidate.get("result_type"), 80),
        "enrichment": enrichment,
        "match": {
            "score": match_score,
            "band": _external_text(candidate.get("match_band") or score_details.get("band"), 80),
            "formula": _external_text(score_details.get("formula"), 300),
            "matched": [] if is_court_lead else _safe_list(candidate.get("top_matches"))[:12],
            "missing": _safe_list(score_details.get("missing"))[:10],
            "components": score_details.get("components") if isinstance(score_details.get("components"), dict) else {},
        },
        "education": _external_education(candidate),
        "certifications": _external_certifications(candidate),
        "providerSkills": _safe_list(candidate.get("skills"))[:30] if is_court_lead else _external_candidate_skills(candidate),
        "yearsExperience": candidate.get("years_experience") or 0,
        "lastVerified": _external_text(candidate.get("job_last_verified"), 80),
    }
    if is_court_lead:
        metadata["verification"] = {
            "identityStatus": _external_text(verification.get("identity_status") or "not_verified", 80),
            "californiaBarStatus": _external_text(verification.get("california_bar_status") or "not_verified", 80),
            "currentEmployment": _external_text(verification.get("current_employment") or "not_verified", 80),
            "roleInMatter": _external_text(verification.get("role_in_matter"), 120),
        }
        metadata["profileValidation"] = {
            "status": _external_text(profile_validation.get("status") or "not_run", 80),
            "provider": _external_text(profile_validation.get("provider"), 120),
            "checkedAt": _external_text(profile_validation.get("checkedAt"), 80),
            "notice": _external_text(profile_validation.get("notice"), 600),
            "profileUrl": _external_text(profile_validation.get("profileUrl"), 500),
        }
        metadata["courtEvidence"] = _external_court_evidence(candidate)
    return metadata


@router.post("/external/import")
def external_candidate_import(payload: dict = Body(...)):
    domain = _domain_key(payload.get("domain") or "dev")
    candidate = payload.get("candidate") or {}
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=400, detail="Candidate data is required.")
    source = candidate.get("source") or payload.get("source") or "external"
    result_type = candidate.get("result_type")
    is_court_lead = source == "courtlistener" and result_type == "court_attorney_lead"
    if source == "brave" or result_type in {
        "public_web_evidence",
        "court_record_evidence",
    }:
        raise HTTPException(
            status_code=400,
            detail="These research-only evidence results are not stored as candidate profiles.",
        )
    if source == "courtlistener" and not is_court_lead:
        raise HTTPException(status_code=400, detail="Select a CourtListener attorney lead, not an individual court record.")
    if is_court_lead:
        if domain != "law":
            raise HTTPException(status_code=400, detail="CourtListener research profiles are available only in LegalReady.")
        if payload.get("identity_unverified_acknowledged") is not True:
            raise HTTPException(
                status_code=400,
                detail="Confirm that identity, current employment, experience, location, and California Bar standing are unverified.",
            )
        if len(_person_name_tokens(candidate.get("name"))) < 2:
            raise HTTPException(status_code=400, detail="A complete lawyer name is required for a CourtListener TEMP profile.")

    source_id = _external_text(candidate.get("source_id"), 180)
    profile_url = _external_text(candidate.get("profile_url"), 500)
    # Multiple lawyer names can share the same supporting docket. Court leads are
    # therefore deduplicated by their generated person source id, not the docket URL.
    duplicate = candidates.findTemporaryExternalProfile(domain, source_id, "" if is_court_lead else profile_url)
    if duplicate:
        duplicate.update({"source": source, "enrichmentSkipped": True})
        return duplicate

    jd_id = _external_text(payload.get("jd_id"), 80)
    job_skills = _safe_list(candidate.get("top_matches") or candidate.get("skills"))
    search_skills = list(job_skills)
    criteria = None
    if jd_id:
        jd, job_skills = _get_job_skills(jd_id, domain)
        search_skills = _searchable_job_skills(job_skills, 12)
        supplied_criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
        if domain == "law":
            criteria = _lawyer_search_criteria(
                jd,
                titles=",".join(_safe_list(supplied_criteria.get("titles"))),
                practice_areas=",".join(_safe_list(supplied_criteria.get("practiceAreas"))),
                locations=",".join(_safe_list(supplied_criteria.get("locations"))),
                region=_external_text(supplied_criteria.get("region"), 100),
                min_years=supplied_criteria.get("minYears") or 0,
                strict_locations=supplied_criteria.get("strictLocations"),
            )

    enrichment = {
        "status": "not_requested",
        "provider": "",
        "likelihood": 0,
        "creditsUsed": 0,
        "enrichedAt": "",
        "dataOrigin": "Provider-supplied professional data; no LinkedIn scraping.",
    }
    if is_court_lead:
        profile_validation = (
            candidate.get("profile_validation")
            if isinstance(candidate.get("profile_validation"), dict)
            else {}
        )
        try:
            successful_enrichment_credits = int(profile_validation.get("successfulEnrichmentCredits") or 0)
        except (TypeError, ValueError):
            successful_enrichment_credits = 0
        enrichment.update(
            {
                "status": _external_text(profile_validation.get("status") or "not_run", 80),
                "provider": _external_text(profile_validation.get("provider") or "CourtListener / RECAP", 120),
                "creditsUsed": successful_enrichment_credits,
                "enrichedAt": _external_text(profile_validation.get("checkedAt"), 80),
                "dataOrigin": "CourtListener docket research; identity and professional qualifications are not verified.",
            }
        )
    if source == "pdl":
        try:
            response = peopleDataLabs.enrichPerson(profile=profile_url, pdl_id=source_id)
        except peopleDataLabs.PeopleDataLabsError as exc:
            raise HTTPException(status_code=502, detail=f"Selected profile enrichment failed: {str(exc)}") from exc
        enrichment.update(
            {
                "provider": "People Data Labs Person Enrichment",
                "enrichedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        if response.get("status") == 200 and isinstance(response.get("data"), dict):
            mapped = _people_data_row(response["data"], job_skills, search_skills, criteria)
            merged_profile_data = {
                **(candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}),
                **(mapped.get("profile_data") if isinstance(mapped.get("profile_data"), dict) else {}),
            }
            candidate = {**candidate, **mapped, "profile_data": merged_profile_data}
            candidate["source"] = "pdl"
            candidate["source_label"] = "People Data Labs"
            enrichment.update(
                {
                    "status": "completed",
                    "likelihood": int(response.get("likelihood") or 0),
                    "creditsUsed": 1,
                    "matchedInput": "pdl_id" if source_id else "profile_url",
                }
            )
        else:
            raise HTTPException(
                status_code=404,
                detail="People Data Labs could not enrich the selected person. No TEMP profile was created.",
            )

    imported_skills = _safe_list(candidate.get("skills")) if is_court_lead else _external_candidate_skills(candidate)
    skills = [{"title": skill, "years": 1} for skill in imported_skills]
    full_name = candidate.get("name") or "External Candidate"
    profile_url = candidate.get("profile_url") or profile_url
    metadata = _external_profile_metadata(candidate, source, enrichment)
    summary = _external_text(
        candidate.get("summary")
        or ((candidate.get("profile_data") or {}).get("job_summary") if isinstance(candidate.get("profile_data"), dict) else ""),
        2400,
    )
    summary_parts = [
        "Temporary external profile. Confirm details before publishing.",
        f"Imported from {candidate.get('source_label') or source}.",
        summary,
    ]
    if is_court_lead:
        court_evidence = metadata.get("courtEvidence") or {}
        summary_parts.insert(
            1,
            "Court-record research profile. Identity, current employer, current location, years of experience, and California Bar standing are not verified.",
        )
        if court_evidence.get("evidenceCount"):
            summary_parts.append(
                f"Court evidence: listed in {court_evidence['evidenceCount']} matching docket record"
                f"{'s' if court_evidence['evidenceCount'] != 1 else ''}; this association is not candidate-fit scoring."
            )
    if source == "github":
        repos = ((candidate.get("profile_data") or {}).get("repos") or [])[:5]
        repo_lines = [
            f"{repo.get('name')}: {repo.get('language') or 'Unknown'} - {repo.get('description') or ''}".strip()
            for repo in repos
        ]
        if repo_lines:
            summary_parts.append("GitHub evidence:\n" + "\n".join(repo_lines))
    description = candidates.attachExternalProfileMetadata(
        "\n\n".join([part for part in summary_parts if part]),
        metadata,
    )
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    location_data = profile_data.get("location") if isinstance(profile_data.get("location"), dict) else {}
    linked_profile_url = (
        _external_text(candidate.get("professional_profile_url"), 500)
        if is_court_lead
        else profile_url
    )
    profile_validation = (
        candidate.get("profile_validation")
        if isinstance(candidate.get("profile_validation"), dict)
        else {}
    )
    candidate_title = (
        "Court-record attorney lead - identity unverified"
        if is_court_lead and profile_validation.get("status") != "confirmed_profile_match"
        else candidate.get("title") or ""
    )

    try:
        created = candidates.uploadProfile(
            skills=skills,
            fullName=full_name,
            candidateDescription=description,
            domain=domain,
            email=candidate.get("email") or None,
            linkedInUrl=linked_profile_url or None,
            candidateCity=location_data.get("locality") or None,
            candidateState=location_data.get("region") or None,
            candidateCountry=location_data.get("country") or (None if is_court_lead else candidate.get("location") or None),
            candidateTitle=candidate_title,
            portfolioExperiences=_external_portfolio(candidate),
        )
        created["source"] = source
        created["temporaryProfile"] = True
        created["importedSkills"] = [skill["title"] for skill in skills]
        created["enrichment"] = enrichment
        created["match"] = metadata["match"]
        created["identityUnverified"] = is_court_lead
        created["courtEvidence"] = metadata.get("courtEvidence") or {}
        created["enriched_candidate"] = candidate
        return created
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Unable to create profile from external candidate: {str(e)}"},
        )

@router.get("/external/temp")
def external_candidate_temp_profiles(domain: str = "dev", limit: int = 50):
    domain = _domain_key(domain)
    return candidates.listTemporaryExternalProfiles(domain, limit)

@router.post("/external/temp/{person_id}/make-permanent")
def external_candidate_make_permanent(person_id: str):
    return candidates.makeTemporaryExternalProfilePermanent(person_id)

@router.delete("/external/temp/{person_id}")
def external_candidate_delete(person_id: str):
    return candidates.deleteTemporaryExternalProfile(person_id)
