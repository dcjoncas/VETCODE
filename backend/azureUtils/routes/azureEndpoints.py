from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
from azureUtils.storage import candidates, resumes
from resumeProcessing.processing import ingest
from deterministic_profile import build_profile_from_text
from openAI.candidateProcessing import candidateDescription, processGeneral, candidateCulturalExperience, processSkillYears

router = APIRouter(
    prefix="/api/azure",
    tags=["azure", "candidates"]
)

@router.get("/skills")
async def skills_list():
    return candidates.getSkills()

@router.get("/skills/{searchQuery}")
async def search_skills(searchQuery: str):
    return candidates.searchSkills(searchQuery)

@router.get("/countCandidates")
async def count_candidates(domain: str = "all"):
    print(f"Counting candidates for domain: {domain}")
    if domain == "dev":
        return candidates.countCandidates()
    
@router.get("/countCandidates/recent")
async def count_candidates_recent(domain: str = "all"):
    print(f"Counting recent candidates for domain: {domain}")
    if domain == "dev":
        return candidates.countCandidatesRecent()
    
@router.get("/countCandidates/status")
async def count_candidates_status(domain: str = "all"):
    print(f"Counting candidates by status for domain: {domain}")
    if domain == "dev":
        return candidates.countCandidatesStatus()
    
@router.get("/countCandidates/all")
async def count_candidates_all(domain: str = "all"):
    print(f"Counting all candidates for domain: {domain}")
    if domain == "dev":
        outcome = candidates.countCandidatesAll()
        return outcome

@router.post("/searchNameEmail")
async def get_candidates(search_string: str = Form(...), domain: str = Form(...), limit: int = Form(5)):
    if domain == "dev":
        return candidates.searchCandidatesByNameEmail(search_string, limit)
    
@router.post("/searchSkills")
async def get_candidates(skills: str = Form(...), domain: str = Form(...), limit: int = Form(5)):
    if domain == "dev":
        return candidates.searchCandidatesBySkills(skills, limit)
    
@router.post("/pageCount")
def profile_page_count(domain: str = Form(default="dev"), search_string: str = Form(default=""), skills: str = Form(default=""), pageLimit: int = Form(default=10)):
    print(f"Calculating page count for domain='{domain}' with search_string='{search_string}'")
    
    if len(skills) > 0 and skills != 'null':
        return candidates.searchPageCount(search_string, skills, pageLimit)
    else:
        return candidates.searchPageCount(search_string, None, pageLimit)
    
@router.post("/pageSearch")
def profile_page_search(domain: str = Form(default="dev"), search_string: str = Form(default=""), currentPage: int = Form(default=1), pageLimit: int = Form(default=10), skills: str = Form(default="")):
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

@router.get("/getProfile/short/{profileId}")
def get_profile_short(profileId: str = ""):
    print(f"Fetching profile {profileId}")

    return candidates.getProfileShort(profileId)

@router.post("/getProfile/short/score/{jobId}")
def get_profile_short_score(jobId: str = "", profileIds: str = Form(...)):
    print(f"Fetching profiles {profileIds}")

    return candidates.getProfileShortScore(jobId, profileIds.split(','))

@router.post("/profile/update")
async def update_profile_core(personId: str = Form(...), first_name: str = Form(...), last_name: str = Form(...), city: str = Form(default=""), state: str = Form(default=""), country: str = Form(default=""), description: str = Form(default=""), job_title: str = Form(default="")):
    if not personId or personId == "" or not first_name or first_name == "" or not last_name or last_name == "":
        raise HTTPException(status_code=400, detail="Missing Basic Details. personId, first_name and last_name are required.")

    print(f"Updating profile {personId}")

    return candidates.updateCandidateCore(personId=personId, firstName=first_name, lastName=last_name, city=city, state=state, country=country, description=description, jobTitle=job_title)

class profileSkillsUpdateRequest(BaseModel):
    personId: str
    skills: list

