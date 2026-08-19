import base64
import datetime as dt
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env", override=False)

if os.getenv("GOOGLE_ALLOW_INSECURE_HTTP", "").strip() == "1":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

if os.getenv("ALLOW_OUTBOUND_PROXY", "").strip() != "1":
    for proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(proxy_var, None)

router = APIRouter(tags=["calendar"])

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]
GOOGLE_TOKEN_FILE = Path(os.getenv("GOOGLE_TOKEN_FILE", str(BASE_DIR / "google_token.json")))
DEVREADY_LOCAL_CONFIG_DIR = Path(
    os.getenv("DEVREADY_LOCAL_CONFIG_DIR", str(Path.home() / ".devready"))
).expanduser()
GOOGLE_CLIENT_SECRET_FILE = Path(
    os.getenv("GOOGLE_CLIENT_SECRET_FILE", str(DEVREADY_LOCAL_CONFIG_DIR / "google_client_secret.json"))
).expanduser()
GOOGLE_REDIRECT_PATH = os.getenv("GOOGLE_REDIRECT_PATH", "/auth/google/callback").strip() or "/auth/google/callback"
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

OUTLOOK_TOKEN_FILE = Path(os.getenv("OUTLOOK_TOKEN_FILE", str(BASE_DIR / "outlook_token.json")))
OUTLOOK_TOKEN_DIR = Path(os.getenv("OUTLOOK_TOKEN_DIR", str(BASE_DIR / "calendar_tokens" / "outlook")))
OUTLOOK_STATE_DIR = Path(os.getenv("OUTLOOK_STATE_DIR", str(BASE_DIR / "calendar_tokens" / "state")))
CALENDAR_SESSION_COOKIE = os.getenv("CALENDAR_SESSION_COOKIE", "devready_calendar_session")
OUTLOOK_TENANT_ID = (
    os.getenv("OUTLOOK_TENANT_ID")
    or os.getenv("AZURE_TENANT_ID")
    or os.getenv("AZURE_TENNANT_ID")
    or "common"
).strip() or "common"
OUTLOOK_AUTHORITY = f"https://login.microsoftonline.com/{OUTLOOK_TENANT_ID}/oauth2/v2.0"
OUTLOOK_SCOPES = ["offline_access", "User.Read", "Calendars.ReadWrite", "Mail.Send"]
OUTLOOK_CLIENT_ID = os.getenv("OUTLOOK_CLIENT_ID", os.getenv("LOGIN_CLIENT_ID", "")).strip()
OUTLOOK_CLIENT_SECRET = os.getenv("OUTLOOK_CLIENT_SECRET", os.getenv("LOGIN_CLIENT_SECRET", "")).strip()
OUTLOOK_REDIRECT_PATH = os.getenv("OUTLOOK_REDIRECT_PATH", "/auth/outlook/callback").strip() or "/auth/outlook/callback"
OUTLOOK_REDIRECT_URI = os.getenv("OUTLOOK_REDIRECT_URI", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
SCHEDULER_PAGE = "/ui/pages/schedule-interview.html"
DEVREADY_MEETING_AGENT_NAME = os.getenv("DEVREADY_MEETING_AGENT_NAME", "Egeria").strip() or "Egeria"
DEVREADY_MEETING_AGENT_EMAIL = (
    os.getenv("DEVREADY_MEETING_AGENT_EMAIL", "egeria@devready.ai").strip().lower()
    or "egeria@devready.ai"
)


def _meeting_agent() -> Dict[str, str]:
    return {
        "name": DEVREADY_MEETING_AGENT_NAME,
        "email": DEVREADY_MEETING_AGENT_EMAIL,
        "role": "DevReady Meeting Agent",
    }


def _request_error_detail(prefix: str, exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        body = (getattr(response, "text", "") or "").strip()
        if body:
            return f"{prefix}: {body[:800]}"
    return f"{prefix}: {exc}"


def _base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return f"{request.url.scheme}://{request.url.netloc}"


def _url_for_path(request: Request, path: str) -> str:
    clean_path = path if path.startswith("/") else f"/{path}"
    return f"{_base_url(request)}{clean_path}"


def _public_url_for(request: Request, route_name: str) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return f"{configured}{request.app.url_path_for(route_name)}"
    return str(request.url_for(route_name))


def _google_redirect_uri(request: Request) -> str:
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    return _url_for_path(request, GOOGLE_REDIRECT_PATH)


def _configured_google_redirect_uri() -> str:
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    clean_path = GOOGLE_REDIRECT_PATH if GOOGLE_REDIRECT_PATH.startswith("/") else f"/{GOOGLE_REDIRECT_PATH}"
    return f"{base_url}{clean_path}"


def _outlook_redirect_uri(request: Request) -> str:
    return OUTLOOK_REDIRECT_URI or _url_for_path(request, OUTLOOK_REDIRECT_PATH)


def _configured_outlook_redirect_uri() -> str:
    if OUTLOOK_REDIRECT_URI:
        return OUTLOOK_REDIRECT_URI
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    clean_path = OUTLOOK_REDIRECT_PATH if OUTLOOK_REDIRECT_PATH.startswith("/") else f"/{OUTLOOK_REDIRECT_PATH}"
    return f"{base_url}{clean_path}"


def _looks_like_guid(value: str) -> bool:
    parts = value.strip().split("-")
    return (
        len(parts) == 5
        and [len(part) for part in parts] == [8, 4, 4, 4, 12]
        and all(part.replace("-", "").isalnum() for part in parts)
    )


def _valid_session_id(value: str) -> bool:
    return bool(value) and len(value) <= 80 and all(ch.isalnum() or ch in {"-", "_"} for ch in value)


def _google_secret_candidates() -> list[Path]:
    candidates = [
        GOOGLE_CLIENT_SECRET_FILE,
        DEVREADY_LOCAL_CONFIG_DIR / "google_client_secret.json",
        BASE_DIR / "google_client_secret.json",
        BASE_DIR.parent / "google_client_secret.json",
    ]
    appdata = os.getenv("APPDATA", "").strip()
    if appdata:
        candidates.append(Path(appdata) / "DevReady" / "google_client_secret.json")

    unique: list[Path] = []
    seen = set()
    for path in candidates:
        resolved_key = str(path.expanduser())
        if resolved_key not in seen:
            seen.add(resolved_key)
            unique.append(path.expanduser())
    return unique


def _google_secret_path() -> Optional[Path]:
    for path in _google_secret_candidates():
        if path.exists():
            return path
    return None


def _google_secret_configured() -> bool:
    return bool(
        _google_secret_path()
        or os.getenv("GOOGLE_CLIENT_SECRET_JSON_B64", "").strip()
        or os.getenv("GOOGLE_CLIENT_SECRET_JSON", "").strip()
    )


def _calendar_session_id(request: Request) -> str:
    session_id = request.cookies.get(CALENDAR_SESSION_COOKIE, "")
    if _valid_session_id(session_id):
        return session_id
    return secrets.token_urlsafe(24)


def _set_calendar_session_cookie(response, session_id: str):
    response.set_cookie(
        CALENDAR_SESSION_COOKIE,
        session_id,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response


def _outlook_token_path(session_id: Optional[str] = None) -> Path:
    if session_id and _valid_session_id(session_id):
        OUTLOOK_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        return OUTLOOK_TOKEN_DIR / f"{session_id}.json"
    return OUTLOOK_TOKEN_FILE


def _save_outlook_state(state: str, session_id: str):
    OUTLOOK_STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "expires_at": dt.datetime.now(dt.timezone.utc).timestamp() + 600,
    }
    (OUTLOOK_STATE_DIR / f"{state}.json").write_text(json.dumps(payload), encoding="utf-8")


def _pop_outlook_state(state: str) -> Optional[str]:
    if not _valid_session_id(state):
        return None
    state_file = OUTLOOK_STATE_DIR / f"{state}.json"
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        state_file.unlink(missing_ok=True)
        if dt.datetime.now(dt.timezone.utc).timestamp() > float(payload.get("expires_at", 0)):
            return None
        session_id = payload.get("session_id", "")
        return session_id if _valid_session_id(session_id) else None
    except Exception:
        return None


def _ensure_google_secret_file() -> Path:
    existing_path = _google_secret_path()
    if existing_path:
        try:
            json.loads(existing_path.read_text(encoding="utf-8"))
            return existing_path
        except Exception:
            pass
    secret_b64 = os.getenv("GOOGLE_CLIENT_SECRET_JSON_B64", "").strip()
    if secret_b64:
        try:
            parsed_secret = json.loads(base64.b64decode(secret_b64).decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Google OAuth client secret base64 JSON is invalid.") from exc
        if not isinstance(parsed_secret, dict):
            raise HTTPException(status_code=500, detail="Google OAuth client secret base64 JSON is invalid.")
        GOOGLE_CLIENT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOOGLE_CLIENT_SECRET_FILE.write_text(json.dumps(parsed_secret), encoding="utf-8")
        return GOOGLE_CLIENT_SECRET_FILE
    secret_json = os.getenv("GOOGLE_CLIENT_SECRET_JSON", "").strip()
    if secret_json:
        try:
            parsed_secret = json.loads(secret_json)
        except json.JSONDecodeError:
            try:
                parsed_secret = json.loads(json.loads(secret_json))
            except Exception as exc:
                raise HTTPException(status_code=500, detail="Google OAuth client secret JSON is invalid.") from exc
        if not isinstance(parsed_secret, dict):
            raise HTTPException(status_code=500, detail="Google OAuth client secret JSON is invalid.")
        GOOGLE_CLIENT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOOGLE_CLIENT_SECRET_FILE.write_text(json.dumps(parsed_secret), encoding="utf-8")
        return GOOGLE_CLIENT_SECRET_FILE
    raise HTTPException(
        status_code=500,
        detail=(
            "Missing Google OAuth client secrets. Set GOOGLE_CLIENT_SECRET_JSON or "
            "GOOGLE_CLIENT_SECRET_FILE, or save google_client_secret.json in "
            f"{DEVREADY_LOCAL_CONFIG_DIR}."
        ),
    )


def _allow_loopback_http(request: Request):
    if request.url.hostname in {"127.0.0.1", "localhost"}:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _load_google_creds() -> Optional[Credentials]:
    if GOOGLE_TOKEN_FILE.exists():
        return Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), GOOGLE_SCOPES)
    return None


def _save_google_creds(creds: Credentials):
    GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def _google_status(refresh: bool = False) -> Dict[str, Any]:
    creds = _load_google_creds()
    if not creds:
        return {"connected": False, "token_found": False, "needs_auth": True}
    if refresh and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            _save_google_creds(creds)
        except Exception as exc:
            return {"connected": False, "token_found": True, "needs_auth": True, "error": f"Google refresh failed: {exc}"}
    return {"connected": bool(creds.valid), "token_found": True, "expired": bool(creds.expired), "needs_auth": not creds.valid}


def _google_service():
    status = _google_status(refresh=True)
    if not status.get("connected"):
        raise HTTPException(status_code=401, detail=status.get("error") or "Google Calendar is not connected.")
    return build("calendar", "v3", credentials=_load_google_creds())


def _ensure_outlook_config():
    if not OUTLOOK_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Missing OUTLOOK_CLIENT_ID.")
    if not OUTLOOK_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Missing OUTLOOK_CLIENT_SECRET.")
    if _looks_like_guid(OUTLOOK_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Use the Outlook client secret Value, not the Secret ID.")


def _load_outlook_token(session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    token_file = _outlook_token_path(session_id)
    if not token_file.exists():
        return None
    try:
        return json.loads(token_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_outlook_token(token: Dict[str, Any], session_id: Optional[str] = None):
    _outlook_token_path(session_id).write_text(json.dumps(token, indent=2), encoding="utf-8")


def _outlook_status(refresh: bool = False, session_id: Optional[str] = None) -> Dict[str, Any]:
    token = _load_outlook_token(session_id)
    if not token:
        return {"connected": False, "token_found": False, "needs_auth": True}
    expires_at = float(token.get("expires_at", 0))
    expired = dt.datetime.now(dt.timezone.utc).timestamp() > expires_at - 60
    if refresh and expired and token.get("refresh_token"):
        try:
            _ensure_outlook_config()
            resp = requests.post(
                f"{OUTLOOK_AUTHORITY}/token",
                data={
                    "client_id": OUTLOOK_CLIENT_ID,
                    "client_secret": OUTLOOK_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": token["refresh_token"],
                    "scope": " ".join(OUTLOOK_SCOPES),
                },
                timeout=20,
            )
            resp.raise_for_status()
            token = resp.json()
            token["expires_at"] = dt.datetime.now(dt.timezone.utc).timestamp() + int(token.get("expires_in", 3600))
            _save_outlook_token(token, session_id)
            expired = False
        except Exception as exc:
            return {"connected": False, "token_found": True, "needs_auth": True, "error": f"Outlook refresh failed: {exc}"}
    return {"connected": bool(token.get("access_token")) and not expired, "token_found": True, "expired": expired, "needs_auth": expired}


def _outlook_access_token(request: Optional[Request] = None, session_id: Optional[str] = None) -> str:
    session_id = session_id or (os.getenv("OUTLOOK_SEND_SESSION_ID", "").strip() or None)
    if request is not None and not session_id:
        session_id = _calendar_session_id(request)
    status = _outlook_status(refresh=True, session_id=session_id)
    if not status.get("connected"):
        raise HTTPException(status_code=401, detail=status.get("error") or "Outlook is not connected.")
    return _load_outlook_token(session_id)["access_token"]


def _openai_client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(status_code=500, detail="Missing OPENAI_API_KEY.")
    return OpenAI()


def _response_text(resp) -> str:
    if getattr(resp, "output_text", None):
        return resp.output_text
    chunks = []
    for item in getattr(resp, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", "")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


DRAFT_SCHEMA = {
    "name": "InterviewInviteDraft",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 180},
            "attendees": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"email": {"type": "string"}}, "required": ["email"]}},
            "candidate": {"type": "object", "additionalProperties": False, "properties": {"name": {"type": "string"}, "email": {"type": "string"}}, "required": ["name", "email"]},
            "role": {"type": "string"},
            "job_description_summary": {"type": "string"},
            "talking_points": {"type": "array", "items": {"type": "string"}},
            "agenda": {"type": "array", "items": {"type": "string"}},
            "location": {"type": "string"},
            "notes_for_invite": {"type": "string"},
            "email_subject": {"type": "string"},
            "email_body": {"type": "string"},
        },
        "required": ["title", "duration_minutes", "attendees", "candidate", "role", "job_description_summary", "talking_points", "agenda", "location", "notes_for_invite", "email_subject", "email_body"],
    },
}


