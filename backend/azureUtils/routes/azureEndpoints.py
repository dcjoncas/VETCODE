from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
import os
import traceback
from azureUtils.storage import candidates, resumes
from resumeProcessing.processing import ingest
from deterministic_profile import build_profile_from_text, extract_portfolio
from resume_ai_profile import normalize_ai_resume_profile
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
    return candidates.countCandidates(domain)
    
@router.get("/countCandidates/recent")
async def count_candidates_recent(domain: str = "all"):
    print(f"Counting recent candidates for domain: {domain}")
    return candidates.countCandidatesRecent(domain)
    
@router.get("/countCandidates/status")
async def count_candidates_status(domain: str = "all"):
    print(f"Counting candidates by status for domain: {domain}")
    if domain == "dev":
        return candidates.countCandidatesStatus()
    
@router.get("/countCandidates/all")
async def count_candidates_all(domain: str = "all"):
    print(f"Counting all candidates for domain: {domain}")
    return candidates.countCandidatesAll(domain)

@router.get("/profile/discovery")
async def profile_discovery(domain: str = "dev", limit: int = 500):
    print(f"Scanning profile discovery for domain: {domain}")
    return candidates.profileDiscovery(domain, limit)

@router.get("/profiles/alphabetical")
async def profiles_alphabetical(domain: str = "dev"):
    print(f"Listing profiles alphabetically for domain: {domain}")
    return candidates.listProfilesAlphabetical(domain)

@router.post("/searchNameEmail")
async def get_candidates(search_string: str = Form(...), domain: str = Form(...), limit: int = Form(5)):
    print('searching for candidates with domain ' + domain)
    return candidates.searchCandidatesByNameEmail(search_string, limit, domain)
    
@router.post("/searchSkills")
async def get_candidates(skills: str = Form(...), domain: str = Form(...), limit: int = Form(5)):
    return candidates.searchCandidatesBySkills(skills, limit, domain)
    
@router.post("/pageCount")
def profile_page_count(domain: str = Form(default="all"), search_string: str = Form(default=""), skills: str = Form(default=""), pageLimit: int = Form(default=10)):
    print(f"Calculating page count for domain='{domain}' with search_string='{search_string}'")
    
    if len(skills) > 0 and skills != 'null':
        return candidates.searchPageCount(search_string, skills, pageLimit, domain)
    else:
        return candidates.searchPageCount(search_string, None, pageLimit, domain)
    
@router.post("/pageSearch")
def profile_page_search(domain: str = Form(default="dev"), search_string: str = Form(default=""), currentPage: int = Form(default=1), pageLimit: int = Form(default=10), skills: str = Form(default="")):
    print(f"Searching profiles for domain='{domain}' with search_string='{search_string}' on page {currentPage} with pageLimit {pageLimit}")

    currentPage = currentPage - 1  # adjust for 0-based indexing in backend

    if len(skills) > 0 and skills != 'null':
        return candidates.searchCandidatesBySkillsNamesPaginated(search_string,skills,pageLimit,currentPage, domain=domain)
    else:
        return candidates.searchCandidatesByNameEmailPaginated(search_string,pageLimit,currentPage, domain=domain)
    
def _assert_candidate_domain(profileId: str, domain: str = None):
    if not domain or domain == "all":
        return
    candidate_domain = candidates.getCandidateDomain(profileId)
    if candidate_domain and candidate_domain != domain:
        raise HTTPException(status_code=403, detail="Candidate does not belong to this domain.")

