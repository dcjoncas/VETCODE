"""Deterministic, inspectable job search requirements; no external calls.

Search filters may widen logistics, but cannot remove the job's relevance anchor.
Only job-related text is used. The catalog normalizes technologies, not people.
"""
import re

VERSION = "jd-relevance-v2"
SKILLS = {
    "Java": ["java"], "Spring Boot": ["spring boot", "springboot"],
    "React": ["react", "react.js", "reactjs"],
    "PostgreSQL": ["postgresql", "postgres"], "Docker": ["docker"],
    "Linux": ["linux"], "CI/CD": ["ci/cd", "continuous integration"],
    "Python": ["python"], "JavaScript": ["javascript", "java script"],
    "TypeScript": ["typescript", "type script"], "Node.js": ["node.js", "nodejs"],
    "Next.js": ["next.js", "nextjs"], "Angular": ["angular", "angularjs"],
    "Vue.js": ["vue.js", "vuejs"], "C#": ["c#", "c sharp"],
    ".NET": [".net", "dotnet"], "C++": ["c++", "c plus plus"],
    "AWS": ["aws", "amazon web services"], "Azure": ["azure"],
    "GCP": ["gcp", "google cloud platform"], "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"], "SQL": ["sql"], "MySQL": ["mysql"],
    "MongoDB": ["mongodb"], "Redis": ["redis"], "Kafka": ["kafka"],
    "Airflow": ["airflow"], "Trino": ["trino"], "Iceberg": ["iceberg"],
    "JMS": ["jms"], "ActiveMQ": ["activemq"],
    "LLMs": ["llms", "large language models"], "PyTorch": ["pytorch"],
    "TensorFlow": ["tensorflow"], "Spark": ["apache spark"],
    "Snowflake": ["snowflake"], "Databricks": ["databricks"],
    "Salesforce": ["salesforce"], "SAP": ["sap"],
}
# Whole terms, not words removed from otherwise meaningful requirements.
NOISE = {"adaptable", "api", "apis", "boot", "data", "database", "deploy", "design", "designer",
         "documentation", "foundation", "frontend", "infrastructure", "make", "management",
         "migrations", "monitor", "monitoring", "next", "operations", "pace", "performance", "product",
         "safety", "security", "spring", "teams", "tooling", "was", "full-stack", "full stack", "java / spring"}


def contains(text, term):
    return bool(re.search(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", text, re.I))


def role_titles(title):
    title = str(title or "").strip()
    lower = title.lower()
    if re.search(r"full[ -]?stack|founding engineer", lower):
        return ["full stack engineer", "full stack developer", "full-stack engineer", "full-stack developer",
                "software engineer", "software developer", "founding engineer", "technical lead"]
    for phrase, alternatives in (
        ("software", ["software engineer", "software developer"]),
        ("backend", ["backend engineer", "back end developer", "software engineer"]),
        ("front", ["frontend engineer", "front end developer", "software engineer"]),
        ("data engineer", ["data engineer", "data platform engineer", "analytics engineer"]),
        ("machine learning", ["machine learning engineer", "ai engineer", "data scientist"]),
        ("devops", ["devops engineer", "site reliability engineer", "platform engineer"]),
    ):
        if phrase in lower:
            return alternatives
    clean = re.sub(r"\([^)]*\)", "", title)
    clean = re.sub(r"\b(?:senior|sr\.?|staff|junior|jr\.?)\b", "", clean, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip(" /-")
    return [clean.lower()] if clean else []


def build_plan(jd, skills=None):
    title = str(jd.get("title") or "")
    description = str(jd.get("description") or jd.get("jd_text") or "")
    text = title + "\n" + description
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = []
    for label, aliases in SKILLS.items():
        hits = [(index, line) for index, line in enumerate(lines) if any(contains(line, term) for term in aliases)]
        if not hits:
            continue
        core = [(index, line) for index, line in hits if not re.search(r"\b(?:bonus|nice.to.have|preferred|optional|a plus)\b", line, re.I)]
        index, quote = (core or hits)[0]
        items.append({"label": label, "alternatives": aliases, "required": bool(core), "jdEvidence": quote[:600], "position": index})
    # For other professions retain meaningful, explicitly saved JD skills.
    known = {alias.lower() for aliases in SKILLS.values() for alias in aliases}
    for skill in skills if isinstance(skills, list) else jd.get("skills", []):
        if not isinstance(skill, str) or not skill.strip():
            continue
        term = skill.strip()
        if term.lower() in known or term.lower() in NOISE or re.search(r"\b(?:gender|sex|age|race|religion|citizenship|nationality|marital|disability)\b", term, re.I):
            continue
        hits = [(i, line) for i, line in enumerate(lines) if contains(line, term)]
        if description and not hits:
            continue
        index, quote = hits[0] if hits else (len(lines), "Saved job requirement: " + term)
        optional = bool(re.search(r"\b(?:bonus|nice.to.have|preferred|optional|a plus)\b", quote, re.I))
        items.append({"label": term, "alternatives": [term.lower()], "required": not optional, "jdEvidence": quote[:600], "position": index})
    # Without narrative, the saved structured requirements remain authoritative.
    for skill in skills if isinstance(skills, list) else jd.get("skills", []):
        for label, aliases in SKILLS.items():
            if isinstance(skill, str) and skill.lower() in aliases and not any(item["label"] == label for item in items):
                items.append({"label": label, "alternatives": aliases, "required": True,
                              "jdEvidence": "Saved job requirement: " + skill, "position": len(lines)})
    items.sort(key=lambda item: (not item["required"], item["label"] not in SKILLS, item["position"]))
    years = re.search(r"\b(\d{1,2})\s*(?:\+|[-–]\s*\d{1,2})?\s*years?\s+(?:of\s+)?(?:experience|building|developing|working|in\b)", description, re.I)
    return {"version": VERSION, "titles": role_titles(title), "skills": items,
            "minYears": int(years.group(1)) if years else 0,
            "coreSkills": [item["label"] for item in items if item["required"]],
            "bonusSkills": [item["label"] for item in items if not item["required"]],
            "scope": "Job role and explicit technical requirements; qualitative responsibilities require review."}


def open_to_work(candidate):
    """Public professional text signal only; no inference from employment status."""
    details = candidate.get("profile_data") or {}
    for field in ("headline", "summary"):
        value = candidate.get(field) or details.get(field)
        if not isinstance(value, str):
            continue
        for sentence in re.split(r"[.!?\n|]", value):
            if re.search(r"\b(?:not|no longer|hiring|candidates|helping|recruiting)\b", sentence, re.I):
                continue
            if re.search(r"#?open\s*to\s*work\b|\b(?:i am |i'm |currently )?(?:actively seeking|seeking new|open to new) (?:roles|opportunities|work)\b", sentence, re.I):
                return {"status": "signal", "label": "Open to Work — profile text", "source": "People Data Labs professional text",
                        "field": field, "evidence": sentence.strip()[:300], "linkedinBadgeVerified": False,
                        "observedAt": None, "note": "Provider text may be stale. Confirm the current LinkedIn indicator before outreach."}
    return {"status": "unknown", "label": "Open to Work: unknown", "linkedinBadgeVerified": False,
            "note": "PDL does not supply a documented LinkedIn Open to Work badge field. Check LinkedIn; private signals require Recruiter."}
