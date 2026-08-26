from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from datetime import datetime, timezone
from html import unescape
import traceback
import os
import re
import json
import requests
from urllib.parse import quote_plus, urlparse
from openai import OpenAI
from azureUtils.storage import jobs, candidates, externalSearchHistory
from azureUtils import externalSearchReport, linkedinResultsExport
from jd_match import normalize_jd, azureJobMatch, normalize_all_skills
from openAI import externalPeopleSearch
import peopleDataLabs.peopleSearch as peopleDataLabs
from legalSources import coreSignal, courtListener
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
    value = re.sub(r"[\s_-]+", " ", (domain or "dev").strip().lower())
    if value in {"technology", "tech", "devready", "dev"}:
        return "dev"
    if value in {"engineer", "engineering", "build", "buildready"}:
        return "engineer"
    if value in {"law", "legal", "legalready"}:
        return "law"
    if value in {
        "dental",
        "dentalready",
        "dental ready",
        "dental assistant",
        "dental assistants",
        "dental hygiene",
        "dental hygienist",
        "hygienist",
    }:
        return "dental"
    return "dev"


def _external_source_allowed_for_domain(source: str, domain: str) -> bool:
    clean_source = (source or "").strip().lower()
    clean_domain = _domain_key(domain)
    allowed = {
        "dental": {"pdl", "coresignal"},
        "law": {"pdl", "coresignal", "courtlistener"},
        "dev": {"pdl", "coresignal", "github"},
        "engineer": {"pdl", "coresignal", "github"},
    }
    return clean_source in allowed.get(clean_domain, allowed["dev"])


def _assert_external_source_allowed(source: str, domain: str):
    if _external_source_allowed_for_domain(source, domain):
        return
    clean_domain = _domain_key(domain)
    raise HTTPException(
        status_code=400,
        detail=f"{_provider_label(source)} is not available for the {clean_domain} domain.",
    )

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
LAWYER_WORK_ARRANGEMENTS = {"remote", "onsite", "hybrid"}
LAWYER_WORKFORCE_LOCATIONS = {"onshore", "offshore", "either"}
US_STATE_NAMES_BY_ABBR = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
LAWYER_LICENSING_DIRECTORY_URL = (
    "https://www.americanbar.org/groups/legal_services/flh-home/flh-lawyer-licensing/"
)


def _lawyer_license_verification(jurisdiction: str, candidate_name: str = "") -> dict:
    clean_jurisdiction = str(jurisdiction or "").strip() or "Target jurisdiction"
    if clean_jurisdiction.lower() == "california":
        return {
            "status": "not_verified",
            "jurisdiction": clean_jurisdiction,
            "url": (
                "https://apps.calbar.ca.gov/attorney/LicenseeSearch/QuickSearch?FreeText="
                + quote_plus(candidate_name)
            ),
            "source": "California lawyer-licensing authority",
        }
    return {
        "status": "not_verified",
        "jurisdiction": clean_jurisdiction,
        "url": LAWYER_LICENSING_DIRECTORY_URL,
        "source": f"Official {clean_jurisdiction} lawyer-licensing authority",
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
    required_skills=None,
    locations=None,
    region: str = "",
    min_years: int = 0,
    strict_locations: bool | None = None,
    work_arrangement: str = "",
    workforce_location: str = "",
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
    required_practice = _split_external_terms(required_skills, 12)
    if not required_practice:
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
    if not resolved_region:
        for abbreviation, state_name in US_STATE_NAMES_BY_ABBR.items():
            if state_name.lower() in lower or re.search(rf",\s*{abbreviation}\b", combined, re.IGNORECASE):
                resolved_region = state_name
                break
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

    resolved_work_arrangement = str(work_arrangement or "").strip().lower()
    if resolved_work_arrangement not in LAWYER_WORK_ARRANGEMENTS:
        has_remote = bool(re.search(r"\bremote\b", lower))
        has_onsite = bool(re.search(r"\b(?:on[ -]?site|in[ -]?office)\b", lower))
        if "hybrid" in lower or "combination" in lower or (has_remote and has_onsite):
            resolved_work_arrangement = "hybrid"
        elif has_remote:
            resolved_work_arrangement = "remote"
        elif has_onsite:
            resolved_work_arrangement = "onsite"
        else:
            resolved_work_arrangement = ""

    resolved_workforce_location = str(workforce_location or "").strip().lower()
    if resolved_workforce_location not in LAWYER_WORKFORCE_LOCATIONS:
        mentions_onshore = bool(re.search(r"\bon[ -]?shore\b", lower))
        mentions_offshore = bool(re.search(r"\boff[ -]?shore\b", lower))
        if mentions_onshore and mentions_offshore:
            resolved_workforce_location = "either"
        elif mentions_offshore:
            resolved_workforce_location = "offshore"
        else:
            resolved_workforce_location = "onshore"

    return {
        "policyVersion": 2,
        "titles": resolved_titles,
        "practiceAreas": resolved_practice,
        "requiredSkills": required_practice,
        "requiredPracticeAreas": required_practice,
        "locations": resolved_locations,
        "region": resolved_region,
        "minYears": resolved_years,
        "strictLocations": resolved_strict_locations,
        "workArrangement": resolved_work_arrangement,
        "workforceLocation": resolved_workforce_location,
    }


def _lawyer_search_criteria_from_payload(jd: dict, supplied_criteria: dict | None = None) -> dict:
    supplied = supplied_criteria if isinstance(supplied_criteria, dict) else {}
    return _lawyer_search_criteria(
        jd,
        titles=",".join(_safe_list(supplied.get("titles"))),
        practice_areas=",".join(_safe_list(supplied.get("practiceAreas"))),
        required_skills=",".join(
            _safe_list(supplied.get("requiredSkills") or supplied.get("requiredPracticeAreas"))
        ),
        locations=",".join(_safe_list(supplied.get("locations"))),
        region=_external_text(supplied.get("region"), 100),
        min_years=supplied.get("minYears") or 0,
        strict_locations=supplied.get("strictLocations"),
        work_arrangement=_external_text(supplied.get("workArrangement"), 40),
        workforce_location=_external_text(supplied.get("workforceLocation"), 40),
    )


def _lawyer_search_criteria_errors(criteria: dict) -> list[str]:
    errors = []
    if not _safe_list(criteria.get("titles")):
        errors.append("Current titles")
    if not _safe_list(criteria.get("requiredSkills") or criteria.get("requiredPracticeAreas")):
        errors.append("Must-have skills")
    if not _safe_list(criteria.get("practiceAreas")):
        errors.append("Practice evidence")
    if not str(criteria.get("region") or "").strip():
        errors.append("Target licensing jurisdiction")
    try:
        min_years = int(criteria.get("minYears") or 0)
    except (TypeError, ValueError):
        min_years = 0
    if min_years < 1:
        errors.append("Minimum years (at least 1)")
    if criteria.get("strictLocations") and not _safe_list(criteria.get("locations")):
        errors.append("Target cities (required when exact city matching is on)")
    if str(criteria.get("workArrangement") or "").strip().lower() not in LAWYER_WORK_ARRANGEMENTS:
        errors.append("Work arrangement")
    if str(criteria.get("workforceLocation") or "").strip().lower() not in LAWYER_WORKFORCE_LOCATIONS:
        errors.append("Workforce location")
    return errors


def _require_lawyer_search_criteria(criteria: dict):
    errors = _lawyer_search_criteria_errors(criteria)
    if errors:
        raise HTTPException(
            status_code=400,
            detail=(
                "Complete the non-negotiable Lawyer search criteria before contacting a provider: "
                + ", ".join(errors)
                + "."
            ),
        )


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


def _pdl_source_audit(
    response: dict,
    criteria: dict | None = None,
    query_mode: str = "skills",
    domain: str = "law",
):
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
        "legalReadiness": (
            f"{(criteria or {}).get('region') or 'Target-jurisdiction'} license and standing, plus "
            f"{(criteria or {}).get('workArrangement') or 'the required'} work arrangement, must be verified before permanent use or outreach."
            if domain == "law"
            else "Identity, current role, and material facts require human verification before outreach."
        ),
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
    domain: str = "law",
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
        "legalReadiness": (
            f"{(criteria or {}).get('region') or 'Target-jurisdiction'} license, standing, candidate identity, and "
            f"{(criteria or {}).get('workArrangement') or 'required'} work arrangement require manual verification."
            if domain == "law"
            else "Candidate identity, current role, credentials, and availability require manual verification."
        ),
        "linkedInMode": "Professional profile links may be returned; no LinkedIn scraping was performed.",
    }


def _location_fields(location: str, criteria: dict | None):
    clean = str(location or "")
    target_region = str((criteria or {}).get("region") or "").strip()
    region = target_region if target_region and target_region.lower() in clean.lower() else ""
    if not region and target_region:
        abbreviation = next(
            (abbr for abbr, state_name in US_STATE_NAMES_BY_ABBR.items() if state_name.lower() == target_region.lower()),
            "",
        )
        if abbreviation and re.search(rf",\s*{abbreviation}(?:\s|,|$)", clean, re.IGNORECASE):
            region = target_region
    locality = ""
    for city in (criteria or {}).get("locations", []):
        if str(city).lower() in clean.lower():
            locality = str(city)
            break
    return locality, region


def _workforce_location_from_result(row: dict) -> str:
    location = str(row.get("location") or row.get("location_name") or "").strip()
    if not location:
        return "unknown"
    normalized = location.lower()
    if any(
        marker in normalized
        for marker in ("united states", "united states of america", " u.s.", ", us", ", usa")
    ):
        return "onshore"
    if any(state.lower() in normalized for state in US_STATE_NAMES_BY_ABBR.values()):
        return "onshore"
    if re.search(r",\s*(?:" + "|".join(US_STATE_NAMES_BY_ABBR) + r")(?:\s|,|$)", location, re.IGNORECASE):
        return "onshore"
    return "offshore"


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


def _coresignal_text(value, limit: int = 1600) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return _external_text(re.sub(r"\s+", " ", text), limit)