def _profile_completion_status(profile: dict):
    personality = profile.get("personality") or []
    cultural_experience = profile.get("culturalExperience") or []
    core_profile = profile.get("profile") or {}
    skills = profile.get("skills") or []
    technical_skills = profile.get("technicalSkills") or []
    portfolio_experience = profile.get("portfolioExperience") or []

    def _level_value(item):
        try:
            return float(item.get("level") or 0)
        except (TypeError, ValueError):
            return 0

    has_personality = any(
        item and item.get("title") and item.get("score")
        for item in personality
    )
    has_culture = any(
        item and item.get("title") and _level_value(item) > 0
        for item in cultural_experience
    )
    has_regular_profile = bool(core_profile.get("title")) and bool(
        core_profile.get("description")
        or skills
        or technical_skills
        or any(item and (item.get("description") or item.get("mainrole")) for item in portfolio_experience)
    )
    missing = []
    if not has_regular_profile:
        missing.append("regular profile")
    if not has_personality:
        missing.append("personality survey")
    if not has_culture:
        missing.append("culture profile")
    checks = [has_regular_profile, has_personality, has_culture]
    state = "complete" if all(checks) else "partial" if any(checks) else "missing"

    return {
        "profileId": core_profile.get("id") or core_profile.get("personid"),
        "complete": all(checks),
        "state": state,
        "hasRegularProfile": has_regular_profile,
        "hasPersonality": has_personality,
        "hasCulture": has_culture,
        "missing": missing,
        "missingPieces": {
            "regular": not has_regular_profile,
            "personality": not has_personality,
            "culture": not has_culture,
        },
        "email": core_profile.get("email") or profile.get("email") or "",
        "name": " ".join(
            part for part in [core_profile.get("firstName"), core_profile.get("lastName")] if part
        ).strip(),
        "title": core_profile.get("title") or "",
    }

@router.get("/getProfile/{profileId}")
def get_profile(profileId: str = "", domain: str = None):
    print(f"Fetching profile {profileId}")
    _assert_candidate_domain(profileId, domain)
    profile = candidates.getProfile(profileId)
    if isinstance(profile, dict):
        profile["domain"] = candidates.getCandidateDomain(profileId)
    return profile

@router.get("/profile/completionStatus/{profileId}")
def get_profile_completion_status(profileId: str = "", domain: str = None):
    print(f"Checking profile completion status {profileId}")
    _assert_candidate_domain(profileId, domain)

    return _profile_completion_status(candidates.getProfile(profileId))

@router.get("/public/{profileUrl}")
def get_profile_public(profileUrl: str = ""):
    print(f"Fetching public profile {profileUrl}")
    profile = candidates.getProfilePublic(profileUrl)
    profile_id = (profile.get("profile") or {}).get("id") if isinstance(profile, dict) else None
    if isinstance(profile, dict) and profile_id:
        profile["domain"] = candidates.getCandidateDomain(profile_id)
    return profile

@router.get("/public/getPublicUrl/{profileId}")
def get_profile_public_url(profileId: str = ""):
    print(f"Fetching public URL for profile {profileId}")

    return candidates.getProfilePublicUrl(profileId)

@router.get("/getProfile/short/{profileId}")
def get_profile_short(profileId: str = "", domain: str = None):
    print(f"Fetching profile {profileId}")
    _assert_candidate_domain(profileId, domain)

    return candidates.getProfileShort(profileId)

@router.post("/getProfile/short/score/{jobId}")
def get_profile_short_score(jobId: str = "", profileIds: str = Form(...), domain: str = Form(default="dev")):
    print(f"Fetching profiles {profileIds}")

    for profile_id in profileIds.split(','):
        if profile_id:
            _assert_candidate_domain(profile_id, domain)
    return candidates.getProfileShortScore(jobId, profileIds.split(','), domain)

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
    if not os.getenv("OPENAI_API_KEY"):
        return {"title": key, "years": 1}
    try:
        years = processSkillYears(raw, key)
    except Exception:
        years = 1
    return {"title": key, "years": years}

def _infer_resume_source_type(filename: str, source_type: str = None) -> str:
    st = (source_type or "").strip().lower()
    if st in {"pdf", "docx"}:
        return st

    ext = os.path.splitext((filename or "").lower())[1].lstrip(".")
    if ext in {"pdf", "docx"}:
        return ext
    if ext == "doc":
        raise HTTPException(status_code=400, detail="Legacy .doc resumes are not supported. Please upload a PDF or DOCX.")
    raise HTTPException(status_code=400, detail="Unsupported resume type. Please upload a PDF or DOCX.")

