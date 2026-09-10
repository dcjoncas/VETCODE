"""Local, explainable JD evidence comparison; no provider, database or AI calls.

The percentage is supported professional requirements / active requirements, not
a hiring probability or a resume assessment. An absent fact is UNKNOWN, not a
failed requirement. Logistics are reported separately and never improve fit.
Only raw/normalized professional fields are evidence; prior scores, derived
top_matches, identity, contact details and demographic fields are not evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any


VERSION = "professional-evidence-v1"
GROUP_WEIGHTS = {"skills": 50, "titles": 20, "experience": 15, "licenses": 15}
GROUPS = (*GROUP_WEIGHTS, "cities", "arrangements", "workforce")
RANGES = {"1-2": (1, 2), "3-5": (3, 5), "6-9": (6, 9), "10-14": (10, 14), "15+": (15, None)}
# Deliberately narrow equivalences; e.g. Java is NOT JavaScript and managing a
# project is NOT evidence of project-management certification.
ALIASES = (
    ("javascript", "java script"), ("typescript", "type script"),
    ("aws", "amazon web services"), ("gcp", "google cloud platform"),
    ("postgresql", "postgres"), ("node.js", "nodejs"),
    ("react", "react.js", "reactjs"), ("c#", "c sharp"),
    ("c++", "c plus plus"), (".net", "dotnet", "dot net"),
    ("ci/cd", "continuous integration and continuous delivery"),
    ("software engineer", "software developer"),
)
PROTECTED = re.compile(
    r"\b(?:gender|sex|female|male|women|men|racial|race(?! conditions?)|ethnicity|"
    r"religion|religious|marital|pregnan\w*|disab\w*|nationality|citizenship|"
    r"native speaker|birth(?:day|place|date)?|age(?:d)?|sexual orientation|"
    r"veteran status|family status)\b", re.I,
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _norm(value: Any) -> str:
    return _text(value).casefold().replace("–", "-").replace("—", "-")


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(text for item in values if (text := _text(item))))


def _professional_terms(value: Any) -> list[str]:
    return [term for term in _strings(value) if not PROTECTED.search(term)]


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _title(value: Any) -> str:
    return _text(value) or _text(_dict(value).get("name")) or _text(_dict(value).get("title"))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 80 else None
    except (ValueError, TypeError):
        return None


def _variants(term: str) -> list[str]:
    normalized = _norm(term)
    for aliases in ALIASES:
        if normalized in aliases:
            return list(aliases)
    # Substitute complete alias phrases inside longer titles/requirements only.
    variants = [normalized]
    for aliases in ALIASES:
        for alias in aliases:
            pattern = r"(?<![\w+#])" + re.escape(alias) + r"(?![\w+#])"
            if re.search(pattern, normalized):
                variants.extend(re.sub(pattern, replacement, normalized) for replacement in aliases)
    return list(dict.fromkeys(variants))


def _positive_occurrence(term: str, text: str) -> int | None:
    normalized = _norm(text)
    for variant in _variants(term):
        pattern = r"(?<![\w+#])" + re.escape(variant) + r"(?![\w+#])"
        for occurrence in re.finditer(pattern, normalized):
            before = normalized[max(0, occurrence.start() - 100):occurrence.start()]
            before = re.split(r"[.;!?,]|\b(?:but|however|although|yet)\b", before)[-1]
            before = re.sub(r"\bnot only\b", "", before)
            after = normalized[occurrence.end():occurrence.end() + 65]
            # Do not turn an explicit negative or aspirational mention into skill evidence.
            if re.search(r"\b(?:no|not|never|without|lack(?:s|ing)?|want(?:s)? to learn|learning|plan(?:s|ning)? to learn|intend(?:s)? to learn|hop(?:e|es|ing) to learn)\b", before):
                continue
            if re.match(r"[\s:\-]*(?:(?:is|was|has|have) )?(?:no experience|not (?:known|used)|experience (?:is )?lacking|is planned)\b", after):
                continue
            return occurrence.start()
    return None


def _phrase(term: str, text: str) -> bool:
    return _positive_occurrence(term, text) is not None


def _evidence_excerpt(text: str, alternatives: list[str]) -> str:
    positions = [_positive_occurrence(term, text) for term in alternatives]
    mention_positions = [_norm(text).find(variant) for term in alternatives for variant in _variants(term)]
    position = min((item for item in positions if item is not None), default=min((item for item in mention_positions if item >= 0), default=0))
    start = max(0, position - 70)
    return ("…" if start else "") + text[start:start + 240] + ("…" if start + 240 < len(text) else "")


def _expired_credential(fact: dict) -> bool:
    text = re.sub(r"\bnot\s+(?:expired|revoked|lapsed|inactive)\b", "", fact["text"], flags=re.I)
    if re.search(r"\b(?:expired|revoked|lapsed|inactive)\b", text, re.I):
        return True
    expiry = fact.get("expiry", "")
    if re.fullmatch(r"\d{4}", expiry):
        return int(expiry) < date.today().year
    try:
        return date.fromisoformat(expiry[:10]) < date.today()
    except ValueError:
        return False


def _evidence(candidate: dict) -> dict:
    details = _dict(candidate.get("profile_data"))
    facts: list[dict] = []

    def add(path: str, value: Any, kind: str):
        text = _text(value)
        if text:
            facts.append({"field": path, "text": text[:4000], "kind": kind})

    add("job_title" if "job_title" in candidate else "title", _title(candidate.get("job_title") or candidate.get("title")), "title")
    for field in ("summary", "headline", "job_summary"):
        add(field if candidate.get(field) else f"profile_data.{field}", candidate.get(field) or details.get(field), "narrative")
    raw_skills = candidate.get("skills") if isinstance(candidate.get("skills"), list) else [candidate.get("skills")]
    for index, value in enumerate(raw_skills):
        skill = _text(value)
        if skill and not PROTECTED.search(skill):
            add(f"skills[{index}]", skill, "skill")
    experiences = candidate.get("experience") if isinstance(candidate.get("experience"), list) else details.get("experience", [])
    experience_prefix = "experience" if isinstance(candidate.get("experience"), list) else "profile_data.experience"
    for index, item in enumerate(experiences if isinstance(experiences, list) else []):
        item = _dict(item)
        add(f"{experience_prefix}[{index}].title", _title(item.get("title") or item.get("job_title")), "experience_title")
        add(f"{experience_prefix}[{index}].summary", item.get("summary"), "narrative")
    certifications = candidate.get("certifications") if isinstance(candidate.get("certifications"), list) else details.get("certifications", [])
    certification_prefix = "certifications" if isinstance(candidate.get("certifications"), list) else "profile_data.certifications"
    for index, item in enumerate(certifications if isinstance(certifications, list) else []):
        item_text = _text(item) or _text(_dict(item).get("name") or _dict(item).get("title"))
        add(f"{certification_prefix}[{index}]", item_text, "credential")
        if item_text:
            cert = _dict(item)
            facts[-1]["expiry"] = _text(cert.get("expiration_date") or cert.get("expires_on") or cert.get("end_date") or cert.get("expires_at"))
            if _norm(cert.get("status")) in {"expired", "revoked", "lapsed", "inactive"}:
                facts[-1]["text"] += " (" + _norm(cert.get("status")) + ")"

    # education is limited to job-relevant degree/major, never institution ranking,
    # graduation year (age proxy), or name.
    education = candidate.get("education") if isinstance(candidate.get("education"), list) else details.get("education", [])
    education_prefix = "education" if isinstance(candidate.get("education"), list) else "profile_data.education"
    for index, item in enumerate(education if isinstance(education, list) else []):
        for field in ("degrees", "majors"):
            for value in _professional_terms(_dict(item).get(field)):
                add(f"{education_prefix}[{index}].{field}", value, "education")

    raw_years = candidate.get("inferred_years_experience")
    years = _number(raw_years)
    if years is None:
        years = _number(candidate.get("years_experience"))
        # The legacy normalized row uses 0 for both missing and actual zero.
        if years == 0 and candidate.get("years_experience_known") is not True:
            years = None
    location = _dict(details.get("location"))
    locations = _strings([
        candidate.get("location_locality"), candidate.get("location_region"),
        candidate.get("location_name"), candidate.get("location") if isinstance(candidate.get("location"), str) else "",
        location.get("locality"), location.get("region"), location.get("name"),
    ])
    return {"facts": facts, "years": years, "locations": locations}


def _requirements(jd: dict, criteria: dict, job_skills: Any) -> list[dict]:
    ignored = set(_strings(criteria.get("ignoredCriteria")))
    if criteria.get("ignoreAll") is True:
        ignored.update(GROUPS)
    requirements: list[dict] = []

    def add(group: str, label: str, alternatives: list[str], required: bool = True, **extra):
        if not alternatives and not extra:
            return
        requirements.append({
            "id": f"{group}:{len(requirements)}", "group": group, "label": label,
            "alternatives": alternatives, "required": required,
            "ignored": group in ignored, "scored": group in GROUP_WEIGHTS,
            **extra,
        })

    titles = _professional_terms(criteria.get("titles")) or _professional_terms(jd.get("title"))
    if titles:
        add("titles", "Role: " + " or ".join(titles), titles)
    selected_skills = _professional_terms(criteria.get("mustHaveSkills") or criteria.get("requiredSkills"))
    jd_skills = _professional_terms(job_skills if job_skills is not None else jd.get("skills"))
    # Recruiter-selected must-haves carry the same group weight as other JD skills,
    # but are individually flagged for review. There is no first-12 truncation.
    normalized_skills: set[str] = set()
    for skill in selected_skills + jd_skills:
        key = min(_variants(skill))
        if key in normalized_skills:
            continue
        normalized_skills.add(key)
        add("skills", skill, [skill], required=(not selected_skills or skill in selected_skills))

    minimum = _number(criteria.get("minYears"))
    if minimum is None:
        minimum = _number(jd.get("minYears") or jd.get("min_years_experience"))
    ranges = [label for label in _strings(criteria.get("experienceRanges")) if label in RANGES]
    if ranges:
        add("experience", "Reported total experience: " + " or ".join(ranges) + " years", ranges, ranges=ranges)
    elif minimum is not None and minimum > 0:
        add("experience", f"Reported total experience: at least {minimum:g} years", [], minimum=minimum)

    credentials = _professional_terms(criteria.get("licensesOrCertifications") or criteria.get("licenseOrCertification"))
    credentials = [item for item in credentials if _norm(item) not in {"none", "none required", "not required"}]
    if credentials:
        add("licenses", "Credential: " + " or ".join(credentials), credentials)
    locations = _professional_terms(criteria.get("locations"))
    if locations:
        add("cities", "Work location: " + " or ".join(locations), locations)
    arrangements = _strings(criteria.get("workArrangements") or criteria.get("workArrangement"))
    if arrangements:
        add("arrangements", "Availability for " + " or ".join(arrangements), arrangements)
    workforce = _strings(criteria.get("workforceLocations") or criteria.get("workforceLocation"))
    if workforce and "either" not in workforce:
        add("workforce", "Workforce arrangement: " + " or ".join(workforce), workforce)
    return requirements


def _explicit_contrary_skill(term: str, text: str) -> bool:
    """Only unambiguous local patterns, not general natural-language inference."""
    for variant in _variants(term):
        escaped = re.escape(variant)
        if re.search(r"\bno\s+" + escaped + r"\s+(?:experience|knowledge|expertise)\b", _norm(text)):
            return True
        if re.search(r"\bno\s+(?:experience|knowledge|expertise)\s+(?:in|with|of)\s+" + escaped + r"(?![\w+#])", _norm(text)):
            return True
    return False


def _supports_requirement(term: str, fact: dict) -> bool:
    if fact["kind"] == "skill":
        # The provider skill field is a taxonomy/tag, not a sentence to parse.
        return _norm(fact["text"]) in _variants(term)
    return _phrase(term, fact["text"])


def _outcome(requirement: dict, evidence: dict) -> dict:
    result = {key: value for key, value in requirement.items() if key not in {"minimum", "ranges", "ignored"}}
    result.update({"status": "unknown", "evidence": [], "reason": "Not established by the available professional profile; ask the candidate."})
    if requirement["ignored"]:
        result.update(status="ignored", reason="Excluded by the recruiter's criteria controls.")
        return result
    group = requirement["group"]
    if group == "experience":
        years = evidence["years"]
        if years is None:
            return result
        if requirement.get("ranges"):
            matches = any(years >= RANGES[label][0] and (RANGES[label][1] is None or years <= RANGES[label][1]) for label in requirement["ranges"])
        else:
            matches = years >= requirement["minimum"]
        result.update(status="matched" if matches else "gap", evidence=[{"field": "reported_total_years_experience", "text": f"{years:g} years"}],
                      reason="Provider-reported/inferred total experience, not verified years using a specific skill.")
        return result
    if group in {"arrangements", "workforce"}:
        result["reason"] = "Availability and willingness require candidate confirmation; location does not establish these."
        return result
    if group == "cities":
        hits = [value for value in evidence["locations"] if any(_phrase(term, value) for term in requirement["alternatives"])]
        if hits:
            result.update(status="matched", evidence=[{"field": "reported_work_location", "text": hits[0]}], reason="Reported location overlaps the target; commute or relocation willingness is not established.")
        return result
    if group == "titles":
        eligible = [fact for fact in evidence["facts"] if fact["kind"] in {"title", "experience_title"}]
    elif group == "licenses":
        eligible = [fact for fact in evidence["facts"] if fact["kind"] == "credential"]
    else:
        # Narrative can describe ambitions, denials or another person's work.
        # Surface it for human review, never turn phrase occurrence into proof.
        eligible = [fact for fact in evidence["facts"] if fact["kind"] in {"skill", "education"}]
        narratives = [fact for fact in evidence["facts"] if fact["kind"] == "narrative"]
        contrary = [fact for fact in narratives if any(_explicit_contrary_skill(term, fact["text"]) for term in requirement["alternatives"])]
        mentions = [fact for fact in narratives if any(any(re.search(r"(?<![\w+#])" + re.escape(variant) + r"(?![\w+#])", _norm(fact["text"])) for variant in _variants(term)) for term in requirement["alternatives"])]
        structured_hits = [fact for fact in eligible if any(_supports_requirement(term, fact) for term in requirement["alternatives"])]
        if contrary:
            result.update(status="unknown" if structured_hits else "gap",
                          evidence=[{"field": fact["field"], "text": _evidence_excerpt(fact["text"], requirement["alternatives"])} for fact in contrary[:3]],
                          reason="Conflicting source statements require recruiter confirmation." if structured_hits else "The source explicitly reports no experience in this requirement; verify with the candidate.")
            return result
        if not structured_hits and mentions:
            result.update(evidence=[{"field": fact["field"], "text": _evidence_excerpt(fact["text"], requirement["alternatives"])} for fact in mentions[:3]],
                          reason="Mentioned in narrative only. Recruiter review is required to establish the candidate's actual qualification.")
            return result
    hits = [fact for fact in eligible if any(_supports_requirement(term, fact) for term in requirement["alternatives"])]
    if group == "licenses" and hits and all(_expired_credential(hit) for hit in hits):
        result.update(status="gap", evidence=[{"field": hit["field"], "text": _evidence_excerpt(hit["text"], requirement["alternatives"]), "expiry": hit.get("expiry", "")} for hit in hits[:3]],
                      reason="The reported credential is expired, revoked or inactive; verify current credentials with the candidate and issuer.")
        return result
    if group == "licenses":
        hits = [hit for hit in hits if not _expired_credential(hit)]
    if hits:
        result.update(status="matched", evidence=[{"field": hit["field"], "text": _evidence_excerpt(hit["text"], requirement["alternatives"])} for hit in hits[:3]],
                      reason="Requirement explicitly appears in professional evidence; scope, recency and proficiency still need verification.")
        if group == "licenses":
            result["reason"] = "Credential reported by provider; current validity, jurisdiction and standing still require verification."
    # Missing from a list or a different current title is not proof of inability.
    return result


def build_professional_match(jd: dict | None, candidate: dict | None, criteria: dict | None = None, job_skills: Any = None) -> dict:
    """Compare a selected JD with saved/raw professional data without enrichment.

    Pass the current resolved recruiter criteria, including ignoredCriteria.
    Raw PDL records and normalized external result rows are supported. Stored TEMP
    metadata must be adapted from its original provider evidence; do not pass old
    providerSkills when those were contaminated by derived top_matches.
    """
    jd, candidate, criteria = _dict(jd), _dict(candidate), _dict(criteria)
    evidence = _evidence(candidate)
    requirements = _requirements(jd, criteria, job_skills)
    outcomes = [_outcome(requirement, evidence) for requirement in requirements]
    active = [item for item in outcomes if item["scored"] and item["status"] != "ignored"]
    group_counts = {group: sum(item["group"] == group for item in active) for group in GROUP_WEIGHTS}
    total_weight = sum(weight for group, weight in GROUP_WEIGHTS.items() if group_counts[group])
    matched_weight = assessed_weight = 0.0
    for item in outcomes:
        item["weight"] = round(GROUP_WEIGHTS.get(item["group"], 0) / max(1, group_counts.get(item["group"], 0)), 4) if item in active else 0
        if item in active:
            if item["status"] == "matched":
                matched_weight += item["weight"]
            if item["status"] in {"matched", "gap"}:
                assessed_weight += item["weight"]
    meaningful = bool(evidence["facts"] or evidence["years"] is not None)
    has_jd = bool(jd and (_text(jd.get("title")) or _text(jd.get("description")) or _text(jd.get("jd_text")) or _professional_terms(jd.get("skills"))))
    explicitly_ignored = set(_strings(criteria.get("ignoredCriteria")))
    substantive_criteria = any(item["group"] != "titles" for item in active) or "skills" in explicitly_ignored
    status = "calculated" if has_jd and active and meaningful and substantive_criteria else "unavailable"
    score = round(100 * matched_weight / total_weight) if status == "calculated" else None
    coverage = round(100 * assessed_weight / total_weight) if status == "calculated" else None
    fingerprint_payload = {
        "version": VERSION,
        "job": {key: jd.get(key) for key in ("jd_id", "id", "title", "description", "jd_text", "skills")},
        "requirements": requirements, "evidence": evidence, "asOfDate": date.today().isoformat(),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    matched = [item["label"] for item in active if item["status"] == "matched"]
    unknown = [item["label"] for item in active if item["status"] == "unknown"]
    gaps = [item["label"] for item in active if item["status"] == "gap"]
    reason = (f"{len(matched)} of {len(active)} professional requirements have explicit support. "
              f"{len(unknown)} need confirmation; {len(gaps)} reported gaps. This is evidence support, not a hiring probability.")
    if status != "calculated":
        reason = "Select a job with professional requirements." if not has_jd else "No active professional requirements to compare." if not active else "No meaningful professional evidence was returned; contact links alone cannot establish a match." if not meaningful else "A role title alone cannot establish JD fit. Add or review the job's structured skill, experience and credential requirements."
    return {
        "version": VERSION, "status": status, "score": score,
        "label": "Reviewed JD criteria support", "band": "Evidence review" if score is not None else "Not assessed",
        "decision": "Recruiter review required", "reason": reason,
        "formula": "Weighted explicitly supported professional requirements / all active professional requirements. Unknown evidence is not a confirmed failure.",
        "coveragePercent": coverage, "coverageLabel": "Reviewed criteria assessed", "confidence": "Not statistically calibrated",
        "scoreScope": "Selected structured professional requirements only", "fullJobAssessment": False,
        "asOfDate": date.today().isoformat(),
        "criteria": outcomes, "matched": matched, "missing": unknown, "unknown": unknown, "gaps": gaps,
        "matchedCount": len(matched), "requiredCount": len(active),
        "mustHaveGaps": [item["label"] for item in active if item["required"] and item["status"] == "gap"],
        "mustHaveUnknown": [item["label"] for item in active if item["required"] and item["status"] == "unknown"],
        "ignoredCriteria": sorted({item["group"] for item in outcomes if item["status"] == "ignored"}),
        "criteriaSnapshot": {key: criteria[key] for key in (
            "titles", "mustHaveSkills", "requiredSkills", "locations", "minYears", "experienceRanges",
            "licensesOrCertifications", "licenseOrCertification", "workArrangements", "workArrangement",
            "workforceLocations", "workforceLocation", "ignoredCriteria", "ignoreAll", "strictLocations", "region",
        ) if key in criteria},
        "jobId": str(jd.get("jd_id") or jd.get("id") or ""), "jobTitle": _text(jd.get("title")),
        "clientName": _text(jd.get("company")), "evidenceFingerprint": fingerprint,
        "evidenceSources": [_text(candidate.get("source_label")) or "Returned professional profile"],
        "evidenceType": "structured_professional_profile", "resumeCompared": False,
        "limitations": ["Deterministic criterion comparison, not a complete semantic review of every sentence in the JD.",
                        "Absence of a skill is unknown, not proof the person lacks it. Narrative mentions need recruiter review and do not confirm a qualification.",
                        "Current credential validity, availability and proficiency need human verification.",
                        "Work location and availability checks are separate from the professional-match percentage."],
        "providerCreditsUsed": 0, "providerContacted": False,
    }


def match_is_current(saved_match: dict, jd: dict, candidate: dict, criteria: dict | None = None, job_skills: Any = None) -> bool:
    """Invalidate on JD, criteria, algorithm or professional-evidence change."""
    current = build_professional_match(jd, candidate, criteria, job_skills)
    return bool(saved_match.get("evidenceFingerprint") and saved_match.get("evidenceFingerprint") == current["evidenceFingerprint"])