def _coresignal_items(value, limit: int = 8) -> list[dict]:
    rows = []
    for item in value if isinstance(value, list) else []:
        deleted = item.get("deleted") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or deleted is True
            or str(deleted or "").strip() == "1"
            or item.get("deleted_at")
        ):
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def _coresignal_date(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key, width in (("year", 4), ("month", 2), ("day", 2)):
            try:
                number = int(value.get(key) or 0)
            except (TypeError, ValueError):
                number = 0
            if number:
                parts.append(str(number).zfill(width))
        return "-".join(parts)
    return _external_text(value, 40)


def _coresignal_record_date(item: dict, field: str) -> str:
    alternate = {"start_date": "date_from", "end_date": "date_to"}.get(field, field)
    direct = item.get(field) or item.get(alternate)
    if direct:
        return _coresignal_date(direct)
    parts = []
    for suffix, width in (("year", 4), ("month", 2), ("day", 2)):
        try:
            number = int(item.get(f"{alternate}_{suffix}") or 0)
        except (TypeError, ValueError):
            number = 0
        if number:
            parts.append(str(number).zfill(width))
    return "-".join(parts)


def _coresignal_true(value) -> bool:
    return value is True or value == 1 or str(value or "").strip().lower() in {"1", "true", "yes"}


def _coresignal_labels(value, limit: int = 30) -> list[str]:
    labels = []
    seen = set()
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[,;|\n]+", value)
    elif isinstance(value, dict):
        items = [value]
    else:
        items = []
    for item in items:
        if isinstance(item, dict):
            label = next(
                (
                    item.get(key)
                    for key in (
                        "name",
                        "title",
                        "skill",
                        "language",
                        "program",
                        "organization",
                        "organization_name",
                        "position",
                        "company_name",
                        "institution_name",
                        "value",
                    )
                    if item.get(key)
                ),
                "",
            )
        else:
            label = item
        clean = _coresignal_text(label, 120)
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            labels.append(clean)
        if len(labels) >= limit:
            break
    return labels


def _coresignal_collected_row(
    row: dict,
    job_skills: list[str],
    scoring_skills: list[str],
    criteria: dict | None = None,
) -> dict:
    is_multi_source = bool(
        row.get("professional_network_url")
        or row.get("active_experience_title")
        or row.get("primary_professional_email")
    )
    experiences = _coresignal_items(row.get("experience"), 10)
    if not experiences and (row.get("active_experience_title") or row.get("company_name")):
        experiences = [
            {
                "position_title": row.get("active_experience_title"),
                "company_name": row.get("company_name"),
                "company_website": row.get("company_website"),
                "company_industry": row.get("company_industry"),
                "company_size_range": row.get("company_size_range"),
                "description": row.get("active_experience_description"),
                "active_experience": 1,
            }
        ]
    current_experience = next(
        (
            item
            for item in experiences
            if _coresignal_true(item.get("is_current"))
            or _coresignal_true(item.get("active_experience"))
        ),
        None,
    )
    if current_experience is None:
        current_experience = next(
            (item for item in experiences if str(item.get("order_in_profile") or "") == "1"),
            experiences[0] if experiences else {},
        )

    normalized_experience = []
    for experience in experiences:
        location = _coresignal_text(experience.get("location"), 160)
        normalized_experience.append(
            {
                "title": _coresignal_text(
                    experience.get("position_title") or experience.get("title"),
                    180,
                ),
                "company": {
                    "name": _coresignal_text(
                        experience.get("company_name") or experience.get("company"),
                        180,
                    )
                },
                "company_name": _coresignal_text(experience.get("company_name"), 180),
                "summary": _coresignal_text(experience.get("description"), 1600),
                "start_date": _coresignal_record_date(experience, "start_date"),
                "end_date": _coresignal_record_date(experience, "end_date"),
                "is_primary": _coresignal_true(experience.get("is_current"))
                or _coresignal_true(experience.get("active_experience")),
                "location_names": [location] if location else [],
            }
        )

    normalized_education = []
    for education in _coresignal_items(row.get("education"), 6):
        institution = education.get("institution")
        if isinstance(institution, dict):
            institution = institution.get("name")
        degree = education.get("degree_name") or education.get("degree") or education.get("program_name") or education.get("program")
        program = education.get("field_of_study")
        normalized_education.append(
            {
                "school": {
                    "name": _coresignal_text(
                        education.get("institution_name") or education.get("school_name") or institution,
                        180,
                    )
                },
                "degrees": [_coresignal_text(degree, 120)] if degree else [],
                "majors": [_coresignal_text(program, 120)] if program else [],
                "start_date": _coresignal_record_date(education, "start_date"),
                "end_date": _coresignal_record_date(education, "end_date"),
            }
        )

    certifications = []
    for certification in _coresignal_items(row.get("certifications"), 10):
        title = _coresignal_text(certification.get("title") or certification.get("name"), 180)
        if title:
            certifications.append(
                {
                    "name": title,
                    "issuer": _coresignal_text(certification.get("issuer"), 180),
                    "credential_url": _external_text(
                        certification.get("certificate_url") or certification.get("credential_url"),
                        500,
                    ),
                }
            )

    skills = _coresignal_labels(row.get("inferred_skills"), 30)
    for skill in _coresignal_labels(row.get("skills"), 30):
        if skill.lower() not in {value.lower() for value in skills}:
            skills.append(skill)
        if len(skills) >= 30:
            break

    city = _coresignal_text(row.get("location_city") or row.get("city"), 120)
    state = _coresignal_text(row.get("location_state") or row.get("state"), 120)
    country = _coresignal_text(
        row.get("location_country") or row.get("country") or row.get("country_full_name"),
        120,
    )
    location = _coresignal_text(row.get("location_full") or row.get("location"), 240) or ", ".join(
        value for value in (city, state, country) if value
    )
    summary = _coresignal_text(row.get("summary"), 1600)
    headline = _coresignal_text(row.get("headline"), 300)

    websites = [
        {
            "label": _coresignal_text(item.get("name") or item.get("label") or "Website", 100),
            "url": _external_text(item.get("url"), 500),
        }
        for item in _coresignal_items(row.get("websites"), 8)
        if item.get("url")
    ]
    for label, url in (
        ("Professional website", row.get("website")),
        ("GitHub", row.get("github_url")),
        ("X / Twitter", row.get("twitter_url")),
    ):
        clean_url = _external_text(url, 500)
        if clean_url and clean_url.lower() not in {
            str(item.get("url") or "").lower() for item in websites
        }:
            websites.append({"label": label, "url": clean_url})

    professional_details = {
        "photoUrl": _external_text(row.get("picture_url") or row.get("photo_url"), 500),
        "services": _coresignal_labels(row.get("services"), 12),
        "languages": [
            {
                "name": _coresignal_text(item.get("name") or item.get("language"), 100),
                "proficiency": _coresignal_text(item.get("proficiency"), 100),
            }
            for item in _coresignal_items(row.get("languages"), 12)
            if _coresignal_text(item.get("name") or item.get("language"), 100)
        ],
        "projects": [
            {
                "title": _coresignal_text(item.get("title") or item.get("name"), 180),
                "description": _coresignal_text(item.get("description"), 600),
                "url": _external_text(item.get("url") or item.get("project_url"), 500),
            }
            for item in _coresignal_items(row.get("projects"), 6)
            if item.get("title") or item.get("name")
        ],
        "awards": [
            {
                "title": _coresignal_text(item.get("title") or item.get("name"), 180),
                "issuer": _coresignal_text(item.get("issuer"), 180),
                "date": _coresignal_record_date(item, "date"),
            }
            for item in _coresignal_items(row.get("awards"), 6)
            if item.get("title") or item.get("name")
        ],
        "organizations": _coresignal_labels(row.get("organizations"), 8),
        "courses": _coresignal_labels(row.get("courses"), 8),
        "patents": _coresignal_labels(row.get("patents"), 8),
        "publications": _coresignal_labels(row.get("publications"), 8),
        "volunteering": _coresignal_labels(row.get("volunteering_positions") or row.get("volunteering"), 8),
        "websites": websites[:8],
        "connectionsCount": row.get("connections_count") or 0,
        "followersCount": row.get("follower_count") or 0,
        "experienceCount": row.get("experience_count") or len(experiences),
        "recommendationsCount": row.get("recommendations_count") or len(_coresignal_items(row.get("recommendations"), 100)),
        "checkedAt": _external_text(row.get("checked_at"), 80),
        "updatedAt": _external_text(row.get("updated_at"), 80),
        "currentCompanyWebsite": _external_text(current_experience.get("company_website"), 500),
        "currentCompanyIndustry": _coresignal_text(current_experience.get("company_industry"), 180),
        "currentCompanySize": _coresignal_text(
            current_experience.get("company_size_range") or current_experience.get("company_size"),
            80,
        ),
        "currentDepartment": _coresignal_text(
            row.get("active_experience_department") or current_experience.get("department"),
            120,
        ),
        "currentManagementLevel": _coresignal_text(
            row.get("active_experience_management_level") or current_experience.get("management_level"),
            120,
        ),
        "decisionMaker": _coresignal_true(row.get("is_decision_maker")),
    }

    professional_emails = []
    for item in row.get("professional_emails_collection", [])[:8] if isinstance(row.get("professional_emails_collection"), list) else []:
        if not isinstance(item, dict):
            continue
        email = _external_text(item.get("professional_email"), 320)
        status = _external_text(item.get("professional_email_status"), 80)
        if email and email.lower() not in {entry["email"].lower() for entry in professional_emails}:
            professional_emails.append({"email": email, "status": status})
    primary_professional_email = _external_text(row.get("primary_professional_email"), 320)
    primary_professional_email_status = _external_text(
        row.get("primary_professional_email_status"),
        80,
    )
    if primary_professional_email and primary_professional_email.lower() not in {
        entry["email"].lower() for entry in professional_emails
    }:
        professional_emails.insert(
            0,
            {
                "email": primary_professional_email,
                "status": primary_professional_email_status,
            },
        )

    try:
        years_experience = round(max(0, int(row.get("total_experience_duration_months") or 0)) / 12, 1)
    except (TypeError, ValueError):
        years_experience = 0

    mapped = {
        "id": row.get("id"),
        "first_name": row.get("first_name") or "",
        "last_name": row.get("last_name") or "",
        "full_name": row.get("full_name") or "",
        "job_title": current_experience.get("position_title") or current_experience.get("title") or headline,
        "job_company_name": current_experience.get("company_name") or "",
        "location_name": location,
        "location_locality": city,
        "location_region": state,
        "location_country": country,
        "summary": summary or headline,
        "headline": headline,
        "skills": skills,
        "linkedin_url": row.get("professional_network_url") or row.get("profile_url") or "",
        "work_email": primary_professional_email,
        "inferred_years_experience": years_experience,
        "experience": normalized_experience,
        "education": normalized_education,
        "certifications": certifications,
    }
    result = _people_data_row(mapped, job_skills, scoring_skills, criteria)
    result.update(
        {
            "source": "coresignal",
            "source_label": (
                "Coresignal Multi-source Employee"
                if is_multi_source
                else "Coresignal Base Employee"
            ),
            "result_type": "licensed_professional_profile",
            "avatar_url": professional_details["photoUrl"],
            "summary": summary or headline,
            "job_last_verified": professional_details["checkedAt"] or professional_details["updatedAt"],
        }
    )
    result["contact"] = {
        "primaryEmail": primary_professional_email,
        "workEmail": primary_professional_email,
        "recommendedPersonalEmail": "",
        "personalEmails": [],
        "professionalEmails": professional_emails,
        "primaryProfessionalEmailStatus": primary_professional_email_status,
        "primaryPhone": "",
        "mobilePhone": "",
        "phoneNumbers": [],
        "provider": result["source_label"],
        "verificationRequired": True,
        "contactDataAvailable": bool(primary_professional_email or professional_emails),
        "contactScope": "professional_email_only" if is_multi_source else "none",
    }
    result["profile_data"]["professional_details"] = professional_details
    result["verification"].pop("pdl_job_last_verified", None)
    result["verification"]["coresignal_collect"] = "licensed_provider_data_requires_human_verification"
    result["verification"]["coresignal_checked_at"] = professional_details["checkedAt"]
    return result


def _courtlistener_row(row: dict, searched_name: str, jurisdiction: str = ""):
    evidence_type = str(row.get("evidenceType") or "court_record")
    evidence_label = "RECAP docket" if evidence_type == "recap_docket" else "Published opinion"
    title = str(row.get("title") or "Court record").strip()
    court = str(row.get("court") or "").strip()
    docket_number = str(row.get("docketNumber") or "").strip()
    date_filed = str(row.get("dateFiled") or "").strip()
    license_verification = _lawyer_license_verification(jurisdiction, searched_name)
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
            "license_status": license_verification["status"],
            "license_jurisdiction": license_verification["jurisdiction"],
            "license_search_url": license_verification["url"],
            "license_source": license_verification["source"],
            "california_bar_status": "not_verified"
            if license_verification["jurisdiction"].lower() == "california"
            else "not_applicable",
            "california_bar_search_url": license_verification["url"]
            if license_verification["jurisdiction"].lower() == "california"
            else "",
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
    jurisdiction = str(criteria.get("region") or "Target jurisdiction").strip()
    license_verification = _lawyer_license_verification(jurisdiction, name)
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
                f"{jurisdiction} license standing",
            ],
        },
        "top_matches": [f"Court query: {term}" for term in matched_terms[:4]],
        "verification": {
            "identity_status": "not_verified",
            "role_in_matter": "listed_on_matching_docket_not_individually_confirmed",
            "license_status": license_verification["status"],
            "license_jurisdiction": license_verification["jurisdiction"],
            "license_search_url": license_verification["url"],
            "license_source": license_verification["source"],
            "california_bar_status": "not_verified" if jurisdiction.lower() == "california" else "not_applicable",
            "california_bar_search_url": license_verification["url"] if jurisdiction.lower() == "california" else "",
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


def _professional_profile_urls_align(expected_url: str, returned_url: str) -> bool:
    def normalized(value: str) -> tuple[str, str]:
        text = str(value or "").strip()
        if text and not text.startswith(("http://", "https://")):
            text = "https://" + text.lstrip("/")
        parsed = urlparse(text)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.rstrip("/").lower()
        return host, path

    returned = normalized(returned_url)
    if not all(returned):
        return False
    expected = normalized(expected_url)
    return not all(expected) or expected == returned


def _people_data_row(
    row: dict,
    job_skills: list[str],
    scoring_skills: list[str],
    lawyer_criteria: dict | None = None,
):
    skills = _safe_list(row.get("skills"))
    evidence = list(skills)
    evidence.extend(
        value
        for value in [
            row.get("job_title"),
            row.get("headline"),
            row.get("job_summary"),
            row.get("summary"),
        ]
        if isinstance(value, str) and value.strip()
    )
    for experience in row.get("experience", [])[:8] if isinstance(row.get("experience"), list) else []:
        if isinstance(experience, dict):
            evidence.extend(
                value
                for value in [_external_position_title(experience.get("title")), experience.get("summary")]
                if isinstance(value, str) and value.strip()
            )
    for certification in row.get("certifications", [])[:10] if isinstance(row.get("certifications"), list) else []:
        value = certification.get("name") or certification.get("title") if isinstance(certification, dict) else certification
        if isinstance(value, str) and value.strip():
            evidence.append(value)
    if lawyer_criteria:
        score, top_matches, score_details = _lawyer_match_score(row, lawyer_criteria)
    else:
        score, top_matches, score_details = _rank_external_skill_match(evidence, job_skills, scoring_skills)
        score_details["evidence_count"] = len(_safe_list(evidence))
    first = row.get("first_name") or ""
    last = row.get("last_name") or ""
    name = (first + " " + last).strip() or row.get("full_name") or "Unknown candidate"
    linkedin_url = row.get("linkedin_url") or ""
    if linkedin_url and not linkedin_url.startswith("http"):
        linkedin_url = "https://www." + linkedin_url.lstrip("/")
    def contact_string(value) -> str:
        return value.strip() if isinstance(value, str) else ""

    def contact_list(value, limit: int = 5) -> list[str]:
        clean = []
        for item in value if isinstance(value, list) else []:
            item_value = item.get("address") if isinstance(item, dict) else item
            item_text = contact_string(item_value)
            if item_text and item_text.lower() not in {existing.lower() for existing in clean}:
                clean.append(item_text)
            if len(clean) >= limit:
                break
        return clean

    work_email = contact_string(row.get("work_email"))
    recommended_personal_email = contact_string(row.get("recommended_personal_email"))
    personal_emails = contact_list(row.get("personal_emails"))
    if not personal_emails:
        personal_emails = contact_list(row.get("emails"))
    email = work_email or recommended_personal_email or (personal_emails[0] if personal_emails else "")
    mobile_phone = contact_string(row.get("mobile_phone"))
    phone_numbers = contact_list(row.get("phone_numbers"))
    if not phone_numbers:
        phone_numbers = contact_list(
            [item.get("number") for item in row.get("phones", []) if isinstance(item, dict)]
            if isinstance(row.get("phones"), list)
            else []
        )
    primary_phone = mobile_phone or (phone_numbers[0] if phone_numbers else "")
    location = row.get("location_name") or ", ".join([v for v in [row.get("location_locality"), row.get("location_region"), row.get("location_country")] if isinstance(v, str) and v])
    if not isinstance(location, str):
        location = ""
    license_verification = _lawyer_license_verification(
        (lawyer_criteria or {}).get("region") if lawyer_criteria else "",
        name,
    ) if lawyer_criteria else {}

    return {
        "source": "pdl",
        "source_label": "People Data Labs",
        "source_id": row.get("id") or "",
        "name": name,
        "email": email,
        "phone": primary_phone,
        "title": _external_position_title(row.get("job_title") or row.get("title")),
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
            "license_status": license_verification.get("status", "not_applicable"),
            "license_jurisdiction": license_verification.get("jurisdiction", ""),
            "license_search_url": license_verification.get("url", ""),
            "license_source": license_verification.get("source", ""),
            "california_bar_status": license_verification.get("status", "not_applicable")
            if license_verification.get("jurisdiction", "").lower() == "california"
            else "not_applicable",
            "california_bar_search_url": license_verification.get("url", "")
            if license_verification.get("jurisdiction", "").lower() == "california"
            else "",
            "pdl_job_last_verified": row.get("job_last_verified") or "",
            "linkedin_scan": "not_performed",
        },
        "contact": {
            "primaryEmail": email,
            "workEmail": work_email,
            "recommendedPersonalEmail": recommended_personal_email,
            "personalEmails": personal_emails,
            "primaryPhone": primary_phone,
            "mobilePhone": mobile_phone,
            "phoneNumbers": phone_numbers,
            "provider": "People Data Labs",
            "verificationRequired": True,
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
    dental_sources = [
        {
            "key": "ada-careercenter",
            "label": "ADA CareerCenter",
            "url": "https://careercenter.ada.org/",
            "accountUrl": "https://careercenter.ada.org/employers/",
            "accessModel": "Employer portal / job board",
            "apiStatus": "No public candidate API found",
            "candidateAccess": "Post jobs and receive applicants through ADA's career center.",
            "vetcodeUse": "Use as a research-only discovery and job-posting/channel source.",
        },
        {
            "key": "dentalpost",
            "label": "DentalPost",
            "url": "https://www.dentalpost.net/",
            "accountUrl": "https://www.dentalpost.net/employers/",
            "accessModel": "Employer portal / candidate workflow",
            "apiStatus": "No public self-serve API found",
            "candidateAccess": "Employer account supports posting, screening, scheduling, tracking, and messaging candidates.",
            "vetcodeUse": "Use as a high-priority dental board; integrate by partnership/export if DentalPost offers it.",
        },
        {
            "key": "adaa",
            "label": "ADAA Career Center",
            "url": "https://jobs.adaausa.org/",
            "accountUrl": "https://jobs.adaausa.org/employer/pricing/",
            "accessModel": "Association career center",
            "apiStatus": "Likely platform-managed job board; no public candidate API found",
            "candidateAccess": "Post dental-assistant jobs and manage applicants through employer tools.",
            "vetcodeUse": "Use for dental-assistant posting and public-web research signals.",
        },
        {
            "key": "danb",
            "label": "DANB",
            "url": "https://www.danb.org/career-center/dental-assistant-jobs",
            "accountUrl": "https://www.danb.org/career-center/danb-list-rentals",
            "accessModel": "List rental / sponsored email",
            "apiStatus": "No candidate API; outreach product is list rental or sponsored email",
            "candidateAccess": "Reach certificants or certificate holders through DANB-managed employer outreach.",
            "vetcodeUse": "Use for credentialed dental assistant campaigns; store responses as reviewed TEMP leads.",
        },
        {
            "key": "toothio",
            "label": "Toothio",
            "url": "https://www.toothio.com/",
            "accountUrl": "https://www.toothio.com/practices",
            "accessModel": "Staffing platform",
            "apiStatus": "No public API found; likely partner/demo path",
            "candidateAccess": "Hire dental temps or full-time staff through Toothio's platform.",
            "vetcodeUse": "Use as an external staffing channel; import only candidate details explicitly provided to VETCODE.",
        },
        {
            "key": "stynt",
            "label": "Stynt",
            "url": "https://stynt.com/JobBoard/",
            "accountUrl": "https://stynt.com/dental-offices/",
            "accessModel": "Dental staffing / AI job board",
            "apiStatus": "No public API found; contact sales/partner path",
            "candidateAccess": "Stynt ranks and prioritizes dental candidates for employer interview workflows.",
            "vetcodeUse": "Use as a channel to compare ranked candidates against DentalReady jobs.",
        },
        {
            "key": "dentistjobcafe",
            "label": "DentistJobCafe",
            "url": "https://www.dentistjobcafe.com/",
            "accountUrl": "https://www.dentistjobcafe.com/employers",
            "accessModel": "Employer/recruiter resume database",
            "apiStatus": "No public API found; employer sales/demo path",
            "candidateAccess": "Employer tools include posting jobs and searching dental resumes.",
            "vetcodeUse": "Use as a resume-search channel; import manually reviewed candidate leads only.",
        },
    ]
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
                "employeeDataset": coreSignal.enrichment_dataset(),
                "searchCreditsPerRequest": coreSignal.credit_cost("base"),
                "collectionCreditsPerRequest": coreSignal.credit_cost(coreSignal.enrichment_dataset()),
                "contactData": (
                    "professional_email_only"
                    if coreSignal.enrichment_dataset() == "multi_source"
                    else "none"
                ),
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
        "directories": {
            "dental": dental_sources,
        },
        "secretsExposed": False,
    }


@router.get("/external/criteria/{jd_id}")
def external_candidate_criteria(jd_id: str, domain: str = "dev"):
    clean_domain = _domain_key(domain)
    jd, _job_skills = _get_job_skills(jd_id, clean_domain)
    lawyer_criteria = _lawyer_search_criteria(jd) if clean_domain == "law" else {}
    criteria_errors = _lawyer_search_criteria_errors(lawyer_criteria) if lawyer_criteria else []
    license_verification = _lawyer_license_verification(lawyer_criteria.get("region", "")) if lawyer_criteria else {}
    return {
        "domain": clean_domain,
        "jd": {
            "jd_id": jd.get("jd_id"),
            "company": jd.get("company", ""),
            "title": jd.get("title", ""),
        },
        "criteria": lawyer_criteria,
        "criteriaStatus": {
            "complete": bool(lawyer_criteria) and not criteria_errors,
            "missing": criteria_errors,
            "providerContactBlocked": bool(criteria_errors),
        },
        "verificationSources": {
            "lawyerLicensingAuthority": license_verification.get("url", ""),
            "targetJurisdiction": lawyer_criteria.get("region", ""),
            "linkedIn": "profile_link_only",
            "courtEvidence": "CourtListener / RECAP",
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
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    if candidate:
        jd_id = _external_text(payload.get("jd_id"), 80)
        supplied_criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
        criteria = supplied_criteria
        if jd_id:
            jd, _job_skills = _get_job_skills(jd_id, "law")
            criteria = _lawyer_search_criteria_from_payload(jd, supplied_criteria)
        records = [item for item in evidence.get("results", []) if isinstance(item, dict)]
        evidence_text = " ".join(
            " ".join(
                str(item.get(field) or "")
                for field in ("title", "snippet", "court", "attorney")
            )
            for item in records
        ).lower()
        practice_terms = _safe_list(
            criteria.get("requiredPracticeAreas") or criteria.get("practiceAreas")
        )
        matched_practice = [term for term in practice_terms if term.lower() in evidence_text]
        original_profile_data = (
            candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
        )
        updated_candidate = {
            **candidate,
            "profile_data": {
                **original_profile_data,
                "evidence_count": len(records),
                "evidence_records": records[:8],
                "matched_practice_areas": matched_practice,
                "query_practice_areas": practice_terms,
                "courts": list(
                    dict.fromkeys(str(item.get("court") or "").strip() for item in records if item.get("court"))
                )[:8],
                "court_evidence_checked_at": evidence["searchedAt"],
            },
        }
        source_label = _external_text(candidate.get("source_label") or candidate.get("source"), 120)
        if "courtlistener" not in source_label.lower():
            updated_candidate["source_label"] = f"{source_label} + CourtListener / RECAP".strip(" +")
        score_details = (
            candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {}
        )
        updated_candidate["score_details"] = {
            **score_details,
            "evidence_sources": list(
                dict.fromkeys(_safe_list(score_details.get("evidence_sources")) + [source_label, "CourtListener / RECAP"])
            ),
            "court_evidence_count": len(records),
            "court_practice_signals": matched_practice,
            "court_identity_notice": "Court-record association is stored as supporting evidence and requires human confirmation.",
        }
        evidence["candidate"] = updated_candidate
        evidence["includedInCandidateProfile"] = True
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
        scoring_ready = previous_validation.get("status") == "confirmed_profile_match"
        return {
            "candidate": candidate,
            "profileValidation": previous_validation,
            "reused": True,
            "usedForCandidateScoring": scoring_ready,
            "courtEvidenceUsedForScoring": scoring_ready and bool(
                _safe_list(
                    (candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}).get(
                        "matched_practice_areas"
                    )
                )
            ),
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
        criteria = _lawyer_search_criteria_from_payload(jd, supplied_criteria)
    search_skills = _searchable_job_skills(job_skills, 12)
    region = _external_text(criteria.get("region"), 100) or "Target jurisdiction"

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
                f"No LinkedIn-linked PDL profile met the exact-name and {region} lookup threshold. "
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

    raw_profile = dict(response["data"])
    mapped = _people_data_row(raw_profile, job_skills, search_skills, criteria)
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
    original_profile_data = (
        candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    )
    court_practice_signals = _safe_list(original_profile_data.get("matched_practice_areas"))

    if confirmed:
        if court_practice_signals:
            raw_profile["skills"] = list(
                dict.fromkeys(_safe_list(raw_profile.get("skills")) + court_practice_signals)
            )
        mapped = _people_data_row(raw_profile, [], [], None)
        field_values = {
            "professional_profile_url": profile_url,
            "title": _external_text(mapped.get("title"), 200),
            "company": _external_text(mapped.get("company"), 200),
            "location": _external_text(mapped.get("location"), 240),
            "summary": _external_text(mapped.get("summary"), 1600),
            "email": _external_text(mapped.get("email"), 320),
            "phone": _external_text(mapped.get("phone"), 80),
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
        mapped_profile_data = (
            mapped.get("profile_data") if isinstance(mapped.get("profile_data"), dict) else {}
        )
        enriched_candidate["profile_data"] = {
            **original_profile_data,
            **mapped_profile_data,
            "pdl_person_id": _external_text(mapped.get("source_id"), 180),
        }
        enriched_candidate["contact"] = mapped.get("contact") if isinstance(mapped.get("contact"), dict) else {}
        enriched_candidate["source_label"] = "CourtListener / RECAP + People Data Labs"
        enriched_candidate["match_pending"] = True
        enriched_candidate["score_details"] = {
            **(
                candidate.get("score_details")
                if isinstance(candidate.get("score_details"), dict)
                else {}
            ),
            "evidence_sources": ["People Data Labs", "CourtListener / RECAP"],
            "court_evidence_count": int(original_profile_data.get("evidence_count") or 0),
            "court_practice_signals": court_practice_signals[:12],
            "court_identity_notice": "Court-record association still requires human confirmation.",
        }
        enriched_candidate["enrichment_provider"] = "People Data Labs + CourtListener / RECAP"
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
            "court_record_association": "name_linked_needs_human_confirmation",
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
            f"Use Calculate JD match to save the percentage and evidence breakdown. Current employment, {region} license standing, court-record identity, and interest still require verification."
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
        "courtEvidenceUsedForScoring": False,
        "linkedinScraped": False,
    }


def _saved_search_cache_hit(cached: dict) -> dict:
    response = json.loads(json.dumps((cached or {}).get("response") or {}, default=str))
    metadata = dict((cached or {}).get("metadata") or {})
    audit = dict(response.get("sourceAudit") or {})
    audit.update(
        {
            "queryExecuted": False,
            "queryCompleted": True,
            "estimatedCreditsUsed": 0,
            "costLabel": "saved search - no provider charge",
            "statusMessage": (
                f"Loaded saved query {metadata.get('queryName') or ''}. "
                "The provider was not contacted and no search credits were used."
            ).strip(),
            "loadedFromSavedSearch": True,
        }
    )
    pagination = dict(response.get("pagination") or {})
    pagination["costLabel"] = "saved search - no provider charge"
    response["sourceAudit"] = audit
    response["pagination"] = pagination
    response["savedSearch"] = metadata
    return response


def _prepare_external_search_history(query_payload: dict):
    cache_key = externalSearchHistory.query_cache_key(query_payload)
    try:
        cached = externalSearchHistory.get_cached_search(cache_key)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Saved-search storage is unavailable, so the provider was not contacted and no search credits were used. "
                f"Restore the VETCODE database connection and retry. ({type(exc).__name__})"
            ),
        ) from exc
    return cache_key, cached


def _save_external_search(
    *,
    cache_key: str,
    query_payload: dict,
    response_payload: dict,
    domain: str,
    source: str,
    query_mode: str,
    jd_id: str = "",
    jd_name: str = "",
    client_name: str = "",
    history_root_id: str = "",
) -> dict:
    query_name = externalSearchHistory.build_query_name(jd_name, client_name)
    metadata = externalSearchHistory.save_search(
        cache_key=cache_key,
        query_name=query_name,
        domain=domain,
        source=source,
        query_mode=query_mode,
        jd_id=jd_id,
        jd_name=jd_name,
        client_name=client_name,
        query_payload=query_payload,
        response_payload=response_payload,
        parent_id=history_root_id,
    )
    metadata["recordCount"] = len(response_payload.get("results") or [])
    return metadata


@router.post("/external/search")
def external_candidate_search(
    domain: str = Form(default="dev"),
    jd_id: str = Form(...),
    source: str = Form(default="pdl"),
    top_k: int = Form(default=10),
    titles: str = Form(default=""),
    practice_areas: str = Form(default=""),
    required_skills: str = Form(default=""),
    locations: str = Form(default=""),
    region: str = Form(default=""),
    min_years: int = Form(default=0),
    strict_locations: bool | None = Form(default=None),
    work_arrangement: str = Form(default=""),
    workforce_location: str = Form(default=""),
    scroll_token: str = Form(default=""),
    client_name: str = Form(default=""),
    history_root_id: str = Form(default=""),
):
    domain = _domain_key(domain)
    client_name = client_name if isinstance(client_name, str) else ""
    history_root_id = history_root_id if isinstance(history_root_id, (str, int)) else ""
    top_k = _external_result_limit(top_k)
    jd, job_skills = _get_job_skills(jd_id, domain)
    search_skills = _searchable_job_skills(job_skills, 12)
    selected_source = (source or "pdl").strip().lower()
    _assert_external_source_allowed(selected_source, domain)
    results = []
    criteria = (
        _lawyer_search_criteria(
            jd,
            titles=titles,
            practice_areas=practice_areas,
            required_skills=required_skills,
            locations=locations,
            region=region,
            min_years=min_years,
            strict_locations=strict_locations,
            work_arrangement=work_arrangement,
            workforce_location=workforce_location,
        )
        if domain == "law"
        else None
    )
    if criteria:
        _require_lawyer_search_criteria(criteria)
    resolved_client_name = str(jd.get("company") or client_name or "").strip()
    jd_name = str(jd.get("title") or "").strip()
    history_query = {
        "version": 2,
        "domain": domain,
        "source": selected_source,
        "queryMode": "job_description",
        "jdId": str(jd.get("jd_id") or jd_id or ""),
        "jdName": jd_name,
        "clientName": resolved_client_name,
        "jobSkills": job_skills,
        "searchSkills": search_skills,
        "criteria": criteria or {},
        "topK": top_k,
        "scrollToken": str(scroll_token or ""),
    }
    history_cache_key, cached_search = _prepare_external_search_history(history_query)
    if cached_search:
        return _saved_search_cache_hit(cached_search)
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
                    practice_areas=criteria["requiredSkills"],
                    locations=criteria["locations"],
                    region=criteria["region"],
                    min_years=criteria["minYears"],
                    strict_locations=criteria["strictLocations"],
                    workforce_location=criteria["workforceLocation"],
                    size=top_k,
                    scroll_token=scroll_token,
                )
                results = [
                    _people_data_row(row, job_skills, search_skills, criteria)
                    for row in pdl_response.get("data", [])
                ]
                source_audit = _pdl_source_audit(pdl_response, criteria, "lawyer", domain)
                pagination = _pdl_pagination(pdl_response, top_k)
            else:
                pdl_response = peopleDataLabs.searchSkills(search_skills, top_k, scroll_token=scroll_token)
                results = [_people_data_row(row, job_skills, search_skills) for row in pdl_response.get("data", [])]
                source_audit = _pdl_source_audit(pdl_response, {"skills": search_skills}, "skills", domain)
                pagination = _pdl_pagination(pdl_response, top_k)
        elif selected_source == "coresignal":
            page = _provider_page(scroll_token, 1, 100)
            core_response = coreSignal.search_people(
                titles=criteria["titles"] if criteria else search_skills,
                practice_areas=criteria["requiredSkills"] if criteria else search_skills,
                locations=criteria["locations"] if criteria else [],
                region=criteria["region"] if criteria else "",
                workforce_location=criteria["workforceLocation"] if criteria else "either",
                strict_locations=criteria["strictLocations"] if criteria else False,
                size=top_k,
                page=page,
            )
            results = [
                _coresignal_row(row, job_skills, search_skills, criteria)
                for row in core_response.get("data", [])
            ]
            criteria_rejected = 0
            if criteria and criteria.get("workforceLocation") == "offshore":
                reviewed_results = results
                results = [
                    row for row in reviewed_results if _workforce_location_from_result(row) == "offshore"
                ]
                criteria_rejected = len(reviewed_results) - len(results)
            source_audit = _provider_source_audit(
                "Coresignal",
                core_response,
                criteria or {"skills": search_skills},
                "employee_profile_preview",
                "search credits",
                domain,
            )
            source_audit["recordsReturned"] = len(results)
            source_audit["nonNegotiableRejected"] = criteria_rejected
            if criteria_rejected:
                source_audit["statusMessage"] = (
                    f"{criteria_rejected} profile preview did not meet the offshore location requirement and was removed."
                )
            pagination = _provider_pagination(
                core_response,
                top_k,
                f"{coreSignal.credit_cost('base')} search credits",
            )
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
                detail="Select People Data Labs, Coresignal, CourtListener, or GitHub.",
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
    response_payload = {
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
    try:
        response_payload["savedSearch"] = _save_external_search(
            cache_key=history_cache_key,
            query_payload=history_query,
            response_payload=response_payload,
            domain=domain,
            source=selected_source,
            query_mode="job_description",
            jd_id=str(jd.get("jd_id") or jd_id or ""),
            jd_name=jd_name,
            client_name=resolved_client_name,
            history_root_id=history_root_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "The provider returned results, but VETCODE could not save the required local query record. "
                f"Do not repeat this provider query until storage is restored. ({type(exc).__name__})"
            ),
        ) from exc
    return response_payload

