import html
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests


COURTLISTENER_SEARCH_URL = "https://www.courtlistener.com/api/rest/v4/search/"
COURTLISTENER_ATTORNEYS_URL = "https://www.courtlistener.com/api/rest/v4/attorneys/"
COURTLISTENER_SITE = "https://www.courtlistener.com"
COURTLISTENER_TIMEOUT = 20
COURTLISTENER_MAX_DOCKETS = 20
COURTLISTENER_MAX_FALLBACK_REQUESTS = 3


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


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
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


def _request(params: dict[str, Any]) -> dict[str, Any]:
    return _get_json(COURTLISTENER_SEARCH_URL, params)


def _result_url(row: dict[str, Any]) -> str:
    value = str(
        row.get("docket_absolute_url")
        or row.get("absolute_url")
        or row.get("absoluteUrl")
        or ""
    ).strip()
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
    recap_documents = (
        row.get("recap_documents") if isinstance(row.get("recap_documents"), list) else []
    )
    nested_snippet = ""
    for document in [*opinions, *recap_documents]:
        if isinstance(document, dict) and document.get("snippet"):
            nested_snippet = document.get("snippet")
            break
    attorney_values = row.get("attorney") if isinstance(row.get("attorney"), list) else []
    attorney_ids = row.get("attorney_id") if isinstance(row.get("attorney_id"), list) else []
    attorneys = [_plain_text(value, 160) for value in attorney_values if _plain_text(value, 160)]
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
        "docketId": _plain_text(row.get("docket_id") or row.get("id"), 40),
        "court": _plain_text(court, 180),
        "courtId": _plain_text(row.get("court_id"), 40),
        "dateFiled": _plain_text(row.get("dateFiled") or row.get("date_filed"), 40),
        "snippet": _plain_text(row.get("snippet") or nested_snippet, 500),
        "attorney": ", ".join(attorneys[:8]),
        "attorneys": attorneys,
        "attorneyIds": [str(value) for value in attorney_ids if value is not None],
        "url": _result_url(row),
    }


def _practice_terms(criteria: dict[str, Any]) -> list[str]:
    raw_terms = criteria.get("requiredPracticeAreas") or criteria.get("practiceAreas") or []
    generic = {
        "litigation",
        "civil litigation",
        "litigation defense",
        "depositions",
        "trial preparation",
        "malpractice",
    }
    cleaned = []
    for raw in raw_terms:
        value = re.sub(r'["\\():\[\]{}]+', " ", str(raw or ""))
        value = " ".join(value.split()).strip()[:80]
        if len(value) < 4 or value.lower() in {item.lower() for item in cleaned}:
            continue
        cleaned.append(value)
    specific = [term for term in cleaned if term.lower() not in generic]
    return (specific or cleaned or ["professional liability", "legal malpractice"])[:5]


def _court_ids(criteria: dict[str, Any]) -> list[str]:
    location_text = " ".join(str(value or "").lower() for value in criteria.get("locations") or [])
    districts = [
        (
            "cacd",
            [
                "los angeles",
                "irvine",
                "orange county",
                "riverside",
                "san bernardino",
                "ventura",
                "santa barbara",
                "san luis obispo",
            ],
        ),
        (
            "cand",
            [
                "walnut creek",
                "san francisco",
                "oakland",
                "san jose",
                "contra costa",
                "alameda",
                "marin",
                "napa",
                "sonoma",
                "monterey",
            ],
        ),
        ("caed", ["sacramento", "fresno", "bakersfield", "stockton", "redding"]),
        ("casd", ["san diego", "imperial county"]),
    ]
    matched = [court_id for court_id, names in districts if any(name in location_text for name in names)]
    region = str(criteria.get("region") or "").strip().lower()
    if not matched and region in {"california", "ca"}:
        return ["cacd", "cand", "caed", "casd"]
    return matched or ["cacd", "cand", "caed", "casd"]


def _looks_like_person_name(value: str) -> bool:
    clean = " ".join(str(value or "").split()).strip()
    if len(clean) < 5 or len(clean) > 120:
        return False
    lowered = f" {clean.lower()} "
    entity_markers = {
        " state of ",
        " united states ",
        " department ",
        " county of ",
        " city of ",
        " office of ",
        " attorney general ",
        " law offices ",
        " corporation ",
        " company ",
        " llc ",
        " inc ",
        " pro se ",
        " unknown ",
    }
    if any(marker in lowered for marker in entity_markers):
        return False
    words = [word for word in clean.split() if any(char.isalpha() for char in word)]
    return 2 <= len(words) <= 7


def _matched_practice_terms(row: dict[str, Any], terms: list[str]) -> list[str]:
    documents = row.get("recap_documents") if isinstance(row.get("recap_documents"), list) else []
    searchable = " ".join(
        [
            str(row.get("caseName") or ""),
            str(row.get("case_name_full") or ""),
            str(row.get("cause") or ""),
            str(row.get("suitNature") or ""),
            *(str(item.get("snippet") or "") for item in documents if isinstance(item, dict)),
        ]
    )
    searchable = _plain_text(searchable, 5000).lower()
    return [term for term in terms if term.lower() in searchable]


