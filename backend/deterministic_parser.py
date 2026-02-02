
import datetime

def baseline_profile(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name = lines[0] if lines else "Candidate"
    email = next((l for l in lines if "@" in l), "")
    return {
        "meta": {
            "profile_id": "DR-" + datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "version": "1.0",
            "created_at": datetime.datetime.utcnow().isoformat()
        },
        "contact": {
            "full_name": name,
            "email": email
        },
        "capability_scores": {
            "business": 5,
            "functional": 5,
            "technical": 5
        }
    }