@router.post("/external/search-direct")
def external_candidate_search_direct(
    domain: str = Form(default="dev"),
    query: str = Form(...),
    source: str = Form(default="pdl"),
    top_k: int = Form(default=10),
    scroll_token: str = Form(default=""),
    region: str = Form(default=""),
    client_name: str = Form(default=""),
    history_root_id: str = Form(default=""),
):
    domain = _domain_key(domain)
    client_name = client_name if isinstance(client_name, str) else ""
    history_root_id = history_root_id if isinstance(history_root_id, (str, int)) else ""
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
    _assert_external_source_allowed(selected_source, domain)
    resolved_client_name = str(client_name or "").strip()
    history_query = {
        "version": 1,
        "domain": domain,
        "source": selected_source,
        "queryMode": "direct",
        "directQuery": clean_query,
        "clientName": resolved_client_name,
        "region": str(region or "").strip(),
        "topK": top_k,
        "scrollToken": str(scroll_token or ""),
    }
    history_cache_key, cached_search = _prepare_external_search_history(history_query)
    if cached_search:
        return _saved_search_cache_hit(cached_search)
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
            source_audit = _pdl_source_audit(pdl_response, {"terms": search_terms}, "direct", domain)
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
                domain,
            )
            pagination = _provider_pagination(
                core_response,
                top_k,
                f"{coreSignal.credit_cost('base')} search credits",
            )
        elif selected_source == "courtlistener":
            if domain != "law":
                raise HTTPException(status_code=400, detail="CourtListener lawyer research is available in LegalReady.")
            court_response = courtListener.search_evidence(clean_query, size=min(top_k, 5))
            results = [
                _courtlistener_row(row, clean_query, region)
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
                detail="Select People Data Labs, Coresignal, CourtListener, or GitHub.",
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
    response_payload = {
        "source": selected_source,
        "jobSkills": search_terms,
        "searchSkills": search_terms,
        "results": results[:top_k],
        "searchUsesJobDescription": False,
        "sourceAudit": source_audit,
        "pagination": pagination,
    }
    try:
        response_payload["savedSearch"] = _save_external_search(
            cache_key=history_cache_key,
            query_payload=history_query,
            response_payload=response_payload,
            domain=domain,
            source=selected_source,
            query_mode="direct",
            client_name=resolved_client_name,
            history_root_id=history_root_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "The provider returned results, but VETCODE could not save the required local query record. "
                f"Do not repeat this provider query until storage is restored. ({type(exc).__name__})"
            ),
        ) from exc
    return response_payload

