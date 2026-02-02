
def score(profile, job, weights):
    overlap = set(profile.get("skills", [])) & set(job.get("required_skills", []))
    score = min(len(overlap) * 20 * weights["skills"], 100)
    return round(score, 1), list(overlap)
