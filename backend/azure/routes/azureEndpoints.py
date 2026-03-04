from fastapi import APIRouter, Form
from azure.storage import candidates

router = APIRouter(
    prefix="/api/azure",
    tags=["azure", "candidates"]
)

@router.post("/searchNameEmail")
async def get_candidates(search_string: str = Form(...), domain: str = Form(...)):
    return candidates.searchCandidatesByNameEmail(search_string, 5)