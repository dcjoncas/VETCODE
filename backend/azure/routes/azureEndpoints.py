from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from azure.storage import candidates
from resumeProcessing.processing import ingest
from deterministic_profile import build_profile_from_text
from openAI.candidateProcessing import candidateDescription

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
async def get_candidates(search_string: str = Form(...), domain: str = Form(...), limit: int = Form(5)):
    if domain == "technology":
        return candidates.searchCandidatesByNameEmail(search_string, limit)
    
@router.post("/searchSkills")
async def get_candidates(skills: str = Form(...), domain: str = Form(...), limit: int = Form(5)):
    if domain == "technology":
        return candidates.searchCandidatesBySkills(skills, limit)
    
@router.post("/pageCount")
def profile_page_count(domain: str = Form(default="technology"), search_string: str = Form(default=""), skills: str = Form(default=""), pageLimit: int = Form(default=10)):
    print(f"Calculating page count for domain='{domain}' with search_string='{search_string}'")
    
    if len(skills) > 0 and skills != 'null':
        return candidates.searchPageCount(search_string, skills, pageLimit)
    else:
        return candidates.searchPageCount(search_string, None, pageLimit)
    
@router.post("/pageSearch")
def profile_page_search(domain: str = Form(default="technology"), search_string: str = Form(default=""), currentPage: int = Form(default=1), pageLimit: int = Form(default=10), skills: str = Form(default="")):
    print(f"Searching profiles for domain='{domain}' with search_string='{search_string}' on page {currentPage} with pageLimit {pageLimit}")

    currentPage = currentPage - 1  # adjust for 0-based indexing in backend

    if len(skills) > 0 and skills != 'null':
        return candidates.searchCandidatesBySkillsNamesPaginated(search_string,skills,pageLimit,currentPage)
    else:
        return candidates.searchCandidatesByNameEmailPaginated(search_string,pageLimit,currentPage)
    
@router.get("/getProfile/{profileId}")
def get_profile(profileId: str = ""):
    print(f"Fetching profile {profileId}")

    return candidates.getProfile(profileId)

@router.get("/public/{profileUrl}")
def get_profile(profileUrl: str = ""):
    print(f"Fetching profile {profileUrl}")

    return candidates.getProfilePublic(profileUrl)

@router.post("/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    source_type: str = Form(None),
    domain: str = Form("technology"),
):
    print(file)
    
    #try:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file name received.")
    #path = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
    #with open(path, "wb") as f:
    #    shutil.copyfileobj(file.file, f)

    file_bytes = await file.read()

    raw = ingest(source_type, file_bytes)
    profile = build_profile_from_text(raw)

    flatSkills = []

    for key, value in profile["skills"].items():
        flatSkills.extend(value)

    description = candidateDescription(raw)
    print(f"Candidate Description: {description}")

    return candidates.uploadProfile(skills=flatSkills, fullName=profile["contact"]["full_name"], email=profile["contact"]["email"], linkedInUrl=profile["contact"]["linkedin"], candidateDescription=description)