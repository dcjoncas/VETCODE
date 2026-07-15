import html
import os
import re
from typing import Any

import requests


COURTLISTENER_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
COURTLISTENER_SITE = "https://www.courtlistener.com"
COURTLISTENER_TIMEOUT = 20


class CourtListenerError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def configured() -> bool:
    return bool(os.getenv("COURTLISTENER_API_TOKEN", "").strip())


def _api_token() -> str:
    value = os.getenv("COURTLISTENER_API_TOKEN", "").strip()
    if not value:
        raise CourtListenerError("CourtListener is not configured on this environment.", 503)
    return value


def _plain_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())[:limit]


def _error_message(status_code: int) -> str:
    messages = {
        400: "CourtListener rejected the evidence query.",
        401: "CourtListener rejected the configured API token.",
        403: "CourtListener API access is not enabled for this account.",
        404: "CourtListener could not find that result page.",
        429: "CourtListener rate limit reached. Try again after the provider reset.",
    }
    if status_code in messages:
        return messages[status_code]
    if status_code >= 500:
        return "CourtListener is temporarily unavailable."
    return f"CourtListener search failed with status {status_code}."


def _request(params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.get(
            COURTLISTENER_SEARCH_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "Authorization": f"Token {_api_token()}",
            },
            timeout=COURTLISTENER_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise CourtListenerError("CourtListener timed out before returning evidence.") from exc
    except requests.RequestException as exc:
        raise CourtListenerError("CourtListener could not be reached.") from exc

    if response.status_code != 200:
        raise CourtListenerError(_error_message(response.status_code), response.status_code)
    try:
        result = response.json()
    except ValueError as exc:
        raise CourtListenerError("CourtListener returned an invalid response.") from exc
    if not isinstance(result, dict):
        raise CourtListenerError("CourtListener returned an unexpected response.")
    return result


def _result_url(row: dict[str, Any]) -> str:
    value = str(row.get("absolute_url") or row.get("absoluteUrl") or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return COURTLISTENER_SITE + "/" + value.lstrip("/")


def _normalize(row: dict[str, Any], evidence_type: str) -> dict[str, Any]:
    court = row.get("court") or row.get("court_citation_string") or row.get("court_exact") or ""
    if isinstance(court, dict):
        court = court.get("full_name") or court.get("short_name") or court.get("id") or ""
    if isinstance(court, list):
        court = ", ".join(str(item) for item in court[:3])
    opinions = row.get("opinions") if isinstance(row.get("opinions"), list) else []
    nested_snippet = ""
    for opinion in opinions:
        if isinstance(opinion, dict) and opinion.get("snippet"):
            nested_snippet = opinion.get("snippet")
            break
    return {
        "evidenceType": evidence_type,
        "title": _plain_text(
            row.get("caseName")
            or row.get("case_name")
            or row.get("case_name_full")
            or row.get("docketNumber")
            or row.get("docket_number")
            or "Court record",
            240,
        ),
        "docketNumber": _plain_text(row.get("docketNumber") or row.get("docket_number"), 120),
        "court": _plain_text(court, 180),
        "dateFiled": _plain_text(row.get("dateFiled") or row.get("date_filed"), 40),
        "snippet": _plain_text(row.get("snippet") or nested_snippet, 500),
        "attorney": _plain_text(row.get("attorney"), 240),
        "url": _result_url(row),
    }


def search_evidence(name: str, size: int = 3) -> dict[str, Any]:
    _api_token()
    clean_name = " ".join(str(name or "").split()).strip()
    if len(clean_name) < 3:
        raise CourtListenerError("Provide a complete candidate name for evidence review.", 400)
    if len(clean_name) > 160:
        raise CourtListenerError("The candidate name is too long for evidence review.", 400)
    escaped_name = clean_name.replace("\\", " ").replace('"', " ")
    page_size = max(1, min(int(size or 3), 5))
    searches = [
        ("recap_docket", {"q": f'attorney:"{escaped_name}"', "type": "r"}),
        ("published_opinion", {"q": f'"{escaped_name}"', "type": "o"}),
    ]
    results = []
    counts: dict[str, int] = {}
    errors = []
    for evidence_type, query in searches:
        try:
            response = _request(
                {
                    **query,
                    "order_by": "score desc",
                    "page_size": page_size,
                    "highlight": "off",
                }
            )
            rows = response.get("results") if isinstance(response.get("results"), list) else []
            counts[evidence_type] = max(0, int(response.get("count") or 0))
            results.extend(
                _normalize(row, evidence_type)
                for row in rows[:page_size]
                if isinstance(row, dict)
            )
        except CourtListenerError as exc:
            counts[evidence_type] = 0
            errors.append({"evidenceType": evidence_type, "message": str(exc)})

    if errors and len(errors) == len(searches):
        raise CourtListenerError(errors[0]["message"])
    return {
        "provider": "CourtListener / RECAP",
        "queryExecuted": True,
        "candidateName": clean_name,
        "counts": counts,
        "results": results,
        "partialErrors": errors,
        "requestsUsed": len(searches),
        "identityVerified": False,
        "notice": (
            "Name matches are public research leads only. Confirm identity, role in the matter, "
            "and permitted employment use before relying on any record."
        ),
    }
