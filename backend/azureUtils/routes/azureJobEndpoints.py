from fastapi import APIRouter, Body, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import traceback
import os
import requests
from azureUtils.storage import jobs, candidates
from jd_match import normalize_jd, azureJobMatch, normalize_all_skills
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

def _safe_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []

def _searchable_job_skills(job_skills: list[str], limit: int = 10):
    preferred = []
    fallback = []
    noisy = {"clean", "performance", "pm", "flows", "rere"}
    replacements = {
        "python3": "Python",
        "python / django": "Python Django",
        "javascript (vanilla)": "JavaScript",
        "java,": "Java",
        "git/gitlab/github": "GitHub GitLab",
        "git hub": "GitHub",
        "github / gitlab": "GitHub GitLab",
        ".net /.net core": ".NET Core",
        "microsoft azure": "Azure",
        "google cloud platform (big query)": "Google Cloud BigQuery",
        "aws/aurora postgresql": "AWS PostgreSQL",
        "databases sql and nosql": "SQL NoSQL",
    }
    known_terms = [
        ("python", "Python"),
        ("django", "Django"),
        ("javascript", "JavaScript"),
        ("java", "Java"),
        ("c++", "C++"),
        (".net", ".NET"),
        ("aws", "AWS"),
        ("azure", "Azure"),
        ("gcp", "GCP"),
        ("google cloud", "Google Cloud"),
        ("github", "GitHub"),
        ("gitlab", "GitLab"),
        ("sql", "SQL"),
        ("postgres", "PostgreSQL"),
        ("redshift", "Redshift"),
        ("bigquery", "BigQuery"),
        ("big query", "BigQuery"),
        ("chakra", "Chakra UI"),
        ("code review", "Code Review"),
        ("full-stack", "Full Stack"),
        ("web application", "Web Application"),
    ]

    for raw in _safe_list(job_skills):
        lower = raw.lower().strip()
        if lower in noisy:
            continue
        if any(ch.isdigit() for ch in lower) and not any(tech in lower for tech in ["c++", ".net", "s3"]):
            continue
        if len(lower) < 4 and lower not in {"c++", "sql", "aws", "gcp"}:
            continue
        if not any(ch.isalpha() for ch in lower):
            continue
        skill = replacements.get(lower)
        if not skill:
            skill = next((label for token, label in known_terms if token in lower), raw.strip())
        target = preferred if any(token in lower for token, _label in known_terms) or lower in replacements else fallback
        if skill not in preferred and skill not in fallback:
            target.append(skill)

    normalized = preferred + fallback
    return normalized[:limit] or _safe_list(job_skills)[:limit]

def _rank_external_skill_match(raw_skills: list[str], job_skills: list[str], scoring_skills: list[str] | None = None):
    candidate_skills = _safe_list(raw_skills)
    skill_basis = _safe_list(scoring_skills) or _safe_list(job_skills)
    job_skill_count = len(skill_basis) or 1
    matched = []
    lowered_candidate = [skill.lower() for skill in candidate_skills]

    for skill in skill_basis:
        needle = skill.lower()
        if len(needle) <= 3:
            matched_skill = any(needle == haystack for haystack in lowered_candidate)
        else:
            matched_skill = any(needle == haystack or needle in haystack or haystack in needle for haystack in lowered_candidate)
        if matched_skill:
            matched.append(skill)

    return round(len(set(matched)) / job_skill_count * 100), list(dict.fromkeys(matched))

def _people_data_row(row: dict, job_skills: list[str], scoring_skills: list[str]):
    skills = _safe_list(row.get("skills"))
    score, top_matches = _rank_external_skill_match(skills, job_skills, scoring_skills)
    first = row.get("first_name") or ""
    last = row.get("last_name") or ""
    name = (first + " " + last).strip() or row.get("full_name") or "Unknown candidate"
    linkedin_url = row.get("linkedin_url") or ""
    if linkedin_url and not linkedin_url.startswith("http"):
        linkedin_url = "https://www." + linkedin_url.lstrip("/")
    email = row.get("recommended_personal_email") or row.get("work_email") or ""
    if not isinstance(email, str):
        email = ""
    location = row.get("location_name") or ", ".join([v for v in [row.get("location_locality"), row.get("location_region"), row.get("location_country")] if isinstance(v, str) and v])
    if not isinstance(location, str):
        location = ""

    return {
        "source": "pdl",
        "source_label": "People Data Labs",
        "source_id": row.get("id") or "",
        "name": name,
        "email": email,
        "title": row.get("job_title") or row.get("title") or "",
        "company": row.get("job_company_name") or "",
        "location": location,
        "profile_url": linkedin_url,
        "avatar_url": "",
        "summary": row.get("summary") or row.get("headline") or "",
        "skills": skills,
        "score": score,
        "top_matches": top_matches,
        "profile_data": {
            "experience": row.get("experience", [])[:5] if isinstance(row.get("experience"), list) else [],
            "education": row.get("education", [])[:3] if isinstance(row.get("education"), list) else [],
            "certifications": row.get("certifications", [])[:5] if isinstance(row.get("certifications"), list) else [],
            "github_url": row.get("github_url") or "",
        },
    }

