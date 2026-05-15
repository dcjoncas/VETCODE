import json
import os
import re
from typing import Any

from openai import OpenAI


RESUME_PROFILE_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _clean_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _safe_year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    if not match:
        return None
    year = int(match.group(0))
    if 1950 <= year <= 2100:
        return year
    return None


def _safe_level(value: Any) -> int:
    try:
        level = int(float(value))
    except (TypeError, ValueError):
        level = 1
    return max(1, min(level, 3))


def _safe_years(value: Any) -> int:
    try:
        years = int(round(float(value)))
    except (TypeError, ValueError):
        years = 1
    return max(1, min(years, 40))


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def normalize_ai_resume_profile(raw_text: str, domain: str = "dev") -> dict[str, Any] | None:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None

    client = OpenAI()
    system = """
You extract a complete DevReady candidate profile from resume text.
Return ONLY valid JSON. Do not wrap it in markdown.

Use this exact shape:
{
  "contact": {
    "full_name": "",
    "email": "",
    "phone": "",
    "linkedin": "",
    "city": "",
    "state": "",
    "country": ""
  },
  "headline": "",
  "summary": "",
  "skills": [
    {"title": "Java", "years": 6}
  ],
  "culture": [
    {"experience": "Enterprise consulting", "level": 2}
  ],
  "portfolio": [
    {
      "companyName": "Company",
      "mainRole": "Role",
      "description": "Specific impact, systems, scope, and technologies from this role.",
      "startDate": 2020,
      "finishDate": 2023,
      "isPresent": false,
      "skills": ["Java", "SQL"],
      "features": ["System design", "Client delivery"]
    }
  ],
  "confidence": 0.0
}

Rules:
- Dates must be years as four digit integers only, never months or ranges like 1-4.
- If a role is current, use null finishDate and true isPresent.
- Preserve separate jobs as separate portfolio rows.
- Infer realistic skill years from date ranges and repeated usage; do not overstate.
- Keep descriptions concise but meaningful for hiring review.
- Culture means work setting/domain such as Enterprise Consulting, Financial Services, Healthcare, Startup Delivery, Client Leadership, Agile Product Delivery, AI/Data Delivery.
- For missing information, use empty strings, empty arrays, or null.
""".strip()

    response = client.chat.completions.create(
        model=RESUME_PROFILE_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Domain: {domain}\n\nResume text:\n{(raw_text or '')[:18000]}",
            },
        ],
        timeout=45,
    )
    parsed = _clean_json(response.choices[0].message.content or "{}")

    contact = parsed.get("contact") if isinstance(parsed.get("contact"), dict) else {}
    skills = []
    seen_skills = set()
    for item in _as_list(parsed.get("skills")):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        key = title.lower()
        if not title or key in seen_skills:
            continue
        seen_skills.add(key)
        skills.append({"title": title, "years": _safe_years(item.get("years"))})

    culture = []
    for item in _as_list(parsed.get("culture")):
        if not isinstance(item, dict):
            continue
        title = str(item.get("experience") or item.get("title") or "").strip()
        if title:
            culture.append({"experience": title, "level": _safe_level(item.get("level"))})

    portfolio = []
    for item in _as_list(parsed.get("portfolio")):
        if not isinstance(item, dict):
            continue
        company = str(item.get("companyName") or item.get("company") or "").strip()
        role = str(item.get("mainRole") or item.get("role") or "").strip()
        description = str(item.get("description") or "").strip()
        start_year = _safe_year(item.get("startDate"))
        finish_year = _safe_year(item.get("finishDate"))
        is_present = bool(item.get("isPresent")) or str(item.get("finishDate") or "").lower() in {
            "present",
            "current",
            "now",
        }
        if not company and not role and not description:
            continue
        portfolio.append(
            {
                "companyName": company or "Company not listed",
                "mainRole": role or "Role not listed",
                "description": description,
                "startDate": start_year,
                "finishDate": None if is_present else finish_year,
                "isPresent": is_present,
                "skills": [
                    str(skill).strip()
                    for skill in _as_list(item.get("skills"))
                    if str(skill).strip()
                ][:12],
                "features": [
                    str(feature).strip()
                    for feature in _as_list(item.get("features"))
                    if str(feature).strip()
                ][:8],
            }
        )

    return {
        "contact": {
            "full_name": str(contact.get("full_name") or "").strip(),
            "email": str(contact.get("email") or "").strip(),
            "phone": str(contact.get("phone") or "").strip(),
            "linkedin": str(contact.get("linkedin") or "").strip(),
            "city": str(contact.get("city") or "").strip(),
            "state": str(contact.get("state") or "").strip(),
            "country": str(contact.get("country") or "").strip(),
        },
        "headline": str(parsed.get("headline") or "").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
        "skills": skills,
        "culture": culture,
        "portfolio": portfolio,
        "confidence": parsed.get("confidence"),
    }
