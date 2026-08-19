from fastapi import APIRouter, Form
from openAI import emailProcessing
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/api/ai",
    tags=["ai","processing", "candidates"]
)

class incomingCandidateScores(BaseModel):
    jobId: str
    candidateScores: list[emailProcessing.candidateScores]
    clientContext: dict = Field(default_factory=dict)

@router.post("/clientEmail/shortlist")
async def scheduleChats(incomingCandidateScores: incomingCandidateScores):
    jobId = incomingCandidateScores.jobId
    candidateScores = incomingCandidateScores.candidateScores
    client_context = incomingCandidateScores.clientContext

    print(f"Generating email for job: {jobId}")
    return emailProcessing.shortlistClientEmail(jobId, candidateScores, client_context)
