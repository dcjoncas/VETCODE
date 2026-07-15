import os
from typing import Any

import requests


BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_TIMEOUT = 15


class BraveSearchError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def configured() -> bool:
    return bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())


def _api_key() -> str:
    value = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not value:
        raise BraveSearchError("Brave Search is not configured on this environment.", 503)
    return value


def _error_message(status_code: int) -> str:
    messages = {
        400: "Brave Search rejected the search criteria.",
        401: "Brave Search rejected the configured API key.",
        402: "Brave Search credits are unavailable for this request.",
        403: "Brave Search access is not enabled for this key.",
        422: "Brave Search could not process the query.",
        429: "Brave Search rate limit reached. Try again shortly.",
    }
    if status_code in messages:
        return messages[status_code]
    if status_code >= 500:
        return "Brave Search is temporarily unavailable."
    return f"Brave Search failed with status {status_code}."


def search_web(query: str, size: int = 10, page: int = 0) -> dict[str, Any]:
    clean_query = " ".join(str(query or "").split()).strip()
    if not clean_query:
        raise BraveSearchError("Add a public-web search query.", 400)
    if len(clean_query) > 500:
        raise BraveSearchError("The Brave Search query is too long.", 400)
    page_size = max(1, min(int(size or 10), 20))
    page_number = max(0, min(int(page or 0), 9))

    try:
        response = requests.get(
            BRAVE_SEARCH_URL,
            params={
                "q": clean_query,
                "count": page_size,
                "offset": page_number,
                "country": "us",
                "search_lang": "en",
                "safesearch": "moderate",
                "spellcheck": "true",
                "text_decorations": "false",
                "result_filter": "web",
            },
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": _api_key(),
            },
            timeout=BRAVE_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise BraveSearchError("Brave Search timed out before returning results.") from exc
    except requests.RequestException as exc:
        raise BraveSearchError("Brave Search could not be reached.") from exc

    if response.status_code != 200:
        raise BraveSearchError(_error_message(response.status_code), response.status_code)

    try:
        result = response.json()
    except ValueError as exc:
        raise BraveSearchError("Brave Search returned an invalid response.") from exc
    if not isinstance(result, dict):
        raise BraveSearchError("Brave Search returned an unexpected response.")

    web = result.get("web") if isinstance(result.get("web"), dict) else {}
    rows = web.get("results") if isinstance(web.get("results"), list) else []
    rows = [row for row in rows if isinstance(row, dict)][:page_size]
    query_meta = result.get("query") if isinstance(result.get("query"), dict) else {}
    has_more = bool(query_meta.get("more_results_available")) and page_number < 9
    return {
        "status": 200,
        "data": rows,
        "total": page_number * page_size + len(rows) + (1 if has_more else 0),
        "page": page_number,
        "page_size": page_size,
        "has_more": has_more,
        "next_page": page_number + 1 if has_more else None,
        "query": clean_query,
        "requests_used": 1,
    }
