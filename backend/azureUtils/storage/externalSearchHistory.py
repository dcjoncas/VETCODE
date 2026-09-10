import hashlib
import json
import re
from datetime import date, datetime, timezone
from copy import deepcopy
from urllib.parse import urlparse

import azureUtils.storage.client as client


_TABLES_READY = False

# Only server-produced candidate enrichment may be merged into an existing
# result. Search inputs, source audit, billing totals and other rows stay intact.
_ENRICHMENT_FIELDS = frozenset({
    "source_label", "name", "email", "phone", "title", "company", "location",
    "profile_url", "professional_profile_url", "avatar_url", "summary", "skills",
    "years_experience", "years_experience_known", "job_last_verified", "contact",
    "profile_data", "verification", "external_enrichment", "profile_validation",
    "professional_enrichment_complete", "enrichment_provider", "match_pending",
    "score", "match", "saved_match", "match_band", "score_details", "top_matches",
})


def candidate_archive_identity(candidate: dict):
    """Match by provider identity, never by a potentially ambiguous name."""
    if not isinstance(candidate, dict):
        return None
    source = str(candidate.get("source") or "").strip().lower()
    if source not in {"pdl", "coresignal", "courtlistener"}:
        return None
    source_id = str(candidate.get("source_id") or "").strip()
    if source_id:
        return source, "id", source_id
    value = str(candidate.get("profile_url") or "").strip()
    try:
        parsed = urlparse(value if "://" in value else "https://" + value)
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.hostname not in {"linkedin.com", "www.linkedin.com"}
                or not parsed.path.lower().startswith("/in/") or not parsed.path[4:].strip("/")):
            return None
        return source, "linkedin", parsed.path.rstrip("/").lower()
    except ValueError:
        return None


def preserve_candidate_enrichment(search_id, domain: str, original: dict, enriched: dict) -> int:
    """Update matching existing result rows atomically; never create a search."""
    identity = candidate_archive_identity(original)
    if identity is None:
        raise ValueError("An archived provider identity is required.")
    patch = {key: deepcopy(value) for key, value in enriched.items() if key in _ENRICHMENT_FIELDS}
    # An enrichment changes its evidence. Do not retain an old fit percentage.
    if patch.get("match_pending") is True:
        patch.update(score=None, match=None, saved_match=None)
    ensure_tables()
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COALESCE(parent_id, id) FROM external_search_history WHERE id = %s AND domain = %s",
            (int(search_id), domain),
        )
        root = cur.fetchone()
        if not root:
            raise ValueError("The saved search is no longer available in this workspace.")
        root_id = int(root[0])
        cur.execute(
            "SELECT id, response_payload FROM external_search_history "
            "WHERE domain = %s AND (id = %s OR parent_id = %s) ORDER BY id FOR UPDATE",
            (domain, root_id, root_id),
        )
        changed = 0
        for row_id, stored in cur.fetchall():
            response = deepcopy(stored) if isinstance(stored, dict) else json.loads(stored or "{}")
            results = response.get("results")
            if not isinstance(results, list):
                continue
            row_changed = False
            for index, candidate in enumerate(results):
                if candidate_archive_identity(candidate) == identity:
                    results[index] = {**candidate, **deepcopy(patch)}
                    row_changed = True
                    changed += 1
            if row_changed:
                cur.execute(
                    "UPDATE external_search_history SET response_payload = %s::jsonb WHERE id = %s AND domain = %s",
                    (json.dumps(response, default=str), row_id, domain),
                )
        if not changed:
            raise ValueError("The candidate is no longer in this saved search.")
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_part(value, limit: int = 90) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return clean[:limit] or "Not_Found"


def build_query_name(jd_name: str = "", client_name: str = "", query_date=None) -> str:
    if isinstance(query_date, datetime):
        query_date = query_date.date()
    if not isinstance(query_date, date):
        query_date = datetime.now(timezone.utc).date()
    date_part = query_date.isoformat()
    return f"{_safe_part(jd_name)}_{_safe_part(client_name)}_{date_part}_QRY"


def query_cache_key(payload: dict) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS external_search_history (
              id BIGSERIAL PRIMARY KEY,
              parent_id BIGINT REFERENCES external_search_history(id) ON DELETE CASCADE,
              cache_key VARCHAR(64) NOT NULL UNIQUE,
              query_name VARCHAR(320) NOT NULL,
              domain VARCHAR(32) NOT NULL,
              source VARCHAR(64) NOT NULL,
              query_mode VARCHAR(64) NOT NULL,
              jd_id VARCHAR(100),
              jd_name TEXT,
              client_name TEXT,
              query_payload JSONB NOT NULL,
              response_payload JSONB NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              last_opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              reuse_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_external_search_history_domain_created "
            "ON external_search_history(domain, created_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_external_search_history_parent "
            "ON external_search_history(parent_id, id)"
        )
        conn.commit()
        _TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_metadata(row, cache_hit: bool = False) -> dict:
    return {
        "id": row[0],
        "rootId": row[1] or row[0],
        "queryName": row[2],
        "domain": row[3],
        "source": row[4],
        "queryMode": row[5],
        "jdId": row[6] or "",
        "jdName": row[7] or "",
        "clientName": row[8] or "",
        "createdAt": row[9].isoformat() if row[9] else "",
        "lastOpenedAt": row[10].isoformat() if row[10] else "",
        "reuseCount": int(row[11] or 0),
        "cacheHit": cache_hit,
    }


