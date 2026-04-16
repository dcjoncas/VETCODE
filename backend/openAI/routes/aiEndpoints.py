from fastapi import APIRouter, Form
from openAI import emailProcessing
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/ai",
    tags=["ai","processing", "candidates"]
)

class incomingCandidateScores(BaseModel):
    jobId: str
    candidateScores: list[emailProcessing.candidateScores]

@router.post("/clientEmail/shortlist")
async def scheduleChats(incomingCandidateScores: incomingCandidateScores):
    jobId = incomingCandidateScores.jobId
    candidateScores = incomingCandidateScores.candidateScores

    print(f"Generating email for job: {jobId}")
    return emailProcessing.shortlistClientEmail(jobId, candidateScores)