def _github_headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def _github_search(job_skills: list[str], scoring_skills: list[str], size: int = 5):
    selected_skills = _safe_list(scoring_skills)[:8]
    seen_logins = set()
    search_queries = []
    candidate_logins = []
    candidate_pool_size = max(size * 5, 12)

    for skill in selected_skills:
        if len(candidate_logins) >= candidate_pool_size:
            break

        repo_query = f"{skill} stars:>3"
        search_queries.append(repo_query)
        repo_search_response = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": repo_query, "sort": "stars", "order": "desc", "per_page": 5},
            headers=_github_headers(),
            timeout=12,
        )
        if repo_search_response.status_code >= 400:
            raise Exception(f"GitHub repository search failed with status {repo_search_response.status_code}: {repo_search_response.text[:180]}")

        for repo in repo_search_response.json().get("items", []):
            owner = (repo.get("owner") or {}).get("login")
            if owner and owner not in seen_logins:
                seen_logins.add(owner)
                candidate_logins.append(owner)
            if len(candidate_logins) >= candidate_pool_size:
                break

        if len(candidate_logins) >= candidate_pool_size:
            break

        query = f"{skill} in:bio type:user"
        search_queries.append(query)
        search_response = requests.get(
            "https://api.github.com/search/users",
            params={"q": query, "per_page": min(size * 2, 10)},
            headers=_github_headers(),
            timeout=12,
        )
        if search_response.status_code >= 400:
            raise Exception(f"GitHub search failed with status {search_response.status_code}: {search_response.text[:180]}")

        search_items = search_response.json().get("items", [])
        for item in search_items:
            login = item.get("login")
            if not login or login in seen_logins:
                continue
            seen_logins.add(login)
            candidate_logins.append(login)
            if len(candidate_logins) >= candidate_pool_size:
                break

    enriched_rows = []
    for login in candidate_logins:
        if len(enriched_rows) >= size:
            break
        if not login:
            continue

        user_response = requests.get(
            f"https://api.github.com/users/{login}",
            headers=_github_headers(),
            timeout=12,
        )
        user = user_response.json() if user_response.status_code == 200 else item
        if (user.get("type") or "").lower() != "user":
            continue

        repo_response = requests.get(
            f"https://api.github.com/users/{login}/repos",
            params={"sort": "updated", "per_page": 8},
            headers=_github_headers(),
            timeout=12,
        )
        repos = repo_response.json() if repo_response.status_code == 200 else []
        if not isinstance(repos, list):
            repos = []

        repo_skills = []
        for repo in repos:
            repo_skills.extend(_safe_list(repo.get("topics")))
            if repo.get("language"):
                repo_skills.append(repo.get("language"))

        repo_text = " ".join(
            [
                " ".join(
                    [
                        str(repo.get("name") or ""),
                        str(repo.get("description") or ""),
                        str(repo.get("language") or ""),
                        " ".join(_safe_list(repo.get("topics"))),
                    ]
                )
                for repo in repos
            ]
        )
        candidate_text = " ".join([str(user.get("bio") or ""), repo_text]).lower()
        inferred_skills = list(dict.fromkeys(
            [skill for skill in job_skills if skill.lower() in candidate_text]
            + [skill for skill in selected_skills if skill.lower() in candidate_text]
            + repo_skills
        ))
        score, top_matches = _rank_external_skill_match(inferred_skills, job_skills, selected_skills)

        enriched_rows.append({
            "source": "github",
            "source_label": "GitHub",
            "source_id": login,
            "name": user.get("name") or login,
            "email": user.get("email") or "",
            "title": "",
            "company": user.get("company") or "",
            "location": user.get("location") or "",
            "profile_url": user.get("html_url") or f"https://github.com/{login}",
            "avatar_url": user.get("avatar_url") or "",
            "summary": user.get("bio") or "",
            "skills": inferred_skills,
            "score": score,
            "top_matches": top_matches,
            "repo_count": user.get("public_repos") or len(repos),
            "recent_repos": [
                {
                    "name": repo.get("name"),
                    "language": repo.get("language"),
                    "description": repo.get("description"),
                    "url": repo.get("html_url"),
                }
                for repo in repos[:5]
            ],
            "profile_data": {
                "github_login": login,
                "bio": user.get("bio") or "",
                "blog": user.get("blog") or "",
                "followers": user.get("followers") or 0,
                "public_repos": user.get("public_repos") or len(repos),
                "repos": [
                    {
                        "name": repo.get("name"),
                        "language": repo.get("language"),
                        "topics": _safe_list(repo.get("topics")),
                        "description": repo.get("description"),
                        "stars": repo.get("stargazers_count") or 0,
                        "url": repo.get("html_url"),
                    }
                    for repo in repos[:8]
                ],
            },
            "search_queries": search_queries,
        })

    enriched_rows.sort(key=lambda row: row["score"], reverse=True)
    return enriched_rows

