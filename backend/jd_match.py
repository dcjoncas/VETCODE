import re
from skill_lexicon import SKILL_GROUPS

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def normalize_jd(text: str):
    t = _norm(text)
    skills = {k:set() for k in SKILL_GROUPS.keys()}
    for group, items in SKILL_GROUPS.items():
        for sk in items:
            if _norm(sk) in t:
                skills[group].add(sk)
    return {k: sorted(v) for k,v in skills.items()}

def jaccard(a: set, b: set):
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b) or 1
    return inter / union

WEIGHTS = {
    "languages": 0.22,
    "backend": 0.22,
    "frontend": 0.18,
    "cloud_devops": 0.18,
    "data": 0.12,
    "testing": 0.08,
    "security": 0.0
}

def match(profile_skills: dict, jd_skills: dict):
    """
    Returns:
      total_score (0-100)
      score_parts breakdown per skill group

    Scoring philosophy (v2.5.0):
    - Use *JD coverage* (matched / required JD skills) rather than Jaccard.
      This avoids unnaturally low scores when a candidate has more skills than the JD.
    """
    score_parts = {}
    total = 0.0
    for group, w in WEIGHTS.items():
        ps = set((profile_skills or {}).get(group, []) or [])
        js = set((jd_skills or {}).get(group, []) or [])
        matched = ps & js
        # JD coverage score: how much of the JD bucket is covered by the profile
        coverage = (len(matched) / max(1, len(js))) if js else 0.0
        # still provide jaccard for diagnostics
        j = jaccard(ps, js)
        score_parts[group] = {
            "weight": w,
            "coverage": round(coverage, 3),
            "jaccard": round(j, 3),
            "matched": sorted(list(matched)),
            "missing": sorted(list(js - ps))
        }
        total += w * coverage

    total = round(total * 100, 1)
    return total, score_parts
