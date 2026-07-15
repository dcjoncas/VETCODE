import os
from typing import Any

import requests


CORESIGNAL_PREVIEW_URL = (
    "https://api.coresignal.com/cdapi/v2/employee_base/search/filter/preview"
)
CORESIGNAL_TIMEOUT = 20


class CoreSignalError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def configured() -> bool:
    return bool(os.getenv("CORESIGNAL_API_KEY", "").strip())


def _api_key() -> str:
    value = os.getenv("CORESIGNAL_API_KEY", "").strip()
    if not value:
        raise CoreSignalError("Coresignal is not configured on this environment.", 503)
    return value


def _clean_terms(values: list[str], limit: int = 8) -> list[str]:
    clean = []
    for value in values or []:
        term = " ".join(str(value or "").split()).strip(" ,;\"'")
        if term and term.lower() not in {item.lower() for item in clean}:
            clean.append(term[:100])
        if len(clean) >= limit:
            break
    return clean


def _or_filter(values: list[str]) -> str:
    return " OR ".join(f'("{value}")' for value in _clean_terms(values))


def _error_message(status_code: int) -> str:
    messages = {
        400: "Coresignal rejected the search criteria.",
        401: "Coresignal rejected the configured API key.",
        402: "Coresignal credits are unavailable for this request.",
        403: "Coresignal access is not enabled for Employee Search.",
        404: "The Coresignal Employee Search endpoint was not found.",
        429: "Coresignal rate limit reached. Try again shortly.",
    }
    if status_code in messages:
        return messages[status_code]
    if status_code >= 500:
        return "Coresignal is temporarily unavailable."
    return f"Coresignal search failed with status {status_code}."


def search_people(
    titles: list[str],
    practice_areas: list[str],
    locations: list[str],
    region: str = "California",
    size: int = 10,
    page: int = 1,
    direct_query: str = "",
) -> dict[str, Any]:
    page_size = max(1, min(int(size or 10), 20))
    page_number = max(1, min(int(page or 1), 100))
    clean_titles = _clean_terms(titles) or ["Attorney", "Lawyer", "Counsel"]
    clean_practice = _clean_terms(practice_areas)
    clean_locations = _clean_terms(locations)
    location_terms = clean_locations + ([region] if region else [])
    clean_direct_query = " ".join(str(direct_query or "").split()).strip()
    if clean_direct_query:
        payload: dict[str, Any] = {
            "keyword": clean_direct_query[:300],
            "deleted": False,
        }
    else:
        payload = {
            "experience_title": _or_filter(clean_titles),
            "location": _or_filter(location_terms),
            "country": "United States",
            "deleted": False,
        }
        if clean_practice:
            payload["keyword"] = _or_filter(clean_practice)

    try:
        response = requests.post(
            CORESIGNAL_PREVIEW_URL,
            params={"page": page_number, "items_per_page": page_size},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "apikey": _api_key(),
            },
            json=payload,
            timeout=CORESIGNAL_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise CoreSignalError("Coresignal timed out before returning results.") from exc
    except requests.RequestException as exc:
        raise CoreSignalError("Coresignal could not be reached.") from exc

    if response.status_code != 200:
        raise CoreSignalError(_error_message(response.status_code), response.status_code)

    try:
        result = response.json()
    except ValueError as exc:
        raise CoreSignalError("Coresignal returned an invalid response.") from exc

    if isinstance(result, list):
        rows = [row for row in result if isinstance(row, dict)]
    elif isinstance(result, dict):
        candidate_rows = result.get("data") or result.get("results") or result.get("items") or []
        rows = [row for row in candidate_rows if isinstance(row, dict)] if isinstance(candidate_rows, list) else []
    else:
        rows = []

    def header_int(name: str, default: int) -> int:
        try:
            return max(0, int(response.headers.get(name) or default))
        except (TypeError, ValueError):
            return default

    total = header_int("x-total-results", len(rows))
    total_pages = header_int("x-total-pages", page_number + (1 if len(rows) >= page_size else 0))
    has_more = page_number < total_pages
    total = max(total, page_number * page_size + (1 if has_more else 0))
    return {
        "status": 200,
        "data": rows[:page_size],
        "total": total,
        "page": page_number,
        "page_size": page_size,
        "has_more": has_more,
        "next_page": page_number + 1 if has_more else None,
        "query": payload,
        "credits_used": 1,
    }