def _get_job_skills(jd_id: str):
    jd = jobs.getJob(jd_id)
    if not jd:
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    job_skills = list(dict.fromkeys(_safe_list(jd.get("skills"))))
    if not job_skills:
        job_skills = externalPeopleSearch.getPeopleSkills(jd.get("description") or "")
    return jd, job_skills

@router.post("/createJob")
def jdCreate(company: str = Form(...), title: str = Form(...), jd_text: str = Form(...), domain: str = Form(default="dev")):
    print(f"Uploading {title} at {company}")
    try:
        # Deprecated
        # skills = normalize_jd(jd_text)
        flatSkills = normalize_all_skills(jd_text)

        # Get all skills from JD
        #for key, value in skills.items():
            #flatSkills.extend(value)

        flatSkills = list(set(flatSkills))  # unique skills

        created = jobs.uploadJob(company, title, domain, jd_text, flatSkills) or {}
        return {"jd_id": created.get("jd_id"), "company": company, "title": title, "domain": domain, "jd_skills": flatSkills, "jd_text": jd_text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": 'Failed to upload job description.', "trace": traceback.format_exc()})

@router.get("/list/{domain}/{amount}")
def jd_list(domain: str = "dev", amount: int = 5):
    return jobs.listJobs(domain, amount)
    
@router.get("/list/search/{domain}/{query}/{amount}")
def jd_list(domain: str = "dev", query: str = '', amount: int = 5):
    return jobs.searchJobs(domain, query, amount)

@router.get("/getJob/{jobId}")
def jd_get(jobId: str):
    jd = jobs.getJob(jobId)
    if not jd:
        raise HTTPException(status_code=404, detail="Job not found.")
    return jd

@router.post("/match/run")
def run_match(domain: str = Form(default="dev"), jd_id: str = Form(None), top_k: int = Form(10), external_source: str = Form(default="none")):
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
    
    # TODO: Figure out why some jobs have duplicates on upload
    peopleDataSkills = list(set(peopleDataSkills))  # ensure unique skills

    returnedExternalPeople = []

    # TODO: Get location search working
    print('No location extracted from JD. Running external search based on skills only.')
    try:
        if external_source == "pdl":
            returnedExternalPeople = peopleDataLabs.searchSkills(peopleDataSkills, 1)["data"]
        elif external_source == "github":
            pass  # TODO: Implement Github external search
        else:
            print('No external source selected or source not recognized. Skipping external search.')
    except Exception as e:
        print(f'Error during external people search: {e}')

    profiles = candidates.searchCandidatesBySkillId(jd["skillIds"], top_k)

    jobSkillCount = len(peopleDataSkills) or 1  # avoid division by zero
    ranked = []
    for row in profiles:
        #p = storage.get_profile(DB_PATH, row["profile_id"])
        #score, parts = match((p or {}).get("skills", {}), jd_skills)
        #score, parts = azureJobMatch(row['skillMatches'],peopleDataSkills)

        print(f"Matching profile {row['id']} - {row['firstName']} {row['lastName']}")

        skillMatchCount = 0
        top_matches = []
        for skill in peopleDataSkills:
            if skill.lower() in (s.lower() for s in row['skillMatches']):
                top_matches.append(skill)
                skillMatchCount += 1
                print(f"Matched skill: {skill}")

        print(f"Total matched skills: {skillMatchCount} out of {jobSkillCount}")
        print(skillMatchCount / jobSkillCount)
        score = round(skillMatchCount / jobSkillCount * 100)

        print("\n")
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
            "top_matches": top_matches,
            "breakdown": row['skillMatches'],
            'culture_match': percentageNum
        })

    ranked.sort(key=lambda x: (x["score"], x["culture_match"]), reverse=True)

    rankedExternal = []
    for row in returnedExternalPeople:
        #score, parts = azureJobMatch(row['skills'],peopleDataSkills)
        

        inferredSalary = None
        if "inferred_salary" in row:
            inferredSalary = row["inferred_salary"]

        skillMatchCount = 0
        top_matches = []
        for skill in peopleDataSkills:
            if skill.lower() in (s.lower() for s in row['skills']):
                top_matches.append(skill)
                skillMatchCount += 1

        score = round(skillMatchCount / jobSkillCount * 100)
        
        rankedExternal.append({
            "profile_id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "recommended_personal_email": row["recommended_personal_email"],
            "linkedin_url": row["linkedin_url"],
            "inferred_salary": inferredSalary,
            "score": score,
            "top_matches": top_matches,
            "breakdown": row['skills']
        })

    rankedExternal.sort(key=lambda x: x["score"], reverse=True)
    return {"jd": {"jd_id": jd["jd_id"], "company": jd.get("company",""), "title": jd.get("title",""), "created_at": jd.get("created_at","")}, "results": ranked[:top_k], "externalMatches": rankedExternal, "skillList": peopleDataSkills}