def _external_text(value, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _external_position_title(value, limit: int = 180) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "display_name", "role"):
            text = _external_text(value.get(key), limit)
            if text:
                return text
        raw = value.get("raw")
        if isinstance(raw, list) and raw:
            return _external_text(raw[0], limit)
        return ""
    return _external_text(value, limit)


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
        role = _external_position_title(experience.get("title") or experience.get("job_title"), 180)
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


def _external_professional_details(candidate: dict) -> dict:
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    details = profile_data.get("professional_details") if isinstance(profile_data.get("professional_details"), dict) else {}

    def labels(key: str, limit: int = 12) -> list[str]:
        return [
            _external_text(value, 180)
            for value in _safe_list(details.get(key))[:limit]
            if _external_text(value, 180)
        ]

    def records(key: str, fields: tuple[str, ...], limit: int = 8) -> list[dict]:
        clean = []
        for item in details.get(key, [])[:limit] if isinstance(details.get(key), list) else []:
            if not isinstance(item, dict):
                continue
            row = {
                field: _external_text(item.get(field), 600 if field == "description" else 500)
                for field in fields
            }
            if any(row.values()):
                clean.append(row)
        return clean

    counts = {}
    for source_key, target_key in (
        ("connectionsCount", "connectionsCount"),
        ("followersCount", "followersCount"),
        ("experienceCount", "experienceCount"),
        ("recommendationsCount", "recommendationsCount"),
    ):
        try:
            counts[target_key] = max(0, int(details.get(source_key) or 0))
        except (TypeError, ValueError):
            counts[target_key] = 0

    return {
        "photoUrl": _external_text(details.get("photoUrl"), 500),
        "services": labels("services"),
        "languages": records("languages", ("name", "proficiency"), 12),
        "projects": records("projects", ("title", "description", "url"), 6),
        "awards": records("awards", ("title", "issuer", "date"), 6),
        "organizations": labels("organizations", 8),
        "courses": labels("courses", 8),
        "patents": labels("patents", 8),
        "publications": labels("publications", 8),
        "volunteering": labels("volunteering", 8),
        "websites": records("websites", ("label", "url"), 8),
        **counts,
        "checkedAt": _external_text(details.get("checkedAt"), 80),
        "updatedAt": _external_text(details.get("updatedAt"), 80),
        "currentCompanyWebsite": _external_text(details.get("currentCompanyWebsite"), 500),
        "currentCompanyIndustry": _external_text(details.get("currentCompanyIndustry"), 180),
        "currentCompanySize": _external_text(details.get("currentCompanySize"), 80),
        "currentDepartment": _external_text(details.get("currentDepartment"), 120),
        "currentManagementLevel": _external_text(details.get("currentManagementLevel"), 120),
        "decisionMaker": details.get("decisionMaker") is True,
    }