@router.post("/profile/updateSkills")
async def update_profile_skills(profileSkillsUpdate: profileSkillsUpdateRequest):
    personId = profileSkillsUpdate.personId
    skills = profileSkillsUpdate.skills

    if not personId or personId == "":
        raise HTTPException(status_code=400, detail="Missing personId.")

    print(f"Updating skills for profile {personId}")

    return candidates.updateCandidateSkills(personId=personId, skills=skills)

class profileFeaturesUpdateRequest(BaseModel):
    personId: str
    features: list
    cultural: list

@router.post("/profile/updateFeatures")
async def update_profile_features(profileFeaturesUpdate: profileFeaturesUpdateRequest):
    personId = profileFeaturesUpdate.personId
    features = profileFeaturesUpdate.features
    cultural = profileFeaturesUpdate.cultural

    if not personId or personId == "":
        raise HTTPException(status_code=400, detail="Missing personId.")

    print(f"Updating features for profile {personId}")

    return candidates.updateCandidateFeatures(personId=personId, features=features, cultural=cultural)

@router.post("/profile/updatePortfolio")
async def update_profile_portfolio(portfolioUpdate: candidates.profilePortfolioUpdateRequest):
    print(portfolioUpdate)
    personId = portfolioUpdate.personId
    portfolio = portfolioUpdate.portfolio

    if not personId or personId == "":
        raise HTTPException(status_code=400, detail="Missing personId.")

    print(f"Updating portfolio for profile {personId}")

    return candidates.updateCandidatePortfolio(personId=personId, portfolio=portfolio)

# For multithreading
def process_skill(raw: str, key: str):
    return {
        "title": key,
        "years": processSkillYears(raw, key)
    }

@router.post("/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    source_type: str = Form(None),
    domain: str = Form(default="dev"),
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

    # Reset file pointer before uploading
    file.file.seek(0)

    flatSkills = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(process_skill, [raw] * len(profile["skills"]), profile["skills"].keys())

        flatSkills = list(results)

    # TODO: Make python call all AI functions in parallel to speed up processing time. Currently doing sequentially which is not efficient.
    with ThreadPoolExecutor(max_workers=6) as executor:
        description_future = executor.submit(candidateDescription, raw)
        culturalExperiences_future = executor.submit(candidateCulturalExperience, raw)
        candidateCity_future = executor.submit(processGeneral, raw, "currently lived in city (DO NOT RETURN PROVINCE OR STATE. DO NOT RETURN ASSOCIATED JOBS OR COMPANIES. ONLY RETURN CITY NAME)")
        candidateState_future = executor.submit(processGeneral, raw, "currently lived in state or province (DO NOT RETURN CITY. DO NOT RETURN ASSOCIATED JOBS OR COMPANIES. ONLY RETURN STATE OR PROVINCE NAME. RETURN NO ADDITIONAL COMMENTARY)")
        candidateCountry_future = executor.submit(processGeneral, raw, "currently lived in country (DO NOT RETURN CITY, STATE OR PROVINCE. ONLY RETURN COUNTRY NAME)")
        candidateTitle_future = executor.submit(processGeneral, raw, "current or most recent job title (DO NOT RETURN ANY ASSOCIATED COMPANIES OR JOBS. ONLY RETURN JOB TITLE. RETURN NO ADDITIONAL COMMENTARY)")

    print(profile)

    description = description_future.result()
    culturalExperiences = culturalExperiences_future.result()
    candidateCity = candidateCity_future.result()
    candidateState = candidateState_future.result()
    candidateCountry = candidateCountry_future.result()
    candidateTitle = candidateTitle_future.result()

    profileResult = candidates.uploadProfile(skills=flatSkills, fullName=profile["contact"]["full_name"], email=profile["contact"]["email"], linkedInUrl=profile["contact"]["linkedin"], candidateDescription=description, culturalExperiences=culturalExperiences, candidateCity=candidateCity, candidateState=candidateState, candidateCountry=candidateCountry, candidateTitle=candidateTitle)

    await resumes.uploadResume(file, profileResult["personid"])

    return profileResult

@router.get("/resume/{profileId}")
async def get_resume(profileId: str):
    print(f"Getting resume for profile {profileId}")
    return await resumes.getResume(int(profileId))