def get_cached_search(cache_key: str):
    ensure_tables()
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE external_search_history
            SET last_opened_at = NOW(), reuse_count = reuse_count + 1
            WHERE cache_key = %s
            RETURNING id, parent_id, query_name, domain, source, query_mode,
                      jd_id, jd_name, client_name, created_at, last_opened_at,
                      reuse_count, response_payload
            """,
            (cache_key,),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        payload = row[12] if isinstance(row[12], dict) else json.loads(row[12] or "{}")
        return {"metadata": _row_metadata(row[:12], cache_hit=True), "response": payload}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_search(
    *,
    cache_key: str,
    query_name: str,
    domain: str,
    source: str,
    query_mode: str,
    jd_id: str = "",
    jd_name: str = "",
    client_name: str = "",
    query_payload: dict,
    response_payload: dict,
    parent_id=None,
) -> dict:
    ensure_tables()
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        clean_parent_id = int(parent_id) if str(parent_id or "").isdigit() else None
        if clean_parent_id:
            cur.execute(
                "SELECT COALESCE(parent_id, id), query_name FROM external_search_history WHERE id = %s AND domain = %s",
                (clean_parent_id, domain),
            )
            parent_row = cur.fetchone()
            if parent_row:
                clean_parent_id = int(parent_row[0])
                query_name = str(parent_row[1] or query_name)
            else:
                clean_parent_id = None
        cur.execute(
            """
            INSERT INTO external_search_history (
              parent_id, cache_key, query_name, domain, source, query_mode,
              jd_id, jd_name, client_name, query_payload, response_payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (cache_key) DO UPDATE
            SET last_opened_at = NOW(), reuse_count = external_search_history.reuse_count + 1
            RETURNING id, parent_id, query_name, domain, source, query_mode,
                      jd_id, jd_name, client_name, created_at, last_opened_at, reuse_count
            """,
            (
                clean_parent_id,
                cache_key,
                query_name,
                domain,
                source,
                query_mode,
                str(jd_id or ""),
                str(jd_name or ""),
                str(client_name or ""),
                json.dumps(query_payload or {}, default=str),
                json.dumps(response_payload or {}, default=str),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _row_metadata(row, cache_hit=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_searches(domain: str, limit: int = 100, offset: int = 0) -> list[dict]:
    ensure_tables()
    safe_limit = max(1, min(int(limit or 100), 500))
    safe_offset = max(0, int(offset or 0))
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT h.id, h.parent_id, h.query_name, h.domain, h.source, h.query_mode,
                   h.jd_id, h.jd_name, h.client_name, h.created_at, h.last_opened_at,
                   h.reuse_count,
                   COALESCE(jsonb_array_length(h.response_payload->'results'), 0)
                     + COALESCE((
                       SELECT SUM(jsonb_array_length(c.response_payload->'results'))
                       FROM external_search_history c
                       WHERE c.parent_id = h.id
                     ), 0) AS record_count,
                   1 + (SELECT COUNT(*) FROM external_search_history c WHERE c.parent_id = h.id) AS page_count
            FROM external_search_history h
            WHERE h.domain = %s AND h.parent_id IS NULL
            ORDER BY h.created_at DESC, h.id DESC
            LIMIT %s
            OFFSET %s
            """,
            (domain, safe_limit, safe_offset),
        )
        searches = []
        for row in cur.fetchall():
            item = _row_metadata(row[:12])
            item["recordCount"] = int(row[12] or 0)
            item["pageCount"] = int(row[13] or 1)
            searches.append(item)
        return searches
    finally:
        conn.close()


def count_searches(domain: str) -> int:
    ensure_tables()
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM external_search_history WHERE domain = %s AND parent_id IS NULL",
            (domain,),
        )
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def get_search_group(search_id, domain: str):
    ensure_tables()
    try:
        clean_id = int(search_id)
    except (TypeError, ValueError):
        return None
    conn = client.getConnection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COALESCE(parent_id, id) FROM external_search_history WHERE id = %s AND domain = %s",
            (clean_id, domain),
        )
        root_row = cur.fetchone()
        if not root_row:
            return None
        root_id = int(root_row[0])
        cur.execute(
            """
            SELECT id, parent_id, query_name, domain, source, query_mode,
                   jd_id, jd_name, client_name, created_at, last_opened_at,
                   reuse_count, query_payload, response_payload
            FROM external_search_history
            WHERE domain = %s AND (id = %s OR parent_id = %s)
            ORDER BY CASE WHEN id = %s THEN 0 ELSE 1 END, id
            """,
            (domain, root_id, root_id, root_id),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        pages = []
        for row in rows:
            query_payload = row[12] if isinstance(row[12], dict) else json.loads(row[12] or "{}")
            response_payload = row[13] if isinstance(row[13], dict) else json.loads(row[13] or "{}")
            pages.append(
                {
                    "metadata": _row_metadata(row[:12]),
                    "query": query_payload,
                    "response": response_payload,
                }
            )
        return {"metadata": {**pages[0]["metadata"], "rootId": root_id}, "pages": pages}
    finally:
        conn.close()
