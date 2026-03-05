from fastapi import APIRouter, Form
from azure.storage import candidates

router = APIRouter(
    prefix="/api/azure",
    tags=["azure", "candidates"]
)
@router.get("/countCandidates")
async def count_candidates(domain: str = "all"):
    print(f"Counting candidates for domain: {domain}")
    if domain == "technology":
        return candidates.countCandidates()
    
@router.get("/countCandidates/recent")
async def count_candidates_recent(domain: str = "all"):
    print(f"Counting recent candidates for domain: {domain}")
    if domain == "technology":
        return candidates.countCandidatesRecent()
    
@router.get("/countCandidates/status")
async def count_candidates_status(domain: str = "all"):
    print(f"Counting candidates by status for domain: {domain}")
    if domain == "technology":
        return candidates.countCandidatesStatus()
    
@router.get("/countCandidates/all")
async def count_candidates_all(domain: str = "all"):
    print(f"Counting all candidates for domain: {domain}")
    if domain == "technology":
        outcome = candidates.countCandidatesAll()
        return outcome

@router.post("/searchNameEmail")
async def get_candidates(search_string: str = Form(...), domain: str = Form(...)):
    if domain == "technology":
        return candidates.searchCandidatesByNameEmail(search_string, 5)