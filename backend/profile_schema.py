from datetime import datetime

def new_id(prefix: str):
    return f"{prefix}-" + datetime.utcnow().strftime("%Y%m%d%H%M%S%f")[:-3]

def empty_devready_profile():
    return {
        "meta": {
            "profile_id": new_id("DRP"),
            "schema": "devready.profile.v1",
            "domain": "technology",
            "created_at": datetime.utcnow().isoformat() + "Z"
        },
        "contact": {
            "full_name": "",
            "email": "",
            "phone": "",
            "location": "",
            "linkedin": ""
        },
        "summary": {
            "headline": "",
            "overview": ""
        },
        "skills": {
            "languages": [],
            "frontend": [],
            "backend": [],
            "cloud_devops": [],
            "data": [],
            "testing": [],
            "security": [],
            "other": []
        },
        "experience": [],
        "education": [],
        "scores": {
            "overall_technical": {"score": 0, "rationale": ""},
            "backend": {"score": 0, "rationale": ""},
            "frontend": {"score": 0, "rationale": ""},
            "cloud_devops": {"score": 0, "rationale": ""},
            "data": {"score": 0, "rationale": ""},
            "functional": {"score": 0, "rationale": ""},
            "business": {"score": 0, "rationale": ""},
            "testing": {"score": 0, "rationale": ""}
        },
        "debug": {
            "text_chars": 0,
            "text_lines": 0
        }
    }