def _external_professional_evidence(candidate: dict) -> list[str]:
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    evidence = [
        candidate.get("title"),
        candidate.get("summary"),
        profile_data.get("headline"),
        profile_data.get("job_summary"),
        *_external_certifications(candidate),
    ]
    for experience in profile_data.get("experience", [])[:8] if isinstance(profile_data.get("experience"), list) else []:
        if isinstance(experience, dict):
            evidence.extend([_external_position_title(experience.get("title")), experience.get("summary")])
    clean = []
    seen = set()
    for value in evidence:
        text = _external_text(value, 600)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        clean.append(text)
        if len(clean) >= 20:
            break
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


def _pending_external_match(notice: str = "Profile is enriched and ready for an explicit JD match calculation.") -> dict:
    return {
        "status": "not_run",
        "score": None,
        "jobId": "",
        "notice": notice,
    }


def _external_profile_metadata(candidate: dict, source: str, enrichment: dict) -> dict:
    score_details = candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {}
    is_court_lead = source == "courtlistener" and candidate.get("result_type") == "court_attorney_lead"
    profile_validation = (
        candidate.get("profile_validation")
        if isinstance(candidate.get("profile_validation"), dict)
        else {}
    )
    court_profile_confirmed = (
        is_court_lead and profile_validation.get("status") == "confirmed_profile_match"
    )
    verification = candidate.get("verification") if isinstance(candidate.get("verification"), dict) else {}
    try:
        match_score = round(float(candidate.get("score") or 0), 1)
    except (TypeError, ValueError):
        match_score = 0
    search_preview_match = {
        "score": match_score,
        "band": _external_text(candidate.get("match_band") or score_details.get("band"), 80),
        "formula": _external_text(score_details.get("formula"), 300),
        "matched": [] if is_court_lead and not court_profile_confirmed else _safe_list(candidate.get("top_matches"))[:12],
        "missing": _safe_list(score_details.get("missing"))[:10],
        "matchedCount": score_details.get("matched_count") or len(_safe_list(candidate.get("top_matches"))),
        "requiredCount": score_details.get("required_count") or len(_safe_list(score_details.get("scoring_skills"))),
        "components": score_details.get("components") if isinstance(score_details.get("components"), dict) else {},
        "evidenceSources": _safe_list(score_details.get("evidence_sources"))[:8],
        "courtEvidenceCount": score_details.get("court_evidence_count") or 0,
        "courtIdentityNotice": _external_text(score_details.get("court_identity_notice"), 300),
        "notice": "Discovery preview only. This is not the saved JD match.",
    }
    metadata = {
        "version": 2 if is_court_lead else 1,
        "source": _external_text(candidate.get("source_label") or source, 120),
        "sourceId": _external_text(candidate.get("source_id"), 180),
        "profileUrl": _external_text(candidate.get("profile_url"), 500),
        "recordType": _external_text(candidate.get("result_type"), 80),
        "enrichment": enrichment,
        "searchPreviewMatch": search_preview_match,
        "match": _pending_external_match("Use Calculate JD match after enrichment to save match statistics."),
        "education": _external_education(candidate),
        "certifications": _external_certifications(candidate),
        "contact": candidate.get("contact") if isinstance(candidate.get("contact"), dict) else {},
        "providerSkills": _safe_list(candidate.get("skills"))[:30] if is_court_lead else _external_candidate_skills(candidate),
        "professionalEvidence": [] if is_court_lead and not court_profile_confirmed else _external_professional_evidence(candidate),
        "professionalDetails": {} if is_court_lead and not court_profile_confirmed else _external_professional_details(candidate),
        "yearsExperience": candidate.get("years_experience") or 0,
        "lastVerified": _external_text(candidate.get("job_last_verified"), 80),
    }
    if is_court_lead:
        metadata["verification"] = {
            "identityStatus": _external_text(verification.get("identity_status") or "not_verified", 80),
            "licenseStatus": _external_text(verification.get("license_status") or verification.get("california_bar_status") or "not_verified", 80),
            "licenseJurisdiction": _external_text(verification.get("license_jurisdiction"), 100),
            "licenseSearchUrl": _external_text(verification.get("license_search_url") or verification.get("california_bar_search_url"), 500),
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
    court_evidence = _external_court_evidence(candidate)
    if court_evidence.get("evidenceCount"):
        metadata["courtEvidence"] = court_evidence
    return metadata


@router.post("/external/import")
def external_candidate_import(payload: dict = Body(...)):
    domain = _domain_key(payload.get("domain") or "dev")
    candidate = payload.get("candidate") or {}
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=400, detail="Candidate data is required.")
    source = candidate.get("source") or payload.get("source") or "external"
    _assert_external_source_allowed(source, domain)
    result_type = candidate.get("result_type")
    is_court_lead = source == "courtlistener" and result_type == "court_attorney_lead"
    profile_validation = (
        candidate.get("profile_validation")
        if isinstance(candidate.get("profile_validation"), dict)
        else {}
    )
    court_profile_confirmed = (
        is_court_lead and profile_validation.get("status") == "confirmed_profile_match"
    )
    if result_type in {
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
        if not court_profile_confirmed and payload.get("identity_unverified_acknowledged") is not True:
            supplied_criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
            jurisdiction = _external_text(supplied_criteria.get("region"), 100) or "target-jurisdiction"
            raise HTTPException(
                status_code=400,
                detail=f"Confirm that identity, current employment, experience, location, and {jurisdiction} license standing are unverified.",
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
            criteria = _lawyer_search_criteria_from_payload(jd, supplied_criteria)

    enrichment = {
        "status": "not_requested",
        "provider": "",
        "likelihood": 0,
        "creditsUsed": 0,
        "enrichedAt": "",
        "dataOrigin": "Provider-supplied professional data; no LinkedIn scraping.",
    }
    if is_court_lead:
        try:
            successful_enrichment_credits = int(profile_validation.get("successfulEnrichmentCredits") or 0)
        except (TypeError, ValueError):
            successful_enrichment_credits = 0
        enrichment.update(
            {
                "status": _external_text(profile_validation.get("status") or "not_run", 80),
                "provider": _external_text(
                    "People Data Labs + CourtListener / RECAP"
                    if court_profile_confirmed
                    else profile_validation.get("provider") or "CourtListener / RECAP",
                    120,
                ),
                "creditsUsed": successful_enrichment_credits,
                "enrichedAt": _external_text(profile_validation.get("checkedAt"), 80),
                "profileVersion": 2 if court_profile_confirmed else 1,
                "dataOrigin": (
                    "Combined licensed professional data and CourtListener docket evidence; no LinkedIn scraping."
                    if court_profile_confirmed
                    else "CourtListener docket research; identity and professional qualifications are not verified."
                ),
            }
        )
        if court_profile_confirmed:
            enrichment["status"] = "completed"
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
            discovery_match = {
                "score": candidate.get("score"),
                "match_band": candidate.get("match_band"),
                "top_matches": _safe_list(candidate.get("top_matches")),
                "score_details": candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {},
            }
            original_profile_data = (
                candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
            )
            original_score_details = (
                candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {}
            )
            court_practice_signals = _safe_list(original_profile_data.get("matched_practice_areas"))
            enrichment_row = dict(response["data"])
            if court_practice_signals:
                enrichment_row["skills"] = list(
                    dict.fromkeys(_safe_list(enrichment_row.get("skills")) + court_practice_signals)
                )
            mapped = _people_data_row(enrichment_row, [], [], None)
            merged_profile_data = {
                **original_profile_data,
                **(mapped.get("profile_data") if isinstance(mapped.get("profile_data"), dict) else {}),
            }
            candidate = {**candidate, **mapped, "profile_data": merged_profile_data}
            candidate.update(discovery_match)
            candidate["source"] = "pdl"
            candidate["source_label"] = (
                "People Data Labs + CourtListener / RECAP"
                if original_profile_data.get("evidence_count")
                else "People Data Labs"
            )
            if original_profile_data.get("evidence_count"):
                candidate["score_details"] = {
                    **original_score_details,
                    "evidence_sources": list(
                        dict.fromkeys(
                            _safe_list(original_score_details.get("evidence_sources"))
                            + ["People Data Labs", "CourtListener / RECAP"]
                        )
                    ),
                    "court_evidence_count": int(original_profile_data.get("evidence_count") or 0),
                    "court_practice_signals": court_practice_signals[:12],
                    "court_identity_notice": "Court-record association still requires human confirmation.",
                }
            enrichment.update(
                {
                    "status": "completed",
                    "likelihood": int(response.get("likelihood") or 0),
                    "creditsUsed": 1,
                    "profileVersion": 2,
                    "contactFieldsRequested": True,
                    "matchedInput": "pdl_id" if source_id else "profile_url",
                    "linkedinMode": "Provider dataset lookup only; LinkedIn was not scraped.",
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
        jurisdiction = (criteria or {}).get("region") or "target-jurisdiction"
        summary_parts.insert(1, (
            f"Combined professional and court-evidence profile. {jurisdiction} license standing and the court-record association still require human verification."
            if court_profile_confirmed
            else f"Court-record research profile. Identity, current employer, current location, years of experience, and {jurisdiction} license standing are not verified."
        ))
        if court_evidence.get("evidenceCount"):
            summary_parts.append(
                f"Court evidence: listed in {court_evidence['evidenceCount']} matching docket record"
                f"{'s' if court_evidence['evidenceCount'] != 1 else ''}; "
                + (
                    "the professional and court evidence is ready for an explicit JD match calculation; the court-record identity still requires review."
                    if court_profile_confirmed
                    else "this association is not candidate-fit scoring until a professional profile match is confirmed."
                )
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
            candidateCountry=location_data.get("country") or (None if is_court_lead and not court_profile_confirmed else candidate.get("location") or None),
            candidateTitle=candidate_title,
            portfolioExperiences=_external_portfolio(candidate),
        )
        created["source"] = source
        created["temporaryProfile"] = True
        created["importedSkills"] = [skill["title"] for skill in skills]
        created["enrichment"] = enrichment
        created["match"] = metadata["match"]
        created["identityUnverified"] = is_court_lead and not court_profile_confirmed
        created["identityNeedsReview"] = is_court_lead
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

def _saved_result_key(candidate: dict) -> str:
    return "|".join(
        str(candidate.get(key) or "").strip().lower()
        for key in ("source", "source_id", "profile_url", "name")
    )


def _combined_saved_search_response(group: dict) -> dict:
    pages = group.get("pages") if isinstance(group.get("pages"), list) else []
    if not pages:
        return {}
    response = json.loads(json.dumps(pages[0].get("response") or {}, default=str))
    all_results = []
    seen = set()
    returned = 0
    reviewed = 0
    total_matches = 0
    for page in pages:
        page_response = page.get("response") if isinstance(page.get("response"), dict) else {}
        page_audit = page_response.get("sourceAudit") if isinstance(page_response.get("sourceAudit"), dict) else {}
        returned += int(page_audit.get("recordsReturned") or len(page_response.get("results") or []))
        reviewed += int(page_audit.get("recordsReviewed") or len(page_response.get("results") or []))
        total_matches = max(total_matches, int(page_audit.get("totalMatches") or 0))
        for candidate in page_response.get("results") or []:
            if not isinstance(candidate, dict):
                continue
            key = _saved_result_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            all_results.append(candidate)
    all_results.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    audit = dict(response.get("sourceAudit") or {})
    audit.update(
        {
            "queryExecuted": False,
            "queryCompleted": True,
            "estimatedCreditsUsed": 0,
            "recordsReturned": returned,
            "recordsReviewed": reviewed,
            "totalMatches": total_matches,
            "costLabel": "saved search - no provider charge",
            "loadedFromSavedSearch": True,
            "statusMessage": "Loaded the saved results. The provider was not contacted and no search credits were used.",
        }
    )
    response["results"] = all_results
    response["sourceAudit"] = audit
    response["pagination"] = {
        "pageSize": len(all_results),
        "hasNext": False,
        "nextScrollToken": "",
        "costLabel": "saved search - no provider charge",
    }
    response["savedSearch"] = {
        **(group.get("metadata") or {}),
        "cacheHit": True,
        "recordCount": len(all_results),
    }
    response["savedQuery"] = pages[0].get("query") or {}
    return response


def _candidate_location(candidate: dict) -> str:
    direct = candidate.get("location")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
    location = profile_data.get("location") if isinstance(profile_data.get("location"), dict) else {}
    return ", ".join(
        str(location.get(key) or "").strip()
        for key in ("locality", "region", "country")
        if str(location.get(key) or "").strip()
    )


def _public_request_base_url(request: Request) -> str:
    base_url = request.base_url
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    if forwarded_proto in {"http", "https"}:
        base_url = base_url.replace(scheme=forwarded_proto)
    return str(base_url).rstrip("/")


def _saved_search_report_rows(group: dict, domain: str, request: Request) -> list[dict]:
    response = _combined_saved_search_response(group)
    temp_profiles = candidates.listTemporaryExternalProfiles(domain, 500).get("profiles") or []
    temp_by_source_id = {
        str(profile.get("sourceId") or "").strip().lower(): profile
        for profile in temp_profiles
        if str(profile.get("sourceId") or "").strip()
    }
    temp_by_profile_url = {
        str(profile.get("profileUrl") or "").strip().rstrip("/").lower(): profile
        for profile in temp_profiles
        if str(profile.get("profileUrl") or "").strip()
    }
    report_rows = []
    base_url = _public_request_base_url(request)
    for search_order, candidate in enumerate(response.get("results") or [], start=1):
        source_id = str(candidate.get("source_id") or "").strip().lower()
        profile_url_key = str(candidate.get("profile_url") or "").strip().rstrip("/").lower()
        temp = temp_by_source_id.get(source_id) or temp_by_profile_url.get(profile_url_key) or {}
        raw_score = temp.get("matchScore")
        if raw_score in {None, ""}:
            raw_score = candidate.get("score")
        score_details = candidate.get("score_details") if isinstance(candidate.get("score_details"), dict) else {}
        profile_data = candidate.get("profile_data") if isinstance(candidate.get("profile_data"), dict) else {}
        evidence_sources = []
        for values in (
            temp.get("matchEvidenceSources"),
            score_details.get("evidence_sources"),
            profile_data.get("evidence_sources"),
        ):
            if isinstance(values, list):
                evidence_sources.extend(str(value).strip() for value in values if str(value or "").strip())
        source_label = str(candidate.get("source_label") or candidate.get("source") or "External")
        if source_label and source_label not in evidence_sources:
            evidence_sources.insert(0, source_label)
        temp_id = temp.get("personid") or candidate.get("devready_profile_id") or ""
        report_rows.append(
            {
                "searchOrder": search_order,
                "name": candidate.get("name") or "Not Found",
                "title": candidate.get("title") or "",
                "location": _candidate_location(candidate),
                "email": temp.get("email") or candidate.get("email") or "",
                "phone": temp.get("phone") or candidate.get("phone") or "",
                "matchScore": raw_score,
                "matchBand": temp.get("matchBand") or candidate.get("match_band") or score_details.get("band") or "",
                "matched": temp.get("matchMatched") or candidate.get("top_matches") or [],
                "missing": temp.get("matchMissing") or score_details.get("missing") or [],
                "source": source_label,
                "evidenceSources": list(dict.fromkeys(evidence_sources)),
                "sourceProfileUrl": candidate.get("profile_url") or "",
                "tempProfileId": temp_id,
                "tempProfileUrl": (
                    f"{base_url}/ui/pages/profile-preview.html?domain={quote_plus(domain)}&profileId={quote_plus(str(temp_id))}"
                    if temp_id
                    else ""
                ),
            }
        )
    report_rows.sort(
        key=lambda row: (
            -float(row.get("matchScore") or 0),
            int(row.get("searchOrder") or 0),
        )
    )
    for rank, row in enumerate(report_rows, start=1):
        row["rank"] = rank
    return report_rows


@router.get("/external/search-history")
def external_candidate_search_history(domain: str = "dev", limit: int = 100, offset: int = 0):
    domain = _domain_key(domain)
    try:
        safe_limit = max(1, min(int(limit or 100), 500))
        safe_offset = max(0, int(offset or 0))
        searches = externalSearchHistory.list_searches(domain, safe_limit, safe_offset)
        total = externalSearchHistory.count_searches(domain)
        return {
            "status": "success",
            "searches": searches,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "hasMore": safe_offset + len(searches) < total,
            "retention": "Saved searches are not automatically deleted by VETCODE.",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Saved-search storage is unavailable. ({type(exc).__name__})") from exc


@router.get("/external/search-history/{search_id}")
def external_candidate_open_saved_search(search_id: str, domain: str = "dev"):
    domain = _domain_key(domain)
    try:
        group = externalSearchHistory.get_search_group(search_id, domain)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Saved-search storage is unavailable. ({type(exc).__name__})") from exc
    if not group:
        raise HTTPException(status_code=404, detail="Saved search not found in this domain.")
    return _combined_saved_search_response(group)


@router.get("/external/search-history/{search_id}/export")
def external_candidate_export_saved_search(search_id: str, request: Request, domain: str = "dev"):
    domain = _domain_key(domain)
    try:
        group = externalSearchHistory.get_search_group(search_id, domain)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Saved-search storage is unavailable. ({type(exc).__name__})") from exc
    if not group:
        raise HTTPException(status_code=404, detail="Saved search not found in this domain.")
    rows = _saved_search_report_rows(group, domain, request)
    if not rows:
        raise HTTPException(
            status_code=409,
            detail=(
                "This saved query has no candidate rows to export. Open the saved query or run the search again "
                "after results are returned. No provider request was made by this export."
            ),
        )
    workbook = externalSearchReport.build_ranked_search_xlsx(group, rows)
    metadata = group.get("metadata") if isinstance(group.get("metadata"), dict) else {}
    export_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(metadata.get("rootId") or metadata.get("id") or search_id)).strip("-")[:32]
    filename = f"{domain}-ranked-{export_id or 'saved'}.xlsx"
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-VETCODE-Record-Count": str(len(rows)),
            "X-VETCODE-Filename": filename,
        },
    )


@router.get("/external/temp")
def external_candidate_temp_profiles(domain: str = "dev", limit: int = 50):
    domain = _domain_key(domain)
    return candidates.listTemporaryExternalProfiles(domain, limit)


@router.get("/external/temp/linkedin-results/export")
def external_candidate_linkedin_results_export(domain: str = "dev"):
    domain = _domain_key(domain)
    rows = candidates.listLinkedInEnrichedTemporaryProfiles(domain, 500)
    workbook = linkedinResultsExport.build_linkedin_results_xlsx(rows, domain)
    date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"{domain}-linkedin-enriched-temp-profiles-{date_stamp}.xlsx"
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/external/temp/{person_id}/calculate-match")
def external_candidate_calculate_temp_match(person_id: str, payload: dict = Body(default={})):
    domain = _domain_key(payload.get("domain") or "dev")
    jd_id = _external_text(payload.get("jd_id"), 80)
    if not jd_id:
        raise HTTPException(status_code=400, detail="Choose an active job description before calculating a match.")

    stored = candidates.getTemporaryExternalProfileForEnrichment(person_id, domain)
    metadata = stored.get("externalProfile") if isinstance(stored.get("externalProfile"), dict) else {}
    enrichment = metadata.get("enrichment") if isinstance(metadata.get("enrichment"), dict) else {}
    try:
        enrichment_version = int(enrichment.get("profileVersion") or 1)
    except (TypeError, ValueError):
        enrichment_version = 1
    if enrichment.get("status") != "completed" or enrichment_version < 2:
        raise HTTPException(
            status_code=409,
            detail="Enrich this TEMP profile with licensed professional data before calculating its JD match.",
        )

    jd, job_skills = _get_job_skills(jd_id, domain)
    scoring_skills = _searchable_job_skills(job_skills, 12)
    supplied_criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    criteria = None
    if domain == "law":
        criteria = _lawyer_search_criteria_from_payload(jd, supplied_criteria)

    stored_skills = _safe_list(metadata.get("providerSkills"))
    stored_evidence = stored_skills + [
        _external_text(stored.get("title"), 200),
        _external_text(stored.get("description"), 2400),
        *(_safe_list(metadata.get("certifications"))),
        *(_safe_list(metadata.get("professionalEvidence"))),
    ]
    if criteria:
        score, matched, score_details = _lawyer_match_score(
            {
                "job_title": stored.get("title") or "",
                "skills": stored_skills,
                "summary": stored.get("description") or "",
                "location_name": ", ".join(
                    str(value or "") for value in (stored.get("location") or {}).values() if value
                ),
                "inferred_years_experience": metadata.get("yearsExperience") or 0,
            },
            criteria,
        )
    else:
        score, matched, score_details = _rank_external_skill_match(
            stored_evidence,
            job_skills,
            scoring_skills,
        )

    reason = _deterministic_fit_reason(
        stored.get("name") or "Candidate",
        score,
        matched,
        score_details,
    )
    evidence_sources = [
        _external_text(metadata.get("source"), 120),
        _external_text(enrichment.get("provider"), 120),
    ]
    court_evidence = metadata.get("courtEvidence") if isinstance(metadata.get("courtEvidence"), dict) else {}
    if int(court_evidence.get("evidenceCount") or 0):
        evidence_sources.append("CourtListener / RECAP")
    calculated_at = datetime.now(timezone.utc).isoformat()
    match = {
        "status": "calculated",
        "score": score,
        "band": score_details.get("band") or _score_band(score),
        "decision": reason.get("fit_decision") or "Review",
        "reason": reason.get("fit_reason") or "",
        "formula": score_details.get("formula") or reason.get("score_formula") or "weighted matched JD signals / weighted searchable JD signals",
        "matched": matched,
        "missing": _safe_list(score_details.get("missing"))[:12],
        "matchedCount": score_details.get("matched_count") or len(matched),
        "requiredCount": score_details.get("required_count") or len(scoring_skills),
        "components": score_details.get("components") if isinstance(score_details.get("components"), dict) else {},
        "evidenceSources": list(dict.fromkeys(source for source in evidence_sources if source)),
        "courtEvidenceCount": int(court_evidence.get("evidenceCount") or 0),
        "jobId": str(jd.get("jd_id") or jd_id),
        "jobTitle": _external_text(jd.get("title"), 240),
        "clientName": _external_text(jd.get("company"), 240),
        "calculationMode": "explicit_user_action",
        "calculatedAt": calculated_at,
    }
    result = candidates.saveTemporaryExternalProfileMatch(person_id, domain, match)
    result.update(
        {
            "providerCreditsUsed": 0,
            "providerContacted": False,
            "linkedinScraped": False,
        }
    )
    return result


@router.post("/external/temp/{person_id}/enrich-professional")
def external_candidate_enrich_temp_profile(person_id: str, payload: dict = Body(default={})):
    domain = _domain_key(payload.get("domain") or "dev")
    stored = candidates.getTemporaryExternalProfileForEnrichment(person_id, domain)
    current_metadata = (
        stored.get("externalProfile") if isinstance(stored.get("externalProfile"), dict) else {}
    )
    current_enrichment = (
        current_metadata.get("enrichment")
        if isinstance(current_metadata.get("enrichment"), dict)
        else {}
    )
    try:
        enrichment_version = int(current_enrichment.get("profileVersion") or 1)
    except (TypeError, ValueError):
        enrichment_version = 1
    current_provider = str(current_enrichment.get("provider") or "").lower()
    source_is_coresignal = "coresignal" in " ".join(
        [
            str(current_metadata.get("source") or ""),
            str(current_enrichment.get("provider") or ""),
        ]
    ).lower()
    reusable_enrichment = (
        current_enrichment.get("status") == "completed"
        and ("people data labs" in current_provider or "coresignal" in current_provider)
        and enrichment_version >= 2
    )
    if reusable_enrichment:
        return {
            "status": "success",
            "personid": stored.get("personid"),
            "name": stored.get("name"),
            "profileUrl": stored.get("profileUrl") or current_metadata.get("profileUrl") or "",
            "enrichment": current_enrichment,
            "reused": True,
            "creditsUsed": 0,
            "match": current_metadata.get("match") or {},
            "matchPending": (current_metadata.get("match") or {}).get("status") != "calculated",
            "linkedinScraped": False,
        }

    name = _external_text(stored.get("name"), 160)
    if len(_person_name_tokens(name)) < 2:
        raise HTTPException(status_code=400, detail="A complete name is required for profile enrichment.")
    profile_url = _external_text(stored.get("profileUrl"), 500)
    location = stored.get("location") if isinstance(stored.get("location"), dict) else {}

    if source_is_coresignal and coreSignal.configured():
        source_id = _external_text(current_metadata.get("sourceId"), 180)
        coresignal_dataset = coreSignal.enrichment_dataset()
        collect_by_profile = coresignal_dataset == "multi_source" and bool(profile_url)
        try:
            response = coreSignal.collect_person(
                employee_id="" if collect_by_profile else source_id,
                profile_url=profile_url if collect_by_profile or not source_id else "",
                dataset=coresignal_dataset,
            )
        except coreSignal.CoreSignalError as exc:
            provider_status = int(exc.status_code or 502)
            status_code = provider_status if provider_status in {400, 401, 402, 403, 404, 429, 503} else 502
            raise HTTPException(status_code=status_code, detail=f"TEMP profile enrichment failed: {str(exc)}") from exc

        if response.get("status") != 200 or not isinstance(response.get("data"), dict):
            raise HTTPException(
                status_code=404,
                detail="Coresignal could not collect the full professional profile for this TEMP record.",
            )

        mapped = _coresignal_collected_row(response["data"], [], [], None)
        returned_name = _external_text(mapped.get("name"), 160)
        matched_profile_url = _external_text(mapped.get("profile_url") or profile_url, 500)
        if not (
            _person_names_align(name, returned_name)
            and _professional_profile_urls_align(profile_url, matched_profile_url)
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Coresignal returned a possible record, but its name and professional-profile link "
                    "did not safely align with the selected TEMP profile. The profile was not changed."
                ),
            )

        enriched_at = datetime.now(timezone.utc).isoformat()
        collected_dataset = str(response.get("dataset") or coresignal_dataset).lower()
        is_multi_source = collected_dataset == "multi_source"
        provider_label = (
            "Coresignal Multi-source Employee Collect"
            if is_multi_source
            else "Coresignal Base Employee Collect"
        )
        enrichment = {
            "status": "completed",
            "provider": provider_label,
            "enrichedAt": enriched_at,
            "creditsUsed": int(
                response.get("credits_used")
                or coreSignal.credit_cost(coresignal_dataset)
            ),
            "profileVersion": 2,
            "contactFieldsRequested": is_multi_source,
            "matchedInput": response.get("matched_input") or ("profile_url" if collect_by_profile else "employee_id"),
            "employeeDataset": collected_dataset,
            "linkedinMode": "Licensed provider dataset lookup only; LinkedIn was not scraped.",
            "contactNotice": (
                "Coresignal Multi-source can include provider-reported professional email, but not phone data."
                if is_multi_source
                else "Coresignal Base Employee does not include email or phone data."
            ),
        }
        updated_metadata = {
            **current_metadata,
            "source": (
                "Coresignal Multi-source Employee"
                if is_multi_source
                else "Coresignal Base Employee"
            ),
            "sourceId": _external_text(mapped.get("source_id") or source_id, 180),
            "profileUrl": matched_profile_url,
            "enrichment": enrichment,
            "contact": mapped.get("contact") if isinstance(mapped.get("contact"), dict) else {},
            "providerSkills": _external_candidate_skills(mapped),
            "yearsExperience": mapped.get("years_experience") or 0,
            "lastVerified": _external_text(mapped.get("job_last_verified"), 80),
            "education": _external_education(mapped),
            "certifications": _external_certifications(mapped),
            "professionalEvidence": _external_professional_evidence(mapped),
            "professionalDetails": _external_professional_details(mapped),
            "match": _pending_external_match("Professional enrichment changed the profile. Calculate the JD match when ready."),
        }
        mapped["profile_url"] = matched_profile_url
        mapped["portfolio"] = _external_portfolio(mapped)
        result = candidates.applyTemporaryExternalProfileEnrichment(
            person_id,
            domain,
            mapped,
            updated_metadata,
        )
        result.update(
            {
                "reused": False,
                "creditsUsed": enrichment["creditsUsed"],
                "match": updated_metadata.get("match") or {},
                "matchPending": True,
                "linkedinScraped": False,
                "contactDataIncluded": bool((mapped.get("contact") or {}).get("primaryEmail")),
            }
        )
        return result

    try:
        response = peopleDataLabs.enrichPerson(
            profile=profile_url,
            name=name if not profile_url else "",
            locality=_external_text(location.get("locality"), 100) if not profile_url else "",
            region=_external_text(location.get("region"), 100) if not profile_url else "",
            country=_external_text(location.get("country"), 100) if not profile_url else "",
            min_likelihood=8,
            required="linkedin_url",
        )
    except peopleDataLabs.PeopleDataLabsError as exc:
        provider_status = int(exc.status_code or 502)
        status_code = provider_status if provider_status in {400, 401, 402, 403, 429, 503} else 502
        raise HTTPException(status_code=status_code, detail=f"TEMP profile enrichment failed: {str(exc)}") from exc

    if response.get("status") != 200 or not isinstance(response.get("data"), dict):
        raise HTTPException(
            status_code=404,
            detail="People Data Labs could not find a LinkedIn-linked professional profile for this TEMP record.",
        )

    mapped = _people_data_row(response["data"], [], [], None)
    returned_name = _external_text(mapped.get("name"), 160)
    try:
        likelihood = max(0, min(int(response.get("likelihood") or 0), 10))
    except (TypeError, ValueError):
        likelihood = 0
    matched_profile_url = _external_text(mapped.get("profile_url"), 500)
    if not (_person_names_align(name, returned_name) and likelihood >= 8 and matched_profile_url):
        raise HTTPException(
            status_code=409,
            detail=(
                "PDL returned a possible record, but it did not meet the exact-name, LinkedIn-link, "
                "and identity-likelihood threshold. The TEMP profile was not changed."
            ),
        )

    enriched_at = datetime.now(timezone.utc).isoformat()
    enrichment = {
        "status": "completed",
        "provider": "People Data Labs Person Enrichment",
        "enrichedAt": enriched_at,
        "likelihood": likelihood,
        "creditsUsed": 1,
        "profileVersion": 2,
        "contactFieldsRequested": True,
        "matchedInput": "profile_url" if profile_url else "name_location",
        "linkedinMode": "Provider dataset lookup only; LinkedIn was not scraped.",
    }
    updated_metadata = {
        **current_metadata,
        "profileUrl": matched_profile_url,
        "enrichment": enrichment,
        "contact": mapped.get("contact") if isinstance(mapped.get("contact"), dict) else {},
        "providerSkills": _external_candidate_skills(mapped),
        "yearsExperience": mapped.get("years_experience") or 0,
        "lastVerified": _external_text(mapped.get("job_last_verified"), 80),
        "education": _external_education(mapped),
        "certifications": _external_certifications(mapped),
        "professionalEvidence": _external_professional_evidence(mapped),
        "professionalDetails": _external_professional_details(mapped),
        "match": _pending_external_match("Professional enrichment changed the profile. Calculate the JD match when ready."),
    }
    mapped["portfolio"] = _external_portfolio(mapped)
    result = candidates.applyTemporaryExternalProfileEnrichment(
        person_id,
        domain,
        mapped,
        updated_metadata,
    )
    result.update(
        {
            "reused": False,
            "creditsUsed": 1,
            "match": updated_metadata.get("match") or {},
            "matchPending": True,
            "linkedinScraped": False,
        }
    )
    return result


@router.post("/external/temp/{person_id}/make-permanent")
def external_candidate_make_permanent(person_id: str):
    return candidates.makeTemporaryExternalProfilePermanent(person_id)

@router.delete("/external/temp/{person_id}")
def external_candidate_delete(person_id: str):
    return candidates.deleteTemporaryExternalProfile(person_id)
