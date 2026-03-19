from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import os, shutil, traceback
from azure.storage import jobs
from jd_match import normalize_jd

router = APIRouter(
    prefix="/api/azureJobs",
    tags=["azure", "jobs"]
)
    
@router.post("/createJob")
def jdCreate(company: str = Form(...), title: str = Form(...), jd_text: str = Form(...), domain: str = Form("technology")):
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
def jd_list(domain: str = "technology", amount: int = 5):
    return jobs.listJobs(domain, amount)
    
@router.get("/list/search/{domain}/{query}/{amount}")
def jd_list(domain: str = "technology", query: str = '', amount: int = 5):
    return jobs.searchJobs(domain, query, amount)