def _fallback_email(draft: Dict[str, Any]) -> str:
    candidate = draft.get("candidate") or {}
    is_internal_review = "candidate review" in str(draft.get("title") or "").lower()
    meeting_label = "internal DevReady Candidate Review" if is_internal_review else "client interview"
    agenda = "\n".join([f"- {item}" for item in draft.get("agenda", [])])
    agenda_text = agenda or "- Introductions\n- Role discussion\n- Candidate questions\n- Next steps"
    attendees = ", ".join([a.get("email", "") for a in draft.get("attendees", []) if a.get("email")])
    return (
        f"Hi {candidate.get('name') or 'there'},\n\n"
        f"We would like to schedule your {meeting_label} for the {draft.get('role') or 'role'} opportunity.\n\n"
        f"Duration: {draft.get('duration_minutes', 60)} minutes\n"
        f"Location: {draft.get('location') or 'Calendar invite to follow'}\n"
        f"Interview team: {attendees or 'To be confirmed'}\n\n"
        f"Agenda:\n{agenda_text}\n\n"
        "Thanks,\nDevReady"
    )


def _fallback_invite_draft(payload: Dict[str, Any], candidate_name: str, candidate_email: str, attendee_emails: list[str], duration: int, location: str) -> Dict[str, Any]:
    role = (payload.get("role") or "Interview").strip()
    company = (payload.get("company") or "DevReady").strip()
    interview_type = (payload.get("interview_type") or "ready").strip().lower()
    title_suffix = "Client Interview" if interview_type == "client" else "Candidate Review"
    agenda = [
        "Introductions",
        "Role and company context",
        "Candidate questions",
        "Next steps",
    ]
    talking_points = payload.get("talking_points") or []
    if isinstance(talking_points, str):
        talking_points = [row.strip(" -\t") for row in talking_points.splitlines() if row.strip()]
    draft = {
        "title": f"{role} - {title_suffix}",
        "duration_minutes": duration,
        "attendees": [{"email": email} for email in sorted(set(attendee_emails))],
        "candidate": {"name": candidate_name, "email": candidate_email},
        "role": role,
        "job_description_summary": (payload.get("job_description") or f"{company} interview for {role}.")[:900],
        "talking_points": talking_points[:8],
        "agenda": agenda,
        "location": location,
        "notes_for_invite": "Generated by the scheduling fallback because the AI draft service was unavailable.",
        "email_subject": f"{role} - {title_suffix}",
    }
    draft["email_body"] = _fallback_email(draft)
    return draft


