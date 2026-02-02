
from scorer import score

def rank(profiles, job, weights):
    results = []
    for p in profiles:
        s, overlap = score(p, job, weights)
        results.append({
            "name": p.get("full_name"),
            "email": p.get("email"),
            "score": s,
            "why": overlap,
            "summary": p.get("summary", "")
        })
    return sorted(results, key=lambda x: x["score"], reverse=True)
