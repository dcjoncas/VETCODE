# storage.py
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List

def new_id(prefix: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{ts}_{uuid.uuid4().hex[:8]}"

def _conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = set()
    for r in cur.fetchall():
        cols.add((r["name"] if isinstance(r, sqlite3.Row) else r[1]))
    return cols

def _ensure_column(conn: sqlite3.Connection, table: str, col: str, coltype: str):
    cols = _table_columns(conn, table)
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

def init_db(db_path: str):
    conn = _conn(db_path)
    cur = conn.cursor()

    # --- Create tables if missing (DO NOT drop anything) ---
    cur.execute("""
    CREATE TABLE IF NOT EXISTS profiles (
      profile_id TEXT PRIMARY KEY,
      domain TEXT,
      full_name TEXT,
      email TEXT,
      created_at TEXT,
      updated_at TEXT,
      data_json TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jds (
      jd_id TEXT PRIMARY KEY,
      domain TEXT,
      company TEXT,
      title TEXT,
      created_at TEXT,
      updated_at TEXT,
      jd_text TEXT,
      jd_skills_json TEXT
    )
    """)

    # --- Backward/forward compat: add missing columns if DB is older ---
    # If their old DB had jds.jd_skills (legacy), we keep it and also add jd_skills_json if missing.
    _ensure_column(conn, "jds", "domain", "TEXT")
    _ensure_column(conn, "jds", "company", "TEXT")
    _ensure_column(conn, "jds", "title", "TEXT")
    _ensure_column(conn, "jds", "created_at", "TEXT")
    _ensure_column(conn, "jds", "updated_at", "TEXT")
    _ensure_column(conn, "jds", "jd_text", "TEXT")
    _ensure_column(conn, "jds", "jd_skills_json", "TEXT")  # new column (safe if already exists)

    _ensure_column(conn, "profiles", "domain", "TEXT")
    _ensure_column(conn, "profiles", "full_name", "TEXT")
    _ensure_column(conn, "profiles", "email", "TEXT")
    _ensure_column(conn, "profiles", "created_at", "TEXT")
    _ensure_column(conn, "profiles", "updated_at", "TEXT")
    _ensure_column(conn, "profiles", "data_json", "TEXT")

    # Indexes (safe)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profiles_domain ON profiles(domain)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profiles_email ON profiles(email)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jds_domain ON jds(domain)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jds_created ON jds(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jds_updated ON jds(updated_at)")

    conn.commit()
    conn.close()

def upsert_profile(db_path: str, profile: dict):
    meta = profile.get("meta", {}) or {}
    contact = profile.get("contact", {}) or {}

    profile_id = meta.get("profile_id") or profile.get("profile_id") or new_id("PRF")
    meta["profile_id"] = profile_id
    profile["meta"] = meta

    domain = (meta.get("domain") or profile.get("domain") or "technology") or "technology"
    full_name = contact.get("full_name") or contact.get("name") or ""
    email = contact.get("email") or ""

    now = datetime.utcnow().isoformat() + "Z"

    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT profile_id FROM profiles WHERE profile_id=?", (profile_id,))
    exists = cur.fetchone() is not None

    if exists:
        cur.execute("""
        UPDATE profiles
        SET domain=?, full_name=?, email=?, updated_at=?, data_json=?
        WHERE profile_id=?
        """, (domain, full_name, email, now, json.dumps(profile), profile_id))
    else:
        cur.execute("""
        INSERT INTO profiles (profile_id, domain, full_name, email, created_at, updated_at, data_json)
        VALUES (?,?,?,?,?,?,?)
        """, (profile_id, domain, full_name, email, now, now, json.dumps(profile)))

    conn.commit()
    conn.close()

def list_profiles(db_path: str, domain: Optional[str] = "technology"):
    conn = _conn(db_path)
    cur = conn.cursor()

    # If domain filter yields none, fall back to all (so you never "lose" data in UI)
    if domain is None:
        cur.execute("SELECT profile_id, domain, full_name, email, created_at, updated_at FROM profiles ORDER BY COALESCE(updated_at, created_at) DESC")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    cur.execute("SELECT profile_id, domain, full_name, email, created_at, updated_at FROM profiles WHERE COALESCE(domain,'')=? ORDER BY COALESCE(updated_at, created_at) DESC", (domain,))
    rows = cur.fetchall()
    if len(rows) == 0:
        cur.execute("SELECT profile_id, domain, full_name, email, created_at, updated_at FROM profiles ORDER BY COALESCE(updated_at, created_at) DESC")
        rows = cur.fetchall()

    conn.close()
    return [dict(r) for r in rows]

def get_profile(db_path: str, profile_id: str) -> Optional[dict]:
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT data_json FROM profiles WHERE profile_id=?", (profile_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except Exception:
        return None

def upsert_jd(
    db_path: str,
    jd_id: str,
    company: str,
    title: str,
    domain: str,
    created_at: str,
    jd_text: str,
    jd_skills: Any
):
    domain = (domain or "technology") or "technology"
    now = datetime.utcnow().isoformat() + "Z"

    conn = _conn(db_path)
    cur = conn.cursor()

    cols = _table_columns(conn, "jds")
    legacy_has_jd_skills = ("jd_skills" in cols)  # older DBs sometimes used jd_skills TEXT

    cur.execute("SELECT jd_id FROM jds WHERE jd_id=?", (jd_id,))
    exists = cur.fetchone() is not None

    skills_json = json.dumps(jd_skills) if not isinstance(jd_skills, str) else jd_skills

    if exists:
        cur.execute("""
        UPDATE jds
        SET domain=?, company=?, title=?, updated_at=?, jd_text=?, jd_skills_json=?
        WHERE jd_id=?
        """, (domain, company, title, now, jd_text, skills_json, jd_id))

        if legacy_has_jd_skills:
            cur.execute("UPDATE jds SET jd_skills=? WHERE jd_id=?", (skills_json, jd_id))
    else:
        cur.execute("""
        INSERT INTO jds (jd_id, domain, company, title, created_at, updated_at, jd_text, jd_skills_json)
        VALUES (?,?,?,?,?,?,?,?)
        """, (jd_id, domain, company, title, created_at, now, jd_text, skills_json))

        if legacy_has_jd_skills:
            cur.execute("UPDATE jds SET jd_skills=? WHERE jd_id=?", (skills_json, jd_id))

    conn.commit()
    conn.close()

def list_jds(db_path: str, domain: Optional[str] = "technology"):
    conn = _conn(db_path)
    cur = conn.cursor()

    # robust selection even if some columns are NULL/empty
    def _fetch(where_domain: Optional[str]):
        if where_domain is None:
            cur.execute("""
                SELECT jd_id,
                       domain,
                       company,
                       title,
                       created_at,
                       updated_at
                FROM jds
                ORDER BY COALESCE(updated_at, created_at) DESC
            """)
        else:
            cur.execute("""
                SELECT jd_id,
                       domain,
                       company,
                       title,
                       created_at,
                       updated_at
                FROM jds
                WHERE COALESCE(domain,'')=?
                ORDER BY COALESCE(updated_at, created_at) DESC
            """, (where_domain,))
        return cur.fetchall()

    if domain is None:
        rows = _fetch(None)
        conn.close()
        return [dict(r) for r in rows]

    rows = _fetch(domain)

    # If filtered list is empty but we *do* have JDs overall, fall back to all so UI shows them.
    if len(rows) == 0:
        rows_all = _fetch(None)
        rows = rows_all

    conn.close()
    return [dict(r) for r in rows]

def get_jd(db_path: str, jd_id: str) -> Optional[dict]:
    conn = _conn(db_path)
    cur = conn.cursor()

    cols = _table_columns(conn, "jds")
    legacy_has_jd_skills = ("jd_skills" in cols)

    cur.execute("SELECT * FROM jds WHERE jd_id=?", (jd_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None

    jd = dict(row)

    # Load skills from newest column, else legacy, else empty
    raw = jd.get("jd_skills_json")
    if (not raw) and legacy_has_jd_skills:
        raw = jd.get("jd_skills")

    try:
        jd["jd_skills"] = json.loads(raw) if raw else {}
    except Exception:
        jd["jd_skills"] = {}

    # Normalize output keys
    jd.pop("jd_skills_json", None)
    if legacy_has_jd_skills:
        jd.pop("jd_skills", None)  # keep only parsed jd["jd_skills"] above
        jd["jd_skills"] = jd.get("jd_skills", {})  # no-op safety

    return jd

def get_latest_jd(db_path: str, domain: str = "technology") -> Optional[dict]:
    conn = _conn(db_path)
    cur = conn.cursor()

    # Prefer matching domain; if none found, fall back to any JD
    cur.execute("SELECT jd_id FROM jds WHERE COALESCE(domain,'')=? ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 1", (domain,))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT jd_id FROM jds ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 1")
        row = cur.fetchone()

    conn.close()
    if not row:
        return None
    return get_jd(db_path, row["jd_id"])