@router.get("/api/calendar/health")
def calendar_health(request: Request):
    session_id = _calendar_session_id(request)
    google_secret_path = _google_secret_path()
    payload = {
        "ok": True,
        "google_secret_found": _google_secret_configured(),
        "google_secret_location": str(google_secret_path) if google_secret_path else None,
        "google": {
            **_google_status(refresh=False),
            "redirect_uri": _configured_google_redirect_uri(),
            "redirect_uri_setup_note": "This exact URI must be listed under Authorized redirect URIs in the Google Cloud OAuth client.",
        },
        "outlook": {
            **_outlook_status(refresh=False, session_id=session_id),
            "client_configured": bool(OUTLOOK_CLIENT_ID and OUTLOOK_CLIENT_SECRET),
            "secret_value_needed": _looks_like_guid(OUTLOOK_CLIENT_SECRET) if OUTLOOK_CLIENT_SECRET else False,
            "redirect_uri": _configured_outlook_redirect_uri(),
            "tenant": OUTLOOK_TENANT_ID,
            "required_scopes": OUTLOOK_SCOPES,
            "mail_send_ready_after_reauth": "Mail.Send" in OUTLOOK_SCOPES,
            "session_scoped": True,
        },
        "openai_key_found": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "model": OPENAI_MODEL,
        "meeting_agent": {
            **_meeting_agent(),
            "automatic": True,
            "interview_types": ["ready", "client"],
        },
    }
    return _set_calendar_session_cookie(JSONResponse(payload), session_id)