def _fallback_attorneys(docket_id: str) -> list[dict[str, Any]]:
    response = _get_json(
        COURTLISTENER_ATTORNEYS_URL,
        {
            "docket": docket_id,
            "filter_nested_results": "true",
            "page_size": 100,
        },
    )
    rows = response.get("results") if isinstance(response.get("results"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def search_attorneys_by_criteria(criteria: dict[str, Any], size: int = 10) -> dict[str, Any]:
    _api_token()
    if not isinstance(criteria, dict):
        raise CourtListenerError("CourtListener JD criteria are required.", 400)
    result_limit = max(1, min(int(size or 10), 20))
    terms = _practice_terms(criteria)
    court_ids = _court_ids(criteria)
    recent_year = datetime.now(timezone.utc).year - 8
    phrase_query = " OR ".join(f'"{term}"' for term in terms)
    court_query = " OR ".join(court_ids)
    query = (
        f"court_id:({court_query}) AND ({phrase_query}) "
        f"AND dateFiled:[{recent_year}-01-01 TO *]"
    )
    response = _request(
        {
            "q": query,
            "type": "r",
            "order_by": "score desc",
            "page_size": COURTLISTENER_MAX_DOCKETS,
            "highlight": "on",
        }
    )
    rows = response.get("results") if isinstance(response.get("results"), list) else []
    docket_rows = [row for row in rows[:COURTLISTENER_MAX_DOCKETS] if isinstance(row, dict)]
    leads: dict[str, dict[str, Any]] = {}
    requests_used = 1
    partial_errors = []
    discovery_index = 0

    for row in docket_rows:
        evidence = _normalize(row, "recap_docket")
        matched_terms = _matched_practice_terms(row, terms)
        evidence["matchedPracticeAreas"] = matched_terms
        names = evidence.get("attorneys") or []
        attorney_ids = evidence.get("attorneyIds") or []
        attorney_rows = [
            {
                "name": name,
                "id": attorney_ids[index] if index < len(attorney_ids) else "",
                "email": "",
                "phone": "",
                "contact_raw": "",
            }
            for index, name in enumerate(names)
        ]
        if not attorney_rows and evidence.get("docketId") and requests_used <= COURTLISTENER_MAX_FALLBACK_REQUESTS:
            try:
                attorney_rows = _fallback_attorneys(evidence["docketId"])
                requests_used += 1
            except CourtListenerError as exc:
                requests_used += 1
                partial_errors.append(
                    {"docketId": evidence["docketId"], "message": str(exc)}
                )

        for attorney in attorney_rows:
            name = _plain_text(attorney.get("name"), 120)
            if not _looks_like_person_name(name):
                continue
            if name.isupper():
                name = name.title()
            identity_key = re.sub(r"[^a-z0-9]+", "", name.lower())
            if not identity_key:
                continue
            lead = leads.get(identity_key)
            if lead is None:
                lead = {
                    "name": name,
                    "attorneyId": str(attorney.get("id") or ""),
                    "email": _plain_text(attorney.get("email"), 180),
                    "phone": _plain_text(attorney.get("phone"), 80),
                    "contactRaw": _plain_text(attorney.get("contact_raw"), 500),
                    "evidence": [],
                    "matchedPracticeAreas": [],
                    "courts": [],
                    "_discoveryIndex": discovery_index,
                }
                discovery_index += 1
                leads[identity_key] = lead
            elif not lead.get("attorneyId") and attorney.get("id"):
                lead["attorneyId"] = str(attorney.get("id"))
            if evidence.get("docketId") not in {
                item.get("docketId") for item in lead["evidence"]
            }:
                lead["evidence"].append(evidence)
            for term in matched_terms:
                if term not in lead["matchedPracticeAreas"]:
                    lead["matchedPracticeAreas"].append(term)
            if evidence.get("court") and evidence["court"] not in lead["courts"]:
                lead["courts"].append(evidence["court"])

    ranked = sorted(
        leads.values(),
        key=lambda lead: (
            -len(lead.get("evidence") or []),
            -len(lead.get("matchedPracticeAreas") or []),
            int(lead.get("_discoveryIndex") or 0),
        ),
    )
    attorneys_discovered = len(ranked)
    for lead in ranked:
        lead.pop("_discoveryIndex", None)
    matching_dockets = max(0, int(response.get("count") or 0))
    return {
        "provider": "CourtListener / RECAP",
        "queryExecuted": True,
        "query": query,
        "criteria": criteria,
        "courtIds": court_ids,
        "practiceTerms": terms,
        "matchingDockets": matching_dockets,
        "docketsReviewed": len(docket_rows),
        "attorneysDiscovered": attorneys_discovered,
        "results": ranked[:result_limit],
        "partialErrors": partial_errors,
        "requestsUsed": requests_used,
        "countIsEstimate": matching_dockets > 2000,
        "identityVerified": False,
        "notice": (
            "These names were listed as attorneys on dockets matching the JD court query. "
            "That does not prove current employment, specialty, years of experience, location, "
            "California Bar standing, or interest in a new role. Verify every lead before use."
        ),
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
