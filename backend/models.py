
from pydantic import BaseModel
from typing import List, Dict

class DevReadyProfile(BaseModel):
    profile_id: str
    full_name: str
    email: str
    skills: List[str]

class JobProfile(BaseModel):
    job_id: str
    title: str
    required_skills: List[str]

class MatchResult(BaseModel):
    job_id: str
    ranked_candidates: List[Dict]

    @staticmethod
    def sample():
        return {
            "job_id": "JD-001",
            "ranked_candidates": [
                {
                    "profile_id": "DR-001",
                    "score": 92.4,
                    "why": "Strong skill alignment"
                }
            ]
        }