@router.get("/auth/google")
def auth_google(request: Request):
    google_secret_file = _ensure_google_secret_file()
    _allow_loopback_http(request)
    redirect_uri = _google_redirect_uri(request)
    flow = Flow.from_client_secrets_file(str(google_secret_file), scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
    auth_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    return RedirectResponse(auth_url)


@router.get("/auth/google/callback", name="auth_google_callback")
def auth_google_callback(request: Request):
    google_secret_file = _ensure_google_secret_file()
    _allow_loopback_http(request)
    redirect_uri = _google_redirect_uri(request)
    flow = Flow.from_client_secrets_file(str(google_secret_file), scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
    callback_url = f"{redirect_uri}?{request.url.query}" if request.url.query else redirect_uri
    try:
        flow.fetch_token(authorization_response=callback_url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth callback failed: {exc}")
    _save_google_creds(flow.credentials)
    return RedirectResponse(f"{SCHEDULER_PAGE}?connected=google")


@router.get("/auth/outlook")
def auth_outlook(request: Request):
    _ensure_outlook_config()
    session_id = _calendar_session_id(request)
    state = secrets.token_urlsafe(24)
    _save_outlook_state(state, session_id)
    params = {
        "client_id": OUTLOOK_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _outlook_redirect_uri(request),
        "response_mode": "query",
        "scope": " ".join(OUTLOOK_SCOPES),
        "prompt": "select_account",
        "state": state,
    }
    return _set_calendar_session_cookie(RedirectResponse(f"{OUTLOOK_AUTHORITY}/authorize?{urlencode(params)}"), session_id)


@router.get("/auth/outlook/callback", name="auth_outlook_callback")
def auth_outlook_callback(request: Request):
    _ensure_outlook_config()
    code = request.query_params.get("code")
    state = request.query_params.get("state", "")
    session_id = _pop_outlook_state(state) or _calendar_session_id(request)
    if not code:
        error = request.query_params.get("error_description") or request.query_params.get("error")
        if error:
            return HTMLResponse(f"<h2>Outlook connection failed</h2><p>{error}</p><p><a href='{SCHEDULER_PAGE}'>Back to scheduler</a></p>", status_code=400)
        return _set_calendar_session_cookie(RedirectResponse(f"{SCHEDULER_PAGE}?outlook=missing_code"), session_id)
    try:
        resp = requests.post(
            f"{OUTLOOK_AUTHORITY}/token",
            data={
                "client_id": OUTLOOK_CLIENT_ID,
                "client_secret": OUTLOOK_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _outlook_redirect_uri(request),
                "scope": " ".join(OUTLOOK_SCOPES),
            },
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Outlook token exchange failed: {exc}")
    token = resp.json()
    token["expires_at"] = dt.datetime.now(dt.timezone.utc).timestamp() + int(token.get("expires_in", 3600))
    _save_outlook_token(token, session_id)
    return _set_calendar_session_cookie(RedirectResponse(f"{SCHEDULER_PAGE}?connected=outlook"), session_id)


@router.post("/api/calendar/invite/draft")
async def invite_draft(payload: Dict[str, Any]):
    candidate_email = (payload.get("candidate_email") or "").strip()
    candidate_name = (payload.get("candidate_name") or "").strip() or "Candidate"
    role = (payload.get("role") or "").strip()
    if not candidate_email:
        raise HTTPException(status_code=400, detail="candidate_email is required")
    if not role:
        raise HTTPException(status_code=400, detail="role is required")

    attendee_emails = []
    for item in (payload.get("interviewers") or []) + (payload.get("attendees") or []):
        email = (item.get("email") or "").strip()
        if email:
            attendee_emails.append(email.lower())
    # Egeria is a required participant in both internal Candidate Reviews and
    # client-facing interviews. Enforce this server-side so direct API callers
    # cannot accidentally create an interview without the DevReady agent.
    attendee_emails.append(DEVREADY_MEETING_AGENT_EMAIL)
    attendee_emails = sorted(set(attendee_emails))

    duration = max(15, min(180, int(payload.get("duration_minutes") or 60)))
    talking_points = payload.get("talking_points") or []
    if isinstance(talking_points, str):
        talking_points = [row.strip(" -\t") for row in talking_points.splitlines() if row.strip()]

    location = (payload.get("location") or "Calendar video meeting").strip()
    prompt = f"""
You are an interview scheduling assistant for DevReady. Return only strict JSON matching the schema.

Candidate: {candidate_name} <{candidate_email}>
Interviewers/client attendees (including the automatic DevReady Meeting Agent): {attendee_emails}
Interview type: {payload.get("interview_type") or "ready"}
Ready interview purpose: {payload.get("ready_purpose") or ""}
Role: {role}
Company: {payload.get("company") or ""}
Job description: {payload.get("job_description") or ""}
Talking points: {talking_points}
Context from candidate chat/profile: {payload.get("ai_context") or ""}

Rules:
    - title includes the role and "Candidate Review" for the internal DevReady meeting or "Client Interview" for client-facing interviews.
    - Candidate Review is conducted by internal DevReady staff with the candidate; do not imply the client is attending.
    - Client Interview occurs only after the candidate has completed the internal review and confirmed interest.
- if ready_purpose is "community", job description may be empty; use the role/title, candidate context, and talking points.
- duration_minutes is {duration}.
- attendees contains interviewer/client emails plus the automatic DevReady Meeting Agent, not the candidate.
- email_body is a complete plain-text email ready to send.
- location is exactly "{location}".
"""
    try:
        resp = _openai_client().responses.create(
            model=OPENAI_MODEL,
            input=[{"role": "user", "content": prompt}],
            text={"format": {"type": "json_schema", "name": DRAFT_SCHEMA["name"], "schema": DRAFT_SCHEMA["schema"], "strict": True}},
        )
    except Exception as exc:
        draft = _fallback_invite_draft(payload, candidate_name, candidate_email, attendee_emails, duration, location)
        draft["fallback_reason"] = f"OpenAI draft unavailable: {str(exc)[:240]}"
        return draft

    text = _response_text(resp)
    if not text:
        draft = _fallback_invite_draft(payload, candidate_name, candidate_email, attendee_emails, duration, location)
        draft["fallback_reason"] = "OpenAI returned an empty draft."
        return draft
    try:
        draft = json.loads(text)
    except Exception as exc:
        draft = _fallback_invite_draft(payload, candidate_name, candidate_email, attendee_emails, duration, location)
        draft["fallback_reason"] = f"Could not parse AI draft: {str(exc)[:240]}"
        return draft

    draft["candidate"] = {"name": candidate_name, "email": candidate_email}
    draft["attendees"] = [{"email": email} for email in attendee_emails]
    draft["duration_minutes"] = max(15, min(180, int(draft.get("duration_minutes") or duration)))
    draft.setdefault("title", f"{role} Interview")
    draft.setdefault("role", role)
    draft.setdefault("location", location)
    draft.setdefault("email_subject", draft["title"])
    if not draft.get("email_body"):
        draft["email_body"] = _fallback_email(draft)
    return draft


def _parse_time_window(payload: Dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    start = payload.get("time_window_start_iso")
    end = payload.get("time_window_end_iso")
    if not start or not end:
        raise HTTPException(status_code=400, detail="Start and end time are required.")
    try:
        start_dt = dt.datetime.fromisoformat(_normalize_iso_datetime(start))
        end_dt = dt.datetime.fromisoformat(_normalize_iso_datetime(end))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid time format.")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time.")
    return start_dt, end_dt


def _normalize_iso_datetime(value: str) -> str:
    value = str(value).strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    if "." not in value:
        return value

    dot_index = value.find(".")
    tz_index = len(value)
    for marker in ("+", "-"):
        marker_index = value.find(marker, dot_index)
        if marker_index != -1:
            tz_index = marker_index
            break
    fraction = value[dot_index + 1 : tz_index]
    if len(fraction) <= 6:
        return value
    return f"{value[:dot_index + 1]}{fraction[:6]}{value[tz_index:]}"


def _parse_calendar_datetime(value: str, fallback_tzinfo: Optional[dt.tzinfo]) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(_normalize_iso_datetime(value))
    if parsed.tzinfo is None and fallback_tzinfo is not None:
        parsed = parsed.replace(tzinfo=fallback_tzinfo)
    return parsed


def _overlaps(s1: dt.datetime, e1: dt.datetime, s2: dt.datetime, e2: dt.datetime) -> bool:
    return s1 < e2 and s2 < e1


def _find_slot(start_dt: dt.datetime, end_dt: dt.datetime, duration: dt.timedelta, busy: list[Dict[str, str]]):
    slot_start = start_dt
    while slot_start + duration <= end_dt:
        slot_end = slot_start + duration
        if not any(_overlaps(slot_start, slot_end, _parse_calendar_datetime(row["start"], start_dt.tzinfo), _parse_calendar_datetime(row["end"], start_dt.tzinfo)) for row in busy):
            return slot_start, slot_end
        slot_start += dt.timedelta(minutes=15)
    return None


def _attendees(draft: Dict[str, Any], provider: str):
    rows = []
    candidate = draft.get("candidate") or {}
    if candidate.get("email"):
        rows.append({"email": candidate["email"], "name": candidate.get("name", "")})
    for item in draft.get("attendees") or []:
        if item.get("email"):
            rows.append({"email": item["email"], "name": item.get("name", "")})
    deduped = []
    seen = set()
    for row in rows:
        key = row["email"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    if provider == "outlook":
        return [{"emailAddress": {"address": row["email"], "name": row.get("name") or row["email"]}, "type": "required"} for row in deduped]
    return [{"email": row["email"]} for row in deduped]


def _description(draft: Dict[str, Any]) -> str:
    if draft.get("email_body"):
        return draft["email_body"]
    return _fallback_email(draft)


def _windows_timezone(tz: str) -> str:
    return {
        "America/Denver": "Mountain Standard Time",
        "America/New_York": "Eastern Standard Time",
        "America/Los_Angeles": "Pacific Standard Time",
        "America/Chicago": "Central Standard Time",
        "UTC": "UTC",
    }.get(tz, tz)


def _create_google(payload: Dict[str, Any], draft: Dict[str, Any], start_dt: dt.datetime, end_dt: dt.datetime, tz: str):
    service = _google_service()
    calendar_id = payload.get("calendar_id", "primary")
    duration = dt.timedelta(minutes=int(draft.get("duration_minutes", 60)))
    try:
        freebusy = service.freebusy().query(body={"timeMin": start_dt.isoformat(), "timeMax": end_dt.isoformat(), "timeZone": tz, "items": [{"id": calendar_id}]}).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google availability error: {exc}")
    slot = _find_slot(start_dt, end_dt, duration, (freebusy.get("calendars", {}).get(calendar_id, {}) or {}).get("busy", []))
    if not slot:
        return JSONResponse({"ok": False, "error": "No free slot found in that window."}, status_code=409)
    slot_start, slot_end = slot
    event = {
        "summary": draft.get("title", "DevReady Interview"),
        "location": draft.get("location", "Google Meet"),
        "description": _description(draft),
        "start": {"dateTime": slot_start.isoformat(), "timeZone": tz},
        "end": {"dateTime": slot_end.isoformat(), "timeZone": tz},
        "attendees": _attendees(draft, "google"),
        "conferenceData": {"createRequest": {"requestId": f"devready-{int(dt.datetime.now(dt.timezone.utc).timestamp())}", "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    }
    try:
        created = service.events().insert(calendarId=calendar_id, body=event, conferenceDataVersion=1, sendUpdates="all").execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google create event error: {exc}")
    return {"ok": True, "provider": "google", "eventLink": created.get("htmlLink"), "meetingLink": created.get("hangoutLink"), "start": event["start"], "end": event["end"]}


def _create_outlook(request: Request, draft: Dict[str, Any], start_dt: dt.datetime, end_dt: dt.datetime, tz: str):
    token = _outlook_access_token(request)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": f'outlook.timezone="{_windows_timezone(tz)}"'}
    duration = dt.timedelta(minutes=int(draft.get("duration_minutes", 60)))
    try:
        view = requests.get(
            "https://graph.microsoft.com/v1.0/me/calendarView",
            headers=headers,
            params={"startDateTime": start_dt.isoformat(), "endDateTime": end_dt.isoformat(), "$select": "start,end,showAs", "$top": "100"},
            timeout=20,
        )
        view.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_request_error_detail("Outlook availability error", exc))
    busy = []
    for item in view.json().get("value", []):
        if item.get("showAs") in {"free", "workingElsewhere"}:
            continue
        start_value = (item.get("start") or {}).get("dateTime")
        end_value = (item.get("end") or {}).get("dateTime")
        if start_value and end_value:
            busy.append({"start": start_value, "end": end_value})
    slot = _find_slot(start_dt, end_dt, duration, busy)
    if not slot:
        return JSONResponse({"ok": False, "error": "No free slot found in that window."}, status_code=409)
    slot_start, slot_end = slot
    event = {
        "subject": draft.get("title", "DevReady Interview"),
        "body": {"contentType": "text", "content": _description(draft)},
        "start": {"dateTime": slot_start.isoformat(), "timeZone": _windows_timezone(tz)},
        "end": {"dateTime": slot_end.isoformat(), "timeZone": _windows_timezone(tz)},
        "location": {"displayName": draft.get("location", "Microsoft Teams")},
        "attendees": _attendees(draft, "outlook"),
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
    }
    try:
        created = requests.post("https://graph.microsoft.com/v1.0/me/events", headers=headers, json=event, timeout=20)
        created.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_request_error_detail("Outlook create event error", exc))
    created_json = created.json()
    return {"ok": True, "provider": "outlook", "eventLink": created_json.get("webLink"), "meetingLink": (created_json.get("onlineMeeting") or {}).get("joinUrl"), "start": event["start"], "end": event["end"]}


def _plain_text_to_graph_body(text: str) -> Dict[str, str]:
    return {
        "contentType": "Text",
        "content": (text or "").strip()[:20000],
    }


def _recipient(address: str, name: str = "") -> Dict[str, Dict[str, str]]:
    email = (address or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid recipient email is required.")
    payload = {"address": email}
    if (name or "").strip():
        payload["name"] = name.strip()
    return {"emailAddress": payload}


def _send_outlook_mail(
    request: Optional[Request],
    *,
    to: list[dict] | list[str],
    subject: str,
    body: str,
    cc: list[dict] | list[str] | None = None,
    reply_to: list[dict] | list[str] | None = None,
    save_to_sent_items: bool = True,
) -> Dict[str, Any]:
    token = _outlook_access_token(request)
    clean_subject = (subject or "").strip()[:255]
    clean_body = (body or "").strip()
    if not clean_subject:
        raise HTTPException(status_code=400, detail="Email subject is required.")
    if not clean_body:
        raise HTTPException(status_code=400, detail="Email body is required.")

    def normalize_people(values):
        people = []
        for item in values or []:
            if isinstance(item, str):
                people.append(_recipient(item))
            elif isinstance(item, dict):
                people.append(_recipient(item.get("email") or item.get("address") or "", item.get("name") or ""))
        return people

    message = {
        "subject": clean_subject,
        "body": _plain_text_to_graph_body(clean_body),
        "toRecipients": normalize_people(to),
    }
    if not message["toRecipients"]:
        raise HTTPException(status_code=400, detail="At least one recipient is required.")
    cc_recipients = normalize_people(cc)
    if cc_recipients:
        message["ccRecipients"] = cc_recipients
    reply_recipients = normalize_people(reply_to)
    if reply_recipients:
        message["replyTo"] = reply_recipients

    try:
        sent = requests.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"message": message, "saveToSentItems": save_to_sent_items},
            timeout=20,
        )
        sent.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_request_error_detail("Outlook send mail error", exc))
    return {"ok": True, "provider": "outlook", "status_code": sent.status_code, "accepted": sent.status_code in {200, 202}}


@router.post("/api/outlook/mail/send")
async def outlook_mail_send(request: Request, payload: Dict[str, Any]):
    return _send_outlook_mail(
        request,
        to=payload.get("to") or [],
        cc=payload.get("cc") or [],
        reply_to=payload.get("reply_to") or payload.get("replyTo") or [],
        subject=payload.get("subject") or "",
        body=payload.get("body") or "",
        save_to_sent_items=bool(payload.get("save_to_sent_items", True)),
    )


@router.post("/api/calendar/invite/create")
async def invite_create(request: Request, payload: Dict[str, Any]):
    try:
        draft = payload.get("draft")
        if not isinstance(draft, dict):
            raise HTTPException(status_code=400, detail="draft is required.")
        provider = (payload.get("provider") or "google").strip().lower()
        timezone = payload.get("timezone") or "America/Denver"
        start_dt, end_dt = _parse_time_window(payload)
        if provider == "google":
            return _create_google(payload, draft, start_dt, end_dt, timezone)
        if provider == "outlook":
            return _create_outlook(request, draft, start_dt, end_dt, timezone)
        raise HTTPException(status_code=400, detail="provider must be google or outlook.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calendar invite failed: {exc}")
