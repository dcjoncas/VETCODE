"""Read-only, workspace-scoped inventory of persisted, confirmed interest."""

import re
from urllib.parse import urlparse

from azureUtils.storage import client
from azureUtils.storage.candidates import splitExternalProfileDescription

DOMAINS = {"dev", "engineer", "law", "dental"}
SCAN_CHUNK = 250
MAX_SCANNED_PER_PAGE = 5000

# Choose the authoritative professional record before reading interest. An older
# interested record must not override a newer not_interested record. Lateral
# one-row joins also prevent duplicate professionals/addresses from paging twice.
PROFILE_PAGE_SQL = """
    SELECT person.id, person.firstname, person.lastname,
           prof.email, prof.title, prof.maindescription, prof.linkedinurl,
           prof.modifieddate, addr.city, addr.state, addr.country
    FROM person
    JOIN LATERAL (
        SELECT email, title, maindescription, linkedinurl, modifieddate
        FROM professional WHERE personid = person.id
        ORDER BY modifieddate DESC NULLS LAST, id DESC LIMIT 1
    ) prof ON TRUE
    LEFT JOIN LATERAL (
        SELECT city, state, country FROM address WHERE personid = person.id
        ORDER BY id DESC LIMIT 1
    ) addr ON TRUE
    WHERE person.domain = %s AND person.id > %s
    ORDER BY person.id ASC LIMIT %s
"""


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _values(value, fields):
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, dict):
            item = next((item.get(key) for key in fields if _text(item.get(key))), "")
        if _text(item):
            yield _text(item)


def _contacts(values, kind):
    found = []
    for value in values:
        if kind == "email":
            valid = bool(re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value))
        else:
            valid = (not re.search(r"[^\d\s+().xX-]", value)
                     and 7 <= len(re.sub(r"\D", "", value)) <= 20)
        if valid and value.lower() not in {existing.lower() for existing in found}:
            found.append(value)
    return found


def _linkedin(*values):
    for value in values:
        raw = _text(value)
        if not raw or re.search(r"\s", raw):
            continue
        try:
            parsed = urlparse(raw if "://" in raw else "https://" + raw)
            host = (parsed.hostname or "").lower()
            if (parsed.scheme in {"https", "http"} and not parsed.username and not parsed.password
                    and (host == "linkedin.com" or host.endswith(".linkedin.com"))
                    and re.match(r"^/(in|pub)/[^/]+", parsed.path)):
                return parsed.geturl()
        except ValueError:
            continue
    return ""


def _profile(row, domain, jd_id):
    description, metadata = splitExternalProfileDescription(row[5])
    interest = metadata.get("interestWorkflow")
    if not isinstance(interest, dict) or _text(interest.get("status")).lower() != "interested":
        return None
    job_id = str(interest.get("jobId") or "").strip()
    if jd_id and job_id != jd_id:
        return None
    contact = metadata.get("contact") if isinstance(metadata.get("contact"), dict) else {}
    email_values = []
    for value in [row[3], contact.get("primaryEmail"), contact.get("workEmail"),
                  contact.get("recommendedPersonalEmail"), contact.get("professionalEmails"),
                  contact.get("personalEmails"), contact.get("emails")]:
        email_values.extend(_values(value, ("address", "email", "value")))
    phone_values = []
    for value in [contact.get("primaryPhone"), contact.get("mobilePhone"),
                  contact.get("phoneNumbers"), contact.get("phones")]:
        phone_values.extend(_values(value, ("number", "phone", "value")))
    emails, phones = _contacts(email_values, "email"), _contacts(phone_values, "phone")
    linkedin = _linkedin(row[6], metadata.get("profileUrl"), contact.get("linkedinUrl"), contact.get("linkedin"))
    # This contact inventory cannot establish whether a saved score matches the
    # latest JD and criteria. Leave scoring to the explicit fit-review workflow.
    return {
        "personid": row[0], "domain": domain,
        "name": " ".join(filter(None, [_text(row[1]), _text(row[2])])) or "Unnamed candidate",
        "title": _text(row[4]), "summary": description[:600],
        "location": ", ".join(filter(None, [_text(row[8]), _text(row[9]), _text(row[10])])),
        "email": emails[0] if emails else "", "emails": emails,
        "phone": phones[0] if phones else "", "phones": phones,
        "linkedinUrl": linkedin, "profileUrl": linkedin,
        "contactAvailable": bool(emails or phones or linkedin),
        "interestStatus": "interested", "interestConfirmedAt": _text(interest.get("confirmedAt")),
        "interestJobId": job_id, "interestJobTitle": "",
        "source": _text(metadata.get("source")) or "Saved profile",
        "temporary": "Temporary external profile" in description,
        "updated": row[7].isoformat() if hasattr(row[7], "isoformat") else _text(row[7]),
        "profileHref": f"profile-preview.html?domain={domain}&profileId={row[0]}",
    }


def list_interested(domain, limit=50, after=0, jd_id=""):
    """Walk latest records, never the TEMP-only capped list. No writes/providers.

    A bounded scan can return an empty page with hasMore=true. Callers continue
    using nextCursor; this never silently truncates a sparse large workspace.
    """
    if domain not in DOMAINS:
        raise ValueError("Select a supported workspace.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise ValueError("Invalid report cursor.")
    if jd_id and not re.fullmatch(r"[1-9]\d{0,17}", jd_id):
        raise ValueError("Job ID must be a positive integer.")
    conn = client.getConnection()
    try:
        cur = conn.cursor()
        profiles, last_id, scanned, has_more = [], after, 0, False
        while scanned < MAX_SCANNED_PER_PAGE:
            cur.execute(PROFILE_PAGE_SQL, (domain, last_id, SCAN_CHUNK))
            rows = cur.fetchall()
            if not rows:
                break
            for row in rows:
                # Defensive duplicate protection; the SQL itself is one per person.
                if int(row[0]) <= last_id:
                    continue
                profile = _profile(row, domain, jd_id)
                if profile is not None and len(profiles) == limit:
                    has_more = True
                    break
                last_id = int(row[0])
                scanned += 1
                if profile is not None:
                    profiles.append(profile)
            if has_more or len(rows) < SCAN_CHUNK:
                break
            if scanned >= MAX_SCANNED_PER_PAGE:
                has_more = True
        job_ids = sorted({int(p["interestJobId"]) for p in profiles
                          if re.fullmatch(r"[1-9]\d{0,17}", p["interestJobId"])})
        if job_ids:
            cur.execute("SELECT id, jobtitle FROM jobdescription WHERE domain = %s AND id = ANY(%s)", (domain, job_ids))
            titles = {str(row[0]): _text(row[1]) for row in cur.fetchall()}
            for profile in profiles:
                profile["interestJobTitle"] = titles.get(profile["interestJobId"], "")
        return {
            "status": "success", "domain": domain, "profiles": profiles,
            "scope": "All saved interested candidates in this workspace",
            "jdId": jd_id, "hasMore": has_more, "nextCursor": str(last_id) if has_more else None,
            "providerCreditsUsed": 0, "providerContacted": False,
        }
    finally:
        conn.close()
