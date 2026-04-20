from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import os, shutil, traceback
from azureUtils.storage import jobs, candidates
from jd_match import normalize_jd, azureJobMatch
from openAI import externalPeopleSearch
import peopleDataLabs.peopleSearch as peopleDataLabs

def top_matches_from_parts(parts: dict, limit: int = 8):
    """
    Bring the most relevant matched skills to the surface for the UI.
    Order by group weight (languages/backend/frontend/cloud_devops/data/testing/security).
    """
    if not parts:
        return []
    order = ["languages","backend","frontend","cloud_devops","data","testing","security"]
    out = []
    seen = set()
    for g in order:
        for s in (parts.get(g, {}) or {}).get("matched", []) or []:
            if s not in seen:
                out.append(s)
                seen.add(s)
            if len(out) >= limit:
                return out
    return out

router = APIRouter(
    prefix="/api/azureJobs",
    tags=["azure", "jobs"]
)

@router.post("/createJob")
def jdCreate(company: str = Form(...), title: str = Form(...), jd_text: str = Form(...), domain: str = Form(default="dev")):
    print(f"Uploading {title} at {company}")
    try:
        skills = normalize_jd(jd_text)
        flatSkills = []

        # Get all skills from JD
        for key, value in skills.items():
            flatSkills.extend(value)

        flatSkills = list(set(flatSkills))  # unique skills

        jobs.uploadJob(company, title, domain, jd_text, flatSkills)
        return {"company": company, "title": title, "domain": domain, "jd_skills": skills, "jd_text": jd_text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": 'Failed to upload job description.', "trace": traceback.format_exc()})

@router.get("/list/{domain}/{amount}")
def jd_list(domain: str = "dev", amount: int = 5):
    return jobs.listJobs(domain, amount)
    
@router.get("/list/search/{domain}/{query}/{amount}")
def jd_list(domain: str = "dev", query: str = '', amount: int = 5):
    return jobs.searchJobs(domain, query, amount)

@router.post("/match/run")
def run_match(domain: str = Form(default="dev"), jd_id: str = Form(None), top_k: int = Form(10)):
    # TODO: Set up job descriptions in the database
    jd = jobs.getJob(jd_id)

    if not jd:
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    
    peopleDataSkills = []
    if not jd["skills"]:
        peopleDataSkills = externalPeopleSearch.getPeopleSkills(jd["jd_text"])
        # TODO: Upload skills to database
    else:
        peopleDataSkills = jd["skills"]
    
    returnedExternalPeople = []

    # TODO: Get location search working
    print('No location extracted from JD. Running external search based on skills only.')
    try:
        returnedExternalPeople = peopleDataLabs.searchSkills(peopleDataSkills, 1)["data"]
    except Exception as e:
        print(f'Error during external people search: {e}')

    profiles = candidates.searchCandidatesBySkillId(jd["skillIds"], top_k)

    ranked = []
    for row in profiles:
        #p = storage.get_profile(DB_PATH, row["profile_id"])
        #score, parts = match((p or {}).get("skills", {}), jd_skills)
        score, parts = azureJobMatch(row['skillMatches'],peopleDataSkills)

        # Set empty and negative values for easy existance checking
        personalityDifferences = []
        averageDifference = -1
        percentageNum = -1

        for personality in row['personality']:
            # Get the stat that matches the current one
            matchingStat = next((i for i in jd['personalities'] if i['title'] == personality['title']),None)
            personalityDifferences.append(abs(matchingStat['score']-personality['score']))

        if len(personalityDifferences)>0:
            averageDifference = sum(personalityDifferences)/len(personalityDifferences)
            # numbers closer to zero are better and scale is of 5, so take percentage out of five, then subtract from 1 to determine closeness to zero
            percentageNum = round((1-(averageDifference/5))*100)
        
        ranked.append({
            "profile_id": row["id"],
            "name": row["firstName"] + ' ' + row["lastName"],
            "email": row["email"],
            "score": score,
            "top_matches": top_matches_from_parts(parts),
            "breakdown": parts,
            'culture_match': percentageNum
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    rankedExternal = []
    for row in returnedExternalPeople:
        score, parts = azureJobMatch(row['skills'],peopleDataSkills)
        inferredSalary = None
        if "inferred_salary" in row:
            inferredSalary = row["inferred_salary"]

        flattenedMatches = []

        for key, value in parts.items():
            if len(value['matched']) > 0:
                flattenedMatches = flattenedMatches + value['matched']
        
        rankedExternal.append({
            "profile_id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "recommended_personal_email": row["recommended_personal_email"],
            "linkedin_url": row["linkedin_url"],
            "inferred_salary": inferredSalary,
            "score": score,
            "top_matches": list(set(flattenedMatches)),
            "breakdown": parts
        })

    rankedExternal.sort(key=lambda x: x["score"], reverse=True)
    return {"jd": {"jd_id": jd["jd_id"], "company": jd.get("company",""), "title": jd.get("title",""), "created_at": jd.get("created_at","")}, "results": ranked[:top_k], "externalMatches": rankedExternal, "skillList": peopleDataSkills}