def _safe_ai(callable_obj, fallback):
    try:
        value = callable_obj()
        return value if value not in (None, "") else fallback
    except Exception as exc:
        print(f"Resume AI enrichment skipped: {exc}")
        traceback.print_exc()
        return fallback

def _flatten_profile_skills(profile: dict) -> list[str]:
    flat = []
    for skills in (profile.get("skills") or {}).values():
        if isinstance(skills, list):
            flat.extend([str(skill).strip() for skill in skills if str(skill).strip()])
    return sorted(set(flat))

def _normalize_ai_skills(ai_profile: dict) -> list[dict]:
    normalized = []
    seen = set()
    for skill in ai_profile.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        title = str(skill.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        try:
            years = int(skill.get("years") or 1)
        except (TypeError, ValueError):
            years = 1
        normalized.append({"title": title, "years": max(1, min(years, 40))})
    return normalized

def _merge_resume_skills(ai_skills: list[dict], deterministic_skill_names: list[str]) -> list[dict]:
    merged = []
    seen = set()
    for skill in ai_skills or []:
        title = str(skill.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        merged.append(skill)
    for title in deterministic_skill_names or []:
        clean_title = str(title or "").strip()
        if not clean_title or clean_title.lower() in seen:
            continue
        seen.add(clean_title.lower())
        merged.append({"title": clean_title, "years": 1})
    return merged

def _normalize_culture_items(value) -> list[dict]:
    if isinstance(value, str):
        items = [
            {"experience": part.strip(), "level": 1}
            for part in value.split(",")
            if part.strip()
        ]
        return items[:6]
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str):
            title = item.strip()
            level = 1
        elif isinstance(item, dict):
            title = str(item.get("experience") or item.get("title") or "").strip()
            try:
                level = int(item.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
        else:
            continue
        if title:
            items.append({"experience": title, "level": max(1, min(level, 3))})
    return items[:6]

def _normalize_portfolio_items(value) -> list[dict]:
    if not isinstance(value, list):
        return []

    def _truthy(raw_value) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value or "").strip().lower() in {"true", "yes", "1", "present", "current"}

    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = item.get("startDate")
        finish = item.get("finishDate")
        try:
            start_year = int(start) if start not in (None, "") else None
        except (TypeError, ValueError):
            start_year = None
        try:
            finish_year = int(finish) if finish not in (None, "") else None
        except (TypeError, ValueError):
            finish_year = None
        if start_year and (start_year < 1950 or start_year > 2100):
            start_year = None
        if finish_year and (finish_year < 1950 or finish_year > 2100):
            finish_year = None
        normalized.append({
            "companyName": str(item.get("companyName") or "").strip() or "Company not listed",
            "mainRole": str(item.get("mainRole") or "").strip() or "Role not listed",
            "description": str(item.get("description") or "").strip(),
            "startDate": start_year,
            "finishDate": finish_year,
            "isPresent": _truthy(item.get("isPresent")),
            "skills": [str(skill).strip() for skill in item.get("skills") or [] if str(skill).strip()],
            "features": [str(feature).strip() for feature in item.get("features") or [] if str(feature).strip()],
        })
    return normalized[:12]

def _resume_portfolio_items(raw: str, ai_profile: dict | None) -> list[dict]:
    ai_items = _normalize_portfolio_items((ai_profile or {}).get("portfolio"))
    fallback_items = _normalize_portfolio_items(extract_portfolio(raw or ""))
    if ai_items and len(ai_items) >= len(fallback_items):
        return ai_items
    return fallback_items

def _fallback_description(profile: dict) -> str:
    skills = _flatten_profile_skills(profile)
    name = (profile.get("contact") or {}).get("full_name") or "Candidate"
    headline = (profile.get("summary") or {}).get("headline") or "Resume generated profile"
    skill_text = ", ".join(skills[:10]) if skills else "skills to be confirmed"
    return f"{name} is a {headline}. Resume parsing found experience related to {skill_text}."

def _fallback_culture(profile: dict) -> list[dict]:
    skills = _flatten_profile_skills(profile)
    if not skills:
        return [{"experience": "Technology Delivery", "level": 1}]
    families = [key.replace("_", " ").title() for key, vals in (profile.get("skills") or {}).items() if vals]
    return [{"experience": family, "level": 1} for family in families[:5]] or [{"experience": "Technology Delivery", "level": 1}]

def _split_city_state_country(location: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in (location or "").split(",") if part.strip()]
    city = parts[0] if len(parts) > 0 else ""
    state = parts[1] if len(parts) > 1 else ""
    country = parts[2] if len(parts) > 2 else ""
    return city, state, country

def _existing_profile_from_email(email: str, domain: str):
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return None
    try:
        matches = candidates.searchCandidatesByNameEmail(clean_email, limit=5, domain=domain)
        for match in matches:
            if (match.get("email") or "").strip().lower() == clean_email:
                name = " ".join([match.get("firstName") or "", match.get("lastName") or ""]).strip()
                return {
                    "status": "success",
                    "message": "Existing profile found for this resume email.",
                    "personid": match.get("id"),
                    "name": name or clean_email,
                    "existing": True,
                }
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        print(f"Existing email lookup skipped: {exc}")
        traceback.print_exc()
    return None

@router.post("/resume/upload")
async def upload_resume(
    file: UploadFile = File(...),
    source_type: str = Form(None),
    domain: str = Form(default="dev"),
):
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file name received.")

        resolved_source_type = _infer_resume_source_type(file.filename, source_type)
        file_bytes = await file.read()
        raw = ingest(resolved_source_type, file_bytes)
        if not (raw or "").strip():
            raise HTTPException(status_code=400, detail="Could not extract resume text. Please upload a text-based PDF or DOCX.")

        profile = build_profile_from_text(raw)
        ai_profile = None
        if os.getenv("OPENAI_API_KEY"):
            ai_profile = _safe_ai(lambda: normalize_ai_resume_profile(raw, domain), None)

        contact = profile.get("contact") or {}
        summary = profile.get("summary") or {}
        ai_contact = (ai_profile or {}).get("contact") or {}
        full_name = ai_contact.get("full_name") or contact.get("full_name") or "Candidate"
        email = ai_contact.get("email") or contact.get("email") or ""
        linkedin = ai_contact.get("linkedin") or contact.get("linkedin") or ""
        city, state, country = _split_city_state_country(contact.get("location") or "")
        city = ai_contact.get("city") or city
        state = ai_contact.get("state") or state
        country = ai_contact.get("country") or country
        flat_skill_names = _flatten_profile_skills(profile)

        aiSkills = _normalize_ai_skills(ai_profile or {})
        if not aiSkills and flat_skill_names:
            with ThreadPoolExecutor(max_workers=5) as executor:
                aiSkills = list(executor.map(process_skill, [raw] * len(flat_skill_names), flat_skill_names))
        flatSkills = _merge_resume_skills(aiSkills, flat_skill_names)

        has_openai_key = bool(os.getenv("OPENAI_API_KEY"))
        description = (ai_profile or {}).get("summary") or _fallback_description(profile)
        culturalExperiences = _normalize_culture_items((ai_profile or {}).get("culture")) or _fallback_culture(profile)
        portfolioExperiences = _resume_portfolio_items(raw, ai_profile)
        candidateCity = city
        candidateState = state
        candidateCountry = country
        candidateTitle = (ai_profile or {}).get("headline") or summary.get("headline") or "Resume generated profile"

        existingProfile = _existing_profile_from_email(email, domain)
        if existingProfile:
            if portfolioExperiences:
                try:
                    candidates.replaceCandidatePortfolio(existingProfile["personid"], portfolioExperiences)
                    existingProfile["portfolio_updated"] = True
                except Exception as exc:
                    print(f"Portfolio update skipped for existing profile: {exc}")
                    traceback.print_exc()
                    existingProfile["portfolio_warning"] = f"Existing profile selected, but portfolio was not updated: {exc}"
            if flatSkills:
                try:
                    candidates.replaceCandidateSkills(existingProfile["personid"], flatSkills)
                    existingProfile["skills_updated"] = True
                except Exception as exc:
                    print(f"Skill update skipped for existing profile: {exc}")
                    traceback.print_exc()
                    existingProfile["skills_warning"] = f"Existing profile selected, but skills were not updated: {exc}"
            try:
                from starlette.datastructures import UploadFile as StarletteUploadFile
                from io import BytesIO
                resume_upload = StarletteUploadFile(filename=file.filename, file=BytesIO(file_bytes))
                existingProfile["resume"] = await resumes.uploadResume(resume_upload, existingProfile["personid"])
            except Exception as exc:
                print(f"Resume file storage skipped for existing profile: {exc}")
                traceback.print_exc()
                existingProfile["resume"] = None
                existingProfile["resume_warning"] = f"Existing profile selected, but the original resume file was not stored: {exc}"
            return existingProfile

        if has_openai_key and not ai_profile:
            with ThreadPoolExecutor(max_workers=6) as executor:
                description_future = executor.submit(candidateDescription, raw)
                cultural_future = executor.submit(candidateCulturalExperience, raw)
                city_future = executor.submit(processGeneral, raw, "currently lived in city (DO NOT RETURN PROVINCE OR STATE. DO NOT RETURN ASSOCIATED JOBS OR COMPANIES. ONLY RETURN CITY NAME)")
                state_future = executor.submit(processGeneral, raw, "currently lived in state or province (DO NOT RETURN CITY. DO NOT RETURN ASSOCIATED JOBS OR COMPANIES. ONLY RETURN STATE OR PROVINCE NAME. RETURN NO ADDITIONAL COMMENTARY)")
                country_future = executor.submit(processGeneral, raw, "currently lived in country (DO NOT RETURN CITY, STATE OR PROVINCE. ONLY RETURN COUNTRY NAME)")
                title_future = executor.submit(processGeneral, raw, "current or most recent job title (DO NOT RETURN ANY ASSOCIATED COMPANIES OR JOBS. ONLY RETURN JOB TITLE. RETURN NO ADDITIONAL COMMENTARY)")

            description = _safe_ai(description_future.result, description)
            culturalExperiences = _normalize_culture_items(_safe_ai(cultural_future.result, culturalExperiences)) or culturalExperiences
            candidateCity = _safe_ai(city_future.result, candidateCity)
            candidateState = _safe_ai(state_future.result, candidateState)
            candidateCountry = _safe_ai(country_future.result, candidateCountry)
            candidateTitle = _safe_ai(title_future.result, candidateTitle)

        profileResult = candidates.uploadProfile(
            skills=flatSkills,
            fullName=full_name,
            domain=domain,
            email=email,
            linkedInUrl=linkedin,
            candidateDescription=description,
            culturalExperiences=culturalExperiences,
            candidateCity=candidateCity,
            candidateState=candidateState,
            candidateCountry=candidateCountry,
            candidateTitle=candidateTitle,
            portfolioExperiences=portfolioExperiences,
        )

        from starlette.datastructures import UploadFile as StarletteUploadFile
        from io import BytesIO
        resume_upload = StarletteUploadFile(filename=file.filename, file=BytesIO(file_bytes))
        try:
            resumeResult = await resumes.uploadResume(resume_upload, profileResult["personid"])
            profileResult["resume"] = resumeResult
        except Exception as exc:
            print(f"Resume file storage skipped after profile creation: {exc}")
            traceback.print_exc()
            profileResult["resume"] = None
            profileResult["resume_warning"] = f"Profile was created, but the original resume file was not stored: {exc}"

        return profileResult
    except HTTPException:
        raise
    except Exception as exc:
        print(f"Resume upload failed: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Resume upload failed: {exc}")

@router.get("/resume/{profileId}")
async def get_resume(profileId: str):
    print(f"Getting resume for profile {profileId}")
    return await resumes.getResume(int(profileId))
