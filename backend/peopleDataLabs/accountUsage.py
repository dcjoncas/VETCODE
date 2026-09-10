"""Account-wide PDL balances from documented, non-record-returning HEAD requests.

https://docs.peopledatalabs.com/docs/usage-limits documents HEAD for checking
endpoint credit headers. Lifetime usage is not current billing-period usage.
"""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
import threading
import time

import requests


PDL_USAGE_ENDPOINTS = {
    "search": ("Person Search", "https://api.peopledatalabs.com/v5/person/search"),
    "enrich": ("Person Enrichment", "https://api.peopledatalabs.com/v5/person/enrich"),
}
PDL_USAGE_TIMEOUT = (3, 8)
PDL_USAGE_CACHE_SECONDS = 30
_cache_lock = threading.Lock()
_cache_key = None
_cache_expires = {}
_cache_products = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _integer(value):
    if isinstance(value, bool):
        return None
    text = str(value).strip() if isinstance(value, (str, int)) else ""
    return int(text) if text.isascii() and text.isdigit() else None


def _empty_product(product, error_code=None):
    return {
        "label": PDL_USAGE_ENDPOINTS[product][0],
        "creditType": product,
        "status": "unavailable",
        "accountRemainingCredits": None,
        "purchasedRemainingCredits": None,
        "overageRemainingCredits": None,
        "lifetimeCreditsUsed": None,
        "currentTermUsed": None,
        "currentTermTotal": None,
        "checkCreditsUsed": None,
        "balanceComponentsDiffer": False,
        "checkedAt": _now(),
        "observedAt": None,
        "httpStatus": None,
        "errorCode": error_code,
    }


def _head_product(product, api_key):
    result = _empty_product(product)
    try:
        response = requests.head(
            PDL_USAGE_ENDPOINTS[product][1],
            headers={"Accept": "application/json", "X-Api-Key": api_key},
            timeout=PDL_USAGE_TIMEOUT,
            allow_redirects=False,
        )
    except requests.Timeout:
        result["errorCode"] = "timeout"
        return result
    except requests.RequestException:
        result["errorCode"] = "connection_failed"
        return result

    try:
        status = _integer(response.status_code)
        result["httpStatus"] = status
        result["observedAt"] = _now()
        # A HEAD with no person query can return 400 and valid credit headers.
        # Do not follow redirects or trust balances from rejected credentials.
        if status in {401, 403}:
            result["errorCode"] = "authentication_required"
            return result
        if status is None or 300 <= status < 400 or status >= 500:
            result["errorCode"] = "provider_unavailable"
            return result
        raw = response.headers
        headers = {str(key).lower(): value for key, value in raw.items()} if isinstance(raw, Mapping) else {}
        reported_type = headers.get("x-call-credits-type")
        if reported_type is not None and reported_type != product:
            result["errorCode"] = "credit_type_mismatch"
            return result
        fields = {
            "accountRemainingCredits": "x-totallimit-remaining",
            "purchasedRemainingCredits": "x-totallimit-purchased-remaining",
            "overageRemainingCredits": "x-totallimit-overages-remaining",
            "lifetimeCreditsUsed": "x-lifetime-used",
            "checkCreditsUsed": "x-call-credits-spent",
        }
        result.update({field: _integer(headers.get(header)) for field, header in fields.items()})
        remaining = result["accountRemainingCredits"]
        purchased = result["purchasedRemainingCredits"]
        overage = result["overageRemainingCredits"]
        result["balanceComponentsDiffer"] = (
            remaining is not None and purchased is not None and overage is not None
            and remaining != purchased + overage
        )
        result["status"] = "reported" if remaining is not None else "partial" if any(
            result[field] is not None
            for field in ("purchasedRemainingCredits", "overageRemainingCredits", "lifetimeCreditsUsed")
        ) else "unavailable"
        result["errorCode"] = (
            "credits_exhausted" if status == 402 else "rate_limited" if status == 429
            else "headers_unavailable" if result["status"] == "unavailable" else None
        )
        return result
    finally:
        response.close()


def invalidate_account_usage(product=None):
    """Actual search/enrichment attempts invalidate snapshots, including timeouts."""
    with _cache_lock:
        for key in PDL_USAGE_ENDPOINTS if product is None else (product,):
            if key in PDL_USAGE_ENDPOINTS:
                _cache_expires[key] = 0.0


def get_account_usage():
    """Return a short-lived snapshot; never write credentials or balances to disk."""
    global _cache_key
    api_key = os.getenv("PDL_API_KEY", "").strip()
    if not api_key:
        return {
            "provider": "People Data Labs", "source": "head_response_headers",
            "checkedAt": _now(), "cached": False, "cacheMaxAgeSeconds": PDL_USAGE_CACHE_SECONDS,
            "products": {product: _empty_product(product, "not_configured") for product in PDL_USAGE_ENDPOINTS},
        }
    fingerprint = hashlib.sha256(api_key.encode("utf-8")).digest()
    with _cache_lock:
        if fingerprint != _cache_key:
            _cache_key = fingerprint
            _cache_products.clear()
            _cache_expires.clear()
        stale = [product for product in PDL_USAGE_ENDPOINTS
                 if product not in _cache_products or time.monotonic() >= _cache_expires.get(product, 0)]
        # Product pools and their rate limits are independent. An enrichment must
        # not force an extra Search HEAD, especially during a bulk enrichment.
        if stale:
            with ThreadPoolExecutor(max_workers=2) as pool:
                refreshed = dict(zip(stale, pool.map(lambda product: _head_product(product, api_key), stale)))
            _cache_products.update(refreshed)
            _cache_expires.update({product: time.monotonic() + PDL_USAGE_CACHE_SECONDS for product in stale})
        products = {product: {**deepcopy(_cache_products[product]), "cached": product not in stale}
                    for product in PDL_USAGE_ENDPOINTS}
        return {
            "provider": "People Data Labs", "source": "head_response_headers",
            "checkedAt": max(result["checkedAt"] for result in products.values()),
            "cached": not stale, "cacheMaxAgeSeconds": PDL_USAGE_CACHE_SECONDS,
            "products": products,
        }