@router.post("/external/search")
def external_candidate_search(
    domain: str = Form(default="dev"),
    jd_id: str = Form(...),
    source: str = Form(default="pdl"),
    top_k: int = Form(default=10),
):
    jd, job_skills = _get_job_skills(jd_id)
    search_skills = _searchable_job_skills(job_skills, 12)
    selected_source = (source or "pdl").strip().lower()
    results = []

    try:
        if selected_source == "pdl":
            pdl_response = peopleDataLabs.searchSkills(search_skills, top_k)
            results = [_people_data_row(row, job_skills, search_skills) for row in pdl_response.get("data", [])]
        elif selected_source == "github":
            results = _github_search(job_skills, search_skills, top_k)
        else:
            raise HTTPException(status_code=400, detail="Select People Data Labs or GitHub.")
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={
                "detail": f"{'GitHub' if selected_source == 'github' else 'People Data Labs'} search failed: {str(e)}",
                "jobSkills": job_skills,
            },
        )

    results.sort(key=lambda row: row.get("score", 0), reverse=True)
    return {
        "jd": {
            "jd_id": jd["jd_id"],
            "company": jd.get("company", ""),
            "title": jd.get("title", ""),
        },
        "source": selected_source,
        "jobSkills": job_skills,
        "searchSkills": search_skills,
        "results": results[:top_k],
        "searchUsesJobDescription": True,
    }

@router.post("/external/import")
def external_candidate_import(payload: dict = Body(...)):
    domain = payload.get("domain") or "dev"
    candidate = payload.get("candidate") or {}
    source = candidate.get("source") or payload.get("source") or "external"

    skills = [
        {"title": skill, "years": 0}
        for skill in _safe_list(candidate.get("skills"))
    ]
    full_name = candidate.get("name") or "External Candidate"
    profile_url = candidate.get("profile_url") or ""
    summary_parts = [
        candidate.get("summary") or "",
        "Temporary external profile. Confirm details before publishing.",
        f"Imported from {candidate.get('source_label') or source}.",
        f"Relevant matches: {', '.join(_safe_list(candidate.get('top_matches')))}",
    ]
    if source == "github":
        repos = ((candidate.get("profile_data") or {}).get("repos") or [])[:5]
        repo_lines = [
            f"{repo.get('name')}: {repo.get('language') or 'Unknown'} - {repo.get('description') or ''}".strip()
            for repo in repos
        ]
        if repo_lines:
            summary_parts.append("GitHub evidence:\n" + "\n".join(repo_lines))
    description = "\n".join([part for part in summary_parts if part])

    try:
        created = candidates.uploadProfile(
            skills=skills,
            fullName=full_name,
            candidateDescription=description,
            domain=domain,
            email=candidate.get("email") or None,
            linkedInUrl=profile_url,
            candidateCity=None,
            candidateState=None,
            candidateCountry=candidate.get("location") or None,
            candidateTitle=candidate.get("title") or "",
        )
        created["source"] = source
        created["temporaryProfile"] = True
        created["importedSkills"] = [skill["title"] for skill in skills]
        return created
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Unable to create profile from external candidate: {str(e)}"},
        )

@router.delete("/external/temp/{person_id}")
def external_candidate_delete(person_id: str):
    return candidates.deleteTemporaryExternalProfile(person_id)
