import math
import os
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

PDL_SEARCH_URL = "https://api.peopledatalabs.com/v5/person/search"
PDL_TIMEOUT = (5, 30)
PDL_SEARCH_FIELDS = ",".join(
    [
        "id",
        "full_name",
        "first_name",
        "last_name",
        "linkedin_url",
        "headline",
        "job_title",
        "job_company_name",
        "job_last_verified",
        "job_summary",
        "location_name",
        "location_locality",
        "location_region",
        "location_country",
        "inferred_years_experience",
        "summary",
        "skills",
        "experience.title",
        "experience.company.name",
        "experience.start_date",
        "experience.end_date",
        "experience.summary",
        "education.school.name",
        "education.degrees",
        "certifications",
        "github_url",
    ]
)


class PeopleDataLabsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _clean_terms(values: list[str] | tuple[str, ...] | None, limit: int = 20) -> list[str]:
    cleaned = []
    for value in values or []:
        term = str(value or "").strip().lower()
        if term and term not in cleaned:
            cleaned.append(term)
        if len(cleaned) >= limit:
            break
    return cleaned


def _result_size(value: int, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 5
    return max(1, min(parsed, maximum))


def _api_key() -> str:
    value = os.getenv("PDL_API_KEY", "").strip()
    if not value:
        raise PeopleDataLabsError("People Data Labs is not configured on this environment.")
    return value


def _error_message(response: requests.Response) -> str:
    messages = {
        400: "People Data Labs rejected the search criteria.",
        401: "People Data Labs rejected the configured API key.",
        402: "People Data Labs credits are unavailable for this request.",
        403: "People Data Labs access is not enabled for Person Search.",
        404: "The People Data Labs Person Search endpoint was not found.",
        429: "People Data Labs rate limit reached. Try again shortly.",
    }
    if response.status_code in messages:
        return messages[response.status_code]
    if response.status_code >= 500:
        return "People Data Labs is temporarily unavailable."
    return f"People Data Labs search failed with status {response.status_code}."


def _post_search(payload: dict[str, Any]) -> dict[str, Any]:
    request_payload = {
        **payload,
        "dataset": "resume",
        "titlecase": True,
        "data_include": PDL_SEARCH_FIELDS,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Key": _api_key(),
    }

    try:
        response = requests.post(
            PDL_SEARCH_URL,
            headers=headers,
            json=request_payload,
            timeout=PDL_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise PeopleDataLabsError("People Data Labs timed out before returning results.") from exc
    except requests.RequestException as exc:
        raise PeopleDataLabsError("People Data Labs could not be reached.") from exc

    if response.status_code != 200:
        raise PeopleDataLabsError(_error_message(response), response.status_code)

    try:
        result = response.json()
    except ValueError as exc:
        raise PeopleDataLabsError("People Data Labs returned an invalid response.") from exc

    if not isinstance(result, dict):
        raise PeopleDataLabsError("People Data Labs returned an unexpected response.")
    if not isinstance(result.get("data", []), list):
        result["data"] = []
    try:
        result["total"] = max(0, int(result.get("total") or 0))
    except (TypeError, ValueError):
        result["total"] = 0
    return result


def _skill_evidence_clause(skill: str) -> dict[str, Any]:
    return {
        "bool": {
            "should": [
                {"term": {"skills": skill}},
                {"match_phrase": {"summary": skill}},
                {"match_phrase": {"job_summary": skill}},
            ]
        }
    }


def _skill_evidence_clauses(skill_list: list[str]) -> list[dict[str, Any]]:
    return [_skill_evidence_clause(skill) for skill in _clean_terms(skill_list, 20)]


def searchSkills(skillList: list[str], size: int = 5):
    skills = _clean_terms(skillList, 20)
    if not skills:
        raise PeopleDataLabsError("Add at least one meaningful search skill.")

    required_skill_count = max(1, min(3, math.ceil(len(skills) * 0.4)))
    skill_clauses = _skill_evidence_clauses(skills)
    payload = {
        "query": {
            "bool": {
                "must": skill_clauses[:required_skill_count],
                "should": skill_clauses[required_skill_count:],
            }
        },
        "size": _result_size(size),
    }
    return _post_search(payload)


def searchDirect(searchQuery: str, size: int = 5):
    clean_query = str(searchQuery or "").strip()
    terms = _clean_terms(
        [part.strip() for part in clean_query.replace(";", ",").split(",")],
        12,
    )
    if not terms:
        raise PeopleDataLabsError("Enter a name, work email, profile URL, title, or skill.")

    should_clauses = []
    for term in terms:
        if "linkedin.com/in/" in term:
            normalized = term.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
            should_clauses.append({"term": {"linkedin_url": normalized}})
        elif "@" in term:
            should_clauses.append({"term": {"work_email": term}})
        else:
            should_clauses.extend(
                [
                    {"term": {"full_name": term}},
                    {"match_phrase": {"job_title.text": term}},
                    {"term": {"skills": term}},
                    {"match_phrase": {"headline": term}},
                    {"match_phrase": {"summary": term}},
                ]
            )

    payload = {
        "query": {
            "bool": {
                "should": should_clauses,
            }
        },
        "size": _result_size(size),
    }
    return _post_search(payload)


def build_lawyer_search_payload(
    titles: list[str],
    practice_areas: list[str],
    locations: list[str],
    region: str = "california",
    min_years: int = 0,
    strict_locations: bool = True,
    size: int = 10,
) -> dict[str, Any]:
    title_terms = _clean_terms(titles, 8) or ["attorney", "associate attorney", "lawyer", "counsel"]
    practice_terms = _clean_terms(practice_areas, 12)
    location_terms = _clean_terms(locations, 12)
    region_term = str(region or "").strip().lower()
    years = max(0, min(int(min_years or 0), 60))

    title_query = {
        "bool": {
            "should": [
                {"match_phrase": {"job_title.text": title}}
                for title in title_terms
            ],
        }
    }
    must_clauses: list[dict[str, Any]] = [title_query]

    if region_term:
        must_clauses.append({"term": {"location_region": region_term}})
    if years:
        must_clauses.append({"range": {"inferred_years_experience": {"gte": years}}})

    if practice_terms:
        practice_should = []
        for practice in practice_terms:
            practice_should.extend(
                [
                    {"term": {"skills": practice}},
                    {"match_phrase": {"job_title.text": practice}},
                    {"match_phrase": {"headline": practice}},
                    {"match_phrase": {"summary": practice}},
                    {"match_phrase": {"job_summary": practice}},
                    {"match_phrase": {"experience.summary": practice}},
                ]
            )
        must_clauses.append(
            {
                "bool": {
                    "should": practice_should,
                }
            }
        )

    should_clauses: list[dict[str, Any]] = [{"exists": {"field": "linkedin_url"}}]
    if location_terms:
        location_query = {"terms": {"location_locality": location_terms}}
        if strict_locations:
            must_clauses.append(location_query)
        else:
            should_clauses.append(location_query)

    return {
        "query": {
            "bool": {
                "must": must_clauses,
                "should": should_clauses,
            }
        },
        "size": _result_size(size),
    }


def searchLawyers(
    titles: list[str],
    practice_areas: list[str],
    locations: list[str],
    region: str = "california",
    min_years: int = 0,
    strict_locations: bool = True,
    size: int = 10,
):
    payload = build_lawyer_search_payload(
        titles=titles,
        practice_areas=practice_areas,
        locations=locations,
        region=region,
        min_years=min_years,
        strict_locations=strict_locations,
        size=size,
    )
    return _post_search(payload)


def searchSkillsAndLocation(
    skillList: list[str],
    locationCity: str = "",
    locationState: str = "",
    locationCountry: str = "",
    size: int = 5,
):
    skills = _clean_terms(skillList, 20)
    if not skills:
        raise PeopleDataLabsError("Add at least one meaningful search skill.")

    must_clauses = []
    if str(locationCity or "").strip():
        must_clauses.append({"term": {"location_locality": str(locationCity).strip().lower()}})
    if str(locationState or "").strip():
        must_clauses.append({"term": {"location_region": str(locationState).strip().lower()}})
    if str(locationCountry or "").strip():
        must_clauses.append({"term": {"location_country": str(locationCountry).strip().lower()}})

    required_skill_count = max(1, min(3, math.ceil(len(skills) * 0.4)))
    skill_clauses = _skill_evidence_clauses(skills)
    payload = {
        "query": {
            "bool": {
                "must": must_clauses + skill_clauses[:required_skill_count],
                "should": skill_clauses[required_skill_count:],
            }
        },
        "size": _result_size(size),
    }
    return _post_search(payload)
