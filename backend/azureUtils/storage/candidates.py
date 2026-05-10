from pydantic import BaseModel
from fastapi import HTTPException
import azureUtils.storage.client as client
import azureUtils.storage.processingFunctions as processing
from azureUtils.storage.jobs import getJob
from jd_match import azureJobMatch

def getSkills():
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT id, title FROM skill ORDER BY id DESC LIMIT 10;"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()

    skills = []

    for r in results:
        skills.append({
            "id": r[0],
            "title": r[1]
        })
    
    return skills

def searchSkills(searchQuery: str):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT id, title FROM skill WHERE title ILIKE '%{searchQuery}%' ORDER BY id DESC LIMIT 10;"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()

    skills = []

    for r in results:
        skills.append({
            "id": r[0],
            "title": r[1]
        })
    
    return skills

def countCandidates(domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()
    
    query = ''
    # Count distinct candidates in the person table
    if domain == 'all':
        query = f"SELECT COUNT(id) as count FROM person;"
    else:
        query = f"SELECT COUNT(id) as count FROM person WHERE domain = '{domain}';"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()
    
    return results[0][0]

def countCandidatesRecent(domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()

    query = ''
    # Count candidates added in the last 7 days
    if domain == 'all':
        query = f"SELECT COUNT(person.id) as count FROM person JOIN professional ON person.id = professional.id WHERE professional.modifieddate >= CURRENT_DATE - INTERVAL '7 days';"
    else:
        query = f"SELECT COUNT(person.id) as count FROM person JOIN professional ON person.id = professional.id WHERE person.domain = '{domain}' AND professional.modifieddate >= CURRENT_DATE - INTERVAL '7 days';"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()
    
    return results[0][0]

def countCandidatesStatus(domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()

    # Status Guide:
    # 1 = Draft
    # 2 = Pending
    # 3 = Published
    # 4 = Updated
    query = ''
    if domain == 'all':
        query = f"SELECT professional.status, COUNT(person.id) as count FROM person JOIN professional ON person.id = professional.id GROUP BY professional.status;"
    else:
        query = f"SELECT professional.status, COUNT(person.id) as count FROM person JOIN professional ON person.id = professional.id WHERE person.domain = '{domain}' GROUP BY professional.status;"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()

    resultsObject = {
        "Draft": 0,
        "Pending": 0,
        "Published": 0,
        "Updated": 0
        }

    for r in results:
        if r[0] == 1:
            resultsObject["Draft"] = r[1]
        elif r[0] == 2:
            resultsObject["Pending"] = r[1]
        elif r[0] == 3:
            resultsObject["Published"] = r[1]
        elif r[0] == 4:
            resultsObject["Updated"] = r[1]
    
    return resultsObject

def countCandidatesAll(domain: str = 'all'):
    totalCount = countCandidates(domain)
    recentCount = countCandidatesRecent(domain)
    statusCounts = countCandidatesStatus(domain)

    return {
        "total": totalCount,
        "recent": recentCount,
        "statusCounts": statusCounts
    }

def _skill_family(skill_title: str):
    lower = (skill_title or "").lower()
    families = {
        "AI / ML": ["artificial intelligence", "machine learning", " ml ", "llm", "openai", "langchain", "tensorflow", "pytorch", "computer vision", "nlp"],
        "Data": ["data", "sql", "postgres", "mysql", "snowflake", "redshift", "bigquery", "etl", "analytics", "tableau", "power bi", "spark", "warehouse"],
        "Frontend": ["react", "angular", "vue", "javascript", "typescript", "html", "css", "chakra", "frontend", "front-end", "ui", "ux"],
        "Backend": ["python", "django", "flask", "fastapi", "java", "spring", "node", "express", "c#", ".net", "api", "backend", "back-end", "microservice"],
        "Cloud / DevOps": ["aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ci/cd", "devops", "cloud", "jenkins", "github actions"],
        "Mobile": ["ios", "android", "swift", "kotlin", "react native", "flutter", "mobile"],
        "QA / Testing": ["qa", "test", "testing", "selenium", "cypress", "playwright", "automation"],
        "Product / Analysis": ["product", "business analyst", "ba", "scrum", "agile", "requirements", "stakeholder"],
    }
    for family, tokens in families.items():
        if any(token in lower for token in tokens):
            return family
    return "Other"

def _primary_stack_from_skills(skills: list[str]):
    clean_skills = [skill for skill in (skills or []) if skill]
    if not clean_skills:
        return "Unclassified"
    family_counts = {}
    for skill in clean_skills:
        family = _skill_family(skill)
        family_counts[family] = family_counts.get(family, 0) + 1
    if family_counts.get("Frontend", 0) > 0 and family_counts.get("Backend", 0) > 0:
        return "Full Stack"
    return sorted(family_counts.items(), key=lambda item: item[1], reverse=True)[0][0]

def _search_rank(search_terms: list[str], skills: list[str], name: str = "", email: str = ""):
    terms = [term.lower().strip("% ") for term in (search_terms or []) if term and term.strip("% ")]
    skill_text = " ".join(skills or []).lower()
    identity_text = f"{name or ''} {email or ''}".lower()
    score = 0
    for term in terms:
        if term in skill_text:
            score += 10
        if term in identity_text:
            score += 6
    return score

def profileDiscovery(domain: str = 'dev', limit: int = 500):
    conn = client.getConnection()
    cur = conn.cursor()

    domain_filter = "" if domain == "all" else "WHERE person.domain = %s"
    params = () if domain == "all" else (domain,)
    query = f"""
        SELECT
            person.id,
            person.firstname,
            person.lastname,
            prof.email,
            prof.title,
            COALESCE(sk.skills, ARRAY[]::text[]) AS skills,
            COALESCE(pa.steps, ARRAY[]::integer[]) AS steps,
            COALESCE(per.personalities, '[]'::json) AS personalities
        FROM person
        JOIN professional prof ON person.id = prof.personid
        LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid
        LEFT JOIN (
            SELECT profileid, ARRAY_AGG(DISTINCT skill.title) AS skills
            FROM (
                SELECT profileid, skillid FROM professionalskill
                UNION
                SELECT profileid, skillid FROM resumeskill
                UNION
                SELECT profileid, skillid FROM techskill
            ) allskills
            JOIN skill ON allskills.skillid = skill.id
            GROUP BY profileid
        ) sk ON sk.profileid = profper.id
        LEFT JOIN (
            SELECT profileid, ARRAY_AGG(DISTINCT step) AS steps
            FROM platformactivity
            GROUP BY profileid
        ) pa ON pa.profileid = profper.id
        LEFT JOIN (
            SELECT
                profper_inner.id AS profileid,
                json_agg(
                    json_build_object(
                        'title', p.title,
                        'id', p.id,
                        'score', ROUND((personality_scores.avg_answer / 5) * 100)
                    )
                    ORDER BY personality_scores.avg_answer DESC
                ) AS personalities
            FROM professionalprofile profper_inner
            JOIN (
                SELECT
                    ps.profileid,
                    p.id AS personality_id,
                    p.title,
                    AVG(psq.answer) AS avg_answer
                FROM professionalsurvey ps
                JOIN professionalsurveyquestion psq ON psq.professionalsurveyid = ps.id
                JOIN surveyquestion ON psq.surveyquestionid = surveyquestion.id
                JOIN question ON surveyquestion.questionid = question.id
                JOIN personality p ON p.id = question.personalityid
                GROUP BY ps.profileid, p.id, p.title
            ) personality_scores ON personality_scores.profileid = profper_inner.id
            JOIN personality p ON p.id = personality_scores.personality_id
            GROUP BY profper_inner.id
        ) per ON per.profileid = profper.id
        {domain_filter}
        ORDER BY person.id DESC
        LIMIT %s
    """
    cur.execute(query, params + (limit,))
    results = cur.fetchall()
    conn.close()

    profiles = []
    skill_groups = {}
    personality_groups = {}

    for row in results:
        skills = [skill for skill in (row[5] or []) if skill]
        family_counts = {}
        for skill in skills:
            family = _skill_family(skill)
            family_counts[family] = family_counts.get(family, 0) + 1
        main_family = "Unclassified"
        if family_counts:
            main_family = sorted(family_counts.items(), key=lambda item: item[1], reverse=True)[0][0]
        if family_counts.get("Frontend", 0) > 0 and family_counts.get("Backend", 0) > 0:
            main_family = "Full Stack"

        personalities = row[7] or []
        dominant_personality = "No personality data"
        if personalities:
            dominant_personality = personalities[0].get("title") or dominant_personality

        profile = {
            "id": row[0],
            "name": f"{row[1] or ''} {row[2] or ''}".strip() or "Unnamed profile",
            "email": row[3] or "",
            "title": row[4] or "",
            "skills": skills[:16],
            "skillFamily": main_family,
            "skillFamilyCounts": family_counts,
            "personality": personalities,
            "dominantPersonality": dominant_personality,
            "status": processing.stepProcessingOverall(row[6]),
        }
        profiles.append(profile)
        skill_groups.setdefault(main_family, []).append(profile)
        personality_groups.setdefault(dominant_personality, []).append(profile)

    return {
        "total": len(profiles),
        "profiles": profiles,
        "skillGroups": [
            {"name": name, "count": len(rows), "profiles": rows}
            for name, rows in sorted(skill_groups.items(), key=lambda item: len(item[1]), reverse=True)
        ],
        "personalityGroups": [
            {"name": name, "count": len(rows), "profiles": rows}
            for name, rows in sorted(personality_groups.items(), key=lambda item: len(item[1]), reverse=True)
        ],
    }

def getProfessionalProfileId(personId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT profper.id FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid WHERE person.id = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def getEmail(personId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT email FROM professional WHERE personid = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def getProfilePublicUrl(profileId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT url FROM professional WHERE personid = {profileId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def getSurveyId(personId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT profsur.id FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalsurvey profsur ON profper.id = profsur.profileid WHERE person.id = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def searchCandidatesByNameEmail(query: str, limit: int = 5, domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()
    search_terms = [item.strip() for item in query.replace(";", ",").split(",") if item.strip()]

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    sql = ''
    if domain == 'all':
        sql = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT skill.title) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE (person.firstname || ' ' || person.lastname) ILIKE '%{query}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{query}%' OR prof.email ILIKE '%{query}%' GROUP BY person.id, prof.email ORDER BY id DESC LIMIT {limit};"
    else:
        sql = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT skill.title) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.domain = '{domain}' AND ((person.firstname || ' ' || person.lastname) ILIKE '%{query}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{query}%' OR prof.email ILIKE '%{query}%') GROUP BY person.id, prof.email ORDER BY id DESC LIMIT {limit};"
    
    cur.execute(sql)
    results = cur.fetchall()

    conn.close()

    resultsProcessed = []

    for r in results:
        skills = [skill for skill in (r[5] or []) if skill]
        name = f"{r[1]} {r[2]}"
        resultsProcessed.append({
            "id":r[0],
            "firstName":r[1],
            "lastName":r[2],
            "email":r[3],
            "step": processing.stepProcessingOverall(r[4]),
            "skillMatches": skills,
            "primaryStack": _primary_stack_from_skills(skills),
            "searchRank": _search_rank(search_terms, skills, name, r[3]),
            "aiCertification": {"status": "Not started", "level": None}
        })
    
    resultsProcessed.sort(key=lambda row: (row.get("searchRank", 0), len(row.get("skillMatches", []))), reverse=True)
    return resultsProcessed

def searchCandidatesBySkills(query: str, limit: int = 5, domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()

    search_terms = [item.strip() for item in query.split(',') if item.strip()]
    queryArray = [f"%{item}%" for item in search_terms]

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = ''
    if domain == 'all':
        query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN (SELECT profileid, skillid FROM professionalskill UNION SELECT profileid, skillid FROM resumeskill) allskills ON allskills.profileid = profper.id JOIN skill ON allskills.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) GROUP BY person.id, prof.email ORDER BY skillMatches DESC LIMIT {limit};"
    else:
        query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN (SELECT profileid, skillid FROM professionalskill UNION SELECT profileid, skillid FROM resumeskill) allskills ON allskills.profileid = profper.id JOIN skill ON allskills.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.domain = '{domain}' AND skill.title ILIKE ANY(%s) GROUP BY person.id, prof.email ORDER BY skillMatches DESC LIMIT {limit};"
    
    cur.execute(query, (queryArray,))
    results = cur.fetchall()

    conn.close()

    resultsProcessed = []

    for r in results:
        skills = [skill for skill in (r[5] or []) if skill]
        name = f"{r[1]} {r[2]}"
        resultsProcessed.append({
            "id":r[0],
            "firstName":r[1],
            "lastName":r[2],
            "email":r[3],
            "skillCount":r[4],
            "skillMatches":skills,
            "step": processing.stepProcessingOverall(r[6]),
            "primaryStack": _primary_stack_from_skills(skills),
            "searchRank": _search_rank(search_terms, skills, name, r[3]),
            "aiCertification": {"status": "Not started", "level": None}
        })
    
    resultsProcessed.sort(key=lambda row: (row.get("searchRank", 0), row.get("skillCount", 0)), reverse=True)
    return resultsProcessed

def searchCandidatesBySkillId(queryList: list[int], limit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN (SELECT profileid, skillid FROM professionalskill UNION SELECT profileid, skillid FROM resumeskill) allskills ON allskills.profileid = profper.id JOIN skill ON allskills.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.id = ANY(%s::int[]) GROUP BY person.id, prof.email, person.firstname, person.lastname ORDER BY skillMatches DESC LIMIT {limit};"
    
    cur.execute(query, (queryList,))
    results = cur.fetchall()

    resultsProcessed = []

    for r in results:
        query = f"SELECT p.title, p.id, AVG(psq.answer) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalsurvey ps ON ps.profileid = profper.id JOIN professionalsurveyquestion psq ON psq.professionalsurveyid = ps.id JOIN surveyquestion ON psq.surveyquestionid = surveyquestion.id JOIN question ON surveyquestion.questionid = question.id JOIN personality p ON p.id = question.personalityid WHERE person.id = {r[0]} GROUP BY p.title, p.id"
        cur.execute(query)

        personalityResult = cur.fetchall()

        personalityArray = []

        for row in personalityResult:
            personalityArray.append({'title':row[0], 'id':row[1], 'score': row[2]})

        resultsProcessed.append({
            "id":r[0],
            "firstName":r[1],
            "lastName":r[2],
            "email":r[3],
            "skillCount":r[4],
            "skillMatches":r[5],
            "step": processing.stepProcessingOverall(r[6]),
            "personality": personalityArray
        })

    conn.close()
    
    return resultsProcessed

def searchCandidatesBySkillsNamesPaginated(nameQuery: str, skillQuery: str, pageLimit: int = 5, currentPage: int = 0, domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()

    queryArray = [item.strip() for item in skillQuery.split(',')]

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    wildcard = f'%{nameQuery}%'

    query = ''
    if domain == 'all':
        query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) AND ((person.firstname || ' ' || person.lastname) ILIKE %s OR (person.goesbyname || ' ' || person.lastname) ILIKE %s OR prof.email ILIKE %s) GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY skillMatches DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"
    else:
        query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.domain = '{domain}' AND skill.title ILIKE ANY(%s) AND ((person.firstname || ' ' || person.lastname) ILIKE %s OR (person.goesbyname || ' ' || person.lastname) ILIKE %s OR prof.email ILIKE %s) GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY skillMatches DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"

    cur.execute(query, (queryArray, wildcard, wildcard, wildcard))
    results = cur.fetchall()

    conn.close()

    resultsProcessed = []

    for r in results:
        resultsProcessed.append({
            "id":r[0],
            "full_name":f'{r[1]} {r[2]}',
            "email":r[3],
            "skillMatches":r[5],
            "step": processing.stepProcessingOverall(r[6]),
            "location": f'{r[7]}, {r[8]}, {r[9]}'
        })
    
    return resultsProcessed

def searchCandidatesByNameEmailPaginated(queryName: str, pageLimit: int = 5, currentPage: int = 0, domain: str = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = ''
    if domain == 'all':
        query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT skill.title), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE (person.firstname || ' ' || person.lastname) ILIKE '%{queryName}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{queryName}%' OR prof.email ILIKE '%{queryName}%' GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY id DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"
    else:
        query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT skill.title), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.domain = '{domain}' AND ((person.firstname || ' ' || person.lastname) ILIKE '%{queryName}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{queryName}%' OR prof.email ILIKE '%{queryName}%') GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY id DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"

    cur.execute(query)
    results = cur.fetchall()

    conn.close()

    resultsProcessed = []

    for r in results:
        resultsProcessed.append({
            "id":r[0],
            "full_name":f'{r[1]} {r[2]}',
            "email":r[3],
            "step": processing.stepProcessingOverall(r[4]),
            "skillMatches": r[5],
            "location": f'{r[6]}, {r[7]}, {r[8]}'
        })
    
    return resultsProcessed

def searchPageCount(nameQuery: str, skillQuery: str = None, pageLimit: int = 5, domain = 'all'):
    conn = client.getConnection()
    cur = conn.cursor()

    query = ''

    if skillQuery:
        queryArray = [item.strip() for item in skillQuery.split(',')]
        wildcard = f'%{nameQuery}%'

        if domain == 'all':
            query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) AND ((person.firstname || ' ' || person.lastname) ILIKE %s OR (person.goesbyname || ' ' || person.lastname) ILIKE %s OR prof.email ILIKE %s);"
        else:
            query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.domain = '{domain}' AND skill.title ILIKE ANY(%s) AND ((person.firstname || ' ' || person.lastname) ILIKE %s OR (person.goesbyname || ' ' || person.lastname) ILIKE %s OR prof.email ILIKE %s);"
        cur.execute(query, (queryArray, wildcard, wildcard, wildcard))
        results = cur.fetchall()

        rowCount = results[0][0] if results else 0
        pages = (rowCount // pageLimit) + (1 if rowCount and rowCount % pageLimit > 0 else 0)

        conn.close()

        return [rowCount, pages]
    else:
        if domain == 'all':
            query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.personid WHERE (person.firstname || ' ' || person.lastname) ILIKE '%{nameQuery}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{nameQuery}%' OR prof.email ILIKE '%{nameQuery}%';"
        else:
            query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.personid WHERE person.domain = '{domain}' AND ((person.firstname || ' ' || person.lastname) ILIKE '%{nameQuery}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{nameQuery}%' OR prof.email ILIKE '%{nameQuery}%');"
        cur.execute(query)
        results = cur.fetchall()

        rowCount = results[0][0] if results else 0
        pages = (rowCount // pageLimit) + (1 if rowCount and rowCount % pageLimit > 0 else 0)

        conn.close()

        return [rowCount, pages]
    
def getProfile(profileId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT person.firstname, person.middlename, person.lastname, person.goesbyname, person.urlimage, person.citizenship, person.birthday, person.leadsource, prof.status, prof.title, prof.maindescription, prof.url, prof.linkedinurl, prof.email, prof.hubspotcontactid, prof.hubspotdeveloperid, prof.referredby, address.city, address.state, address.country, address.timezone, address.longitude, address.latitude FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid WHERE person.id = {profileId} LIMIT 1;"

    cur.execute(query)
    results = cur.fetchone()

    leadSourceProcessed = processing.leadSourceProcessing(results[7])

    # Get Platform Activity
    query = f"SELECT ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT platact.notes) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.id = {profileId} GROUP BY person.id"
    cur.execute(query)

    platactResult = cur.fetchone()

    platactProcessed = {'step':processing.stepProcessingOverall(platactResult[0]), 'attachedNotes':platactResult[1]}

    # Get Personality Data
    query = f"SELECT p.title, p.id, AVG(psq.answer) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalsurvey ps ON ps.profileid = profper.id JOIN professionalsurveyquestion psq ON psq.professionalsurveyid = ps.id JOIN surveyquestion ON psq.surveyquestionid = surveyquestion.id JOIN question ON surveyquestion.questionid = question.id JOIN personality p ON p.id = question.personalityid WHERE person.id = {profileId} GROUP BY p.title, p.id"
    cur.execute(query)

    personalityResult = cur.fetchall()

    personalityArray = []

    for row in personalityResult:
        personalityArray.append({'title':row[0], 'id':row[1], 'score': round((row[2]/5)*100)})

    # Get Professional Skills Data
    query = f"SELECT DISTINCT profskill.years, skill.title, skill.id, skill.description, skill.type FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id WHERE person.id = {profileId}"
    cur.execute(query)

    skillResult = cur.fetchall()

    skillArray = []

    for row in skillResult:
        skillArray.append({'years':row[0], 'skill':row[1], 'skillId': row[2], 'description': row[3], 'type': row[4]})

    # Get Technical Skills Data
    query = f"SELECT DISTINCT ts.level, skill.title, skill.id, skill.description, skill.type FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN techskill ts ON profper.id = ts.profileid JOIN skill ON ts.skillid = skill.id WHERE person.id = {profileId}"
    cur.execute(query)

    techSkillResult = cur.fetchall()

    techSkillArray = []

    for row in techSkillResult:
        techSkillArray.append({'level':row[0], 'skill':row[1], 'skillId': row[2], 'description': row[3], 'type': row[4]})

    # Get Portfolio Experience Data
    query = f"SELECT pe.description, pe.mainrole, pe.workexperience, pe.companyname, pe.startdate, pe.finishdate, pe.ispresent, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT pf.title), ARRAY_AGG(DISTINCT skill.id) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalexperience pe ON profper.id = pe.profileid LEFT JOIN portfolioskill por ON pe.id = por.professionalexperienceid LEFT JOIN skill ON por.skillid = skill.id LEFT JOIN portfoliofeature pf ON pe.id = pf.professionalexperienceid WHERE person.id = {profileId} GROUP BY pe.description, pe.mainrole, pe.workexperience, pe.companyname, pe.startdate, pe.finishdate, pe.ispresent ORDER BY pe.startdate DESC"
    cur.execute(query)

    portfolioSkillResult = cur.fetchall()

    portfolioSkillArray = []

    for row in portfolioSkillResult:
        portfolioSkillInnerArray = []

        if len(row[7]) > 0:
            for i in range(len(row[7])):
                if row[7][i] is not None:
                    portfolioSkillInnerArray.append({'skill': row[7][i], 'skillId': row[9][i]})

        portfolioSkillArray.append({'description':row[0], 'mainrole':row[1], 'workexperience': row[2], 'companyname': row[3], 'startdate': row[4], 'finishdate': row[5], 'ispresent': row[6], 'skills': portfolioSkillInnerArray, 'features': row[8]})

    # Get Professional Feature Data
    query = f"SELECT pf.title, pf.level FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalfeature pf ON profper.id = pf.profileid WHERE person.id = {profileId}"
    cur.execute(query)

    featureResult = cur.fetchall()

    featureArray = []

    for row in featureResult:
        featureArray.append({'title': row[0], 'level': row[1]})

    # Get Cultural Feature Data
    query = f"SELECT pce.title, pce.level FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalculturalexperience pce ON profper.id = pce.profileid WHERE person.id = {profileId}"
    cur.execute(query)

    culturalExperienceResult = cur.fetchall()

    culturalExperienceArray = []

    for row in culturalExperienceResult:
        culturalExperienceArray.append({'title': row[0], 'level': row[1]})

    conn.close()

    return {
        'profile':{
            'firstName': results[0],
            'middleName': results[1],
            'lastName': results[2],
            'goesByName': results[3],
            'imageUrl': results[4],
            'citizenship':results[5],
            'birthdate': results[6],
            'leadsource':leadSourceProcessed,
            'status':processing.statusProcessing(results[8]),
            'title': results[9],
            'description': results[10],
            'publicUrl': results[11],
            'linkedinUrl': results[12],
            'email': results[13],
            'hubspotcontactid': results[14],
            'hubspotdeveloperid': results[15],
            'referredby': results[16],
            'city': results[17],
            'state': results[18],
            'country': results[19],
            'timezone': results[20],
            'longitude': results[21],
            'latitude': results[22],
        },
        'personality':personalityArray,
        'platformActivity':platactProcessed,
        'skills':skillArray,
        'technicalSkills':techSkillArray,
        'portfolioExperience': portfolioSkillArray,
        'features': featureArray,
        'culturalExperience': culturalExperienceArray
    }

def getProfilePublic(profileUrl: str):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT person.id FROM person JOIN professional prof ON person.id = prof.personid WHERE prof.url = '{profileUrl}' LIMIT 1;"
    cur.execute(query)
    result = cur.fetchone()

    if result:
        return getProfile(result[0])
    else:
        raise Exception("Profile not found")
    
def getProfileShort(profileId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT person.firstname, person.lastname, ARRAY_AGG(DISTINCT platact.step), prof.email FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.id = {profileId} GROUP BY person.firstname, person.lastname, prof.email LIMIT 1;"

    cur.execute(query)
    results = cur.fetchone()

    conn.close()

    return {
        'firstName': results[0],
        'lastName': results[1],
        'status':processing.stepProcessingOverall(results[2]),
        'email': results[3]
    }

def getProfileShortScore(jobId: str, profileIds: list[str]):
    jd = getJob(jobId)

    if not jd:
        raise HTTPException(status_code=400, detail="No job description loaded yet. Normalize a JD first.")
    
    jobSkills = []
    jobSkillIds = []

    if not jd["skills"]:
        raise "No Job Skills Found"
    else:
        jobSkills = list(set(jd["skills"])) # Ensure unique skills
        jobSkillIds = list(set(jd["skillIds"]))

    conn = client.getConnection()
    cur = conn.cursor()

    resultSet = []

    for profile in profileIds:
        query = f"SELECT person.firstname, person.lastname, ARRAY_AGG(DISTINCT platact.step), prof.email FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.id = {profile} GROUP BY person.firstname, person.lastname, prof.email LIMIT 1;"

        cur.execute(query)
        results = cur.fetchone()

        # JOIN (SELECT profileid, skillid FROM professionalskill UNION SELECT profileid, skillid FROM resumeskill) allskills ON allskills.profileid = profper.id JOIN skill ON allskills.skillid = skill.id
        query = f"SELECT DISTINCT skill.title, skill.id FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN (SELECT profileid, skillid FROM professionalskill UNION SELECT profileid, skillid FROM resumeskill) allskills ON allskills.profileid = profper.id JOIN skill ON allskills.skillid = skill.id WHERE person.id = {profile}"
        cur.execute(query)

        skillResult = cur.fetchall()

        skillArray = []

        for row in skillResult:
            if row[1] in jobSkillIds:
                skillArray.append(row[0])

        score = round(len(list(set(skillArray))) / len(jobSkills) * 100)

        resultSet.append({
            'id': profile,
            'firstName': results[0],
            'lastName': results[1],
            'status':processing.stepProcessingOverall(results[2]),
            'skills':skillArray,
            'score': score,
            'email': results[3]
        })

    conn.close()

    resultSet.sort(key=lambda x: x["score"], reverse=True)
    # Sort by skill match
    return resultSet
    
def uploadProfile(skills: list, fullName: str, candidateDescription: str, domain: str, email: str = None, linkedInUrl: str = None, culturalExperiences: list = [], candidateCity: str = None, candidateState: str = None, candidateCountry: str = None, candidateTitle: str = None):
    print(f"Uploading profile for {fullName} with email {email} and LinkedIn URL {linkedInUrl}. Skills: {skills}")
    conn = client.getConnection()
    cur = conn.cursor()

    splitName = fullName.split(" ")
    firstName = splitName[0]
    lastName = splitName[-1] if len(splitName) > 1 else ""

    query = "INSERT INTO person (firstname, lastname, leadsource, domain) VALUES (%s, %s, %s, %s) RETURNING id"    
    cur.execute(query, (firstName, lastName, 1, domain))
    print(cur.statusmessage)

    personId = cur.fetchone()[0]
    url = f"{firstName.lower()}-{lastName.lower()}-{personId}"

    print(f"Person ID: {personId}")

    query = "INSERT INTO address (personid, city, state, country) VALUES (%s, %s, %s, %s)"
    cur.execute(query, (personId, candidateCity, candidateState, candidateCountry))

    if len(linkedInUrl) < 1:
        linkedInUrl = "N/A"

    professionalId = ""

    cur.execute(
        "INSERT INTO professional (personid, email, linkedinurl, maindescription, status, url, title) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (personId, email, linkedInUrl, candidateDescription, 1, url, candidateTitle)
    )
    rawRow = cur.fetchone()
    professionalId = rawRow[0]
    print(f"Professional ID: {professionalId}")

    query = "INSERT INTO professionalprofile (professionalid) VALUES (%s) RETURNING id"    
    cur.execute(query, (professionalId,))

    professionalprofileId = cur.fetchone()[0]

    query = "INSERT INTO platformactivity (profileid, step, result, date) VALUES (%s, 1, 1, NOW()) RETURNING id"
    cur.execute(query, (professionalprofileId,))

    for skill in skills:
        # Check if skill already exists
        query = f"SELECT id FROM skill WHERE title ILIKE '%{skill['title'].strip()}%' LIMIT 1"
        cur.execute(query)
        skillId = cur.fetchone()[0] if cur.rowcount > 0 else None

        if not skillId:
            continue

        # Associate skill with professional profile
        query = "INSERT INTO resumeskill (profileid, skillid) VALUES (%s, %s)"
        cur.execute(query, (professionalprofileId, skillId))

        query = "INSERT INTO professionalskill (profileid, skillid, years) VALUES (%s, %s, %s)"
        cur.execute(query, (professionalprofileId, skillId, skill['years']))

    for experience in culturalExperiences:
        query = "INSERT INTO professionalculturalexperience (profileid, title, level) VALUES (%s, %s, %s)"
        cur.execute(query, (professionalprofileId, experience["experience"], experience["level"]))
    
    conn.commit()
    conn.close()
    print(f"Profile for {fullName} uploaded successfully with ID {personId}.")
    return {"status": "success", "message": f"Profile for {fullName} uploaded successfully.", "personid": personId, "name": fullName}

def deleteTemporaryExternalProfile(personId: str):
    try:
        personIdInt = int(personId)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid profile id.")

    conn = client.getConnection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id, maindescription FROM professional WHERE personid = %s",
            (personIdInt,),
        )
        professional_rows = cur.fetchall()
        if not professional_rows:
            raise HTTPException(status_code=404, detail="Temporary profile not found.")

        descriptions = " ".join([str(row[1] or "") for row in professional_rows])
        if "Temporary external profile" not in descriptions:
            raise HTTPException(
                status_code=403,
                detail="Delete is limited to temporary external profiles.",
            )

        professional_ids = [row[0] for row in professional_rows]

        cur.execute(
            "SELECT id FROM professionalprofile WHERE professionalid = ANY(%s)",
            (professional_ids,),
        )
        profile_ids = [row[0] for row in cur.fetchall()]

        survey_ids = []
        experience_ids = []
        if profile_ids:
            cur.execute(
                "SELECT id FROM professionalsurvey WHERE profileid = ANY(%s)",
                (profile_ids,),
            )
            survey_ids = [row[0] for row in cur.fetchall()]

            cur.execute(
                "SELECT id FROM professionalexperience WHERE profileid = ANY(%s)",
                (profile_ids,),
            )
            experience_ids = [row[0] for row in cur.fetchall()]

        if survey_ids:
            cur.execute(
                "DELETE FROM professionalsurveyquestion WHERE professionalsurveyid = ANY(%s)",
                (survey_ids,),
            )
            cur.execute(
                "DELETE FROM professionalsurvey WHERE id = ANY(%s)",
                (survey_ids,),
            )

        cur.execute("DELETE FROM aichatlogs WHERE personid = %s", (personIdInt,))

        if experience_ids:
            cur.execute(
                "DELETE FROM portfolioskill WHERE professionalexperienceid = ANY(%s)",
                (experience_ids,),
            )
            cur.execute(
                "DELETE FROM portfoliofeature WHERE professionalexperienceid = ANY(%s)",
                (experience_ids,),
            )
            cur.execute(
                "DELETE FROM professionalexperience WHERE id = ANY(%s)",
                (experience_ids,),
            )

        if profile_ids:
            for table in [
                "professionalculturalexperience",
                "professionalfeature",
                "professionalskill",
                "resumeskill",
                "techskill",
                "platformactivity",
            ]:
                cur.execute(
                    f"DELETE FROM {table} WHERE profileid = ANY(%s)",
                    (profile_ids,),
                )
            cur.execute(
                "DELETE FROM professionalprofile WHERE id = ANY(%s)",
                (profile_ids,),
            )

        cur.execute("DELETE FROM address WHERE personid = %s", (personIdInt,))
        cur.execute("DELETE FROM professional WHERE personid = %s", (personIdInt,))
        cur.execute("DELETE FROM person WHERE id = %s", (personIdInt,))

        conn.commit()
        return {"status": "success", "deletedProfileId": personIdInt}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        print(f"Failed to delete temporary external profile {personId}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete temporary external profile: {e}",
        )
    finally:
        conn.close()

def updateCandidateCore(personId: str, firstName: str, lastName: str, city: str = "", state: str = "", country: str = "", description: str = "", jobTitle: str = ""):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"UPDATE person SET firstname = %s, lastname = %s WHERE id = {personId}"
    cur.execute(query, (firstName, lastName))

    query = f"UPDATE address SET city = %s, state = %s, country = %s WHERE personid = {personId}"
    cur.execute(query, (city, state, country))

    query = f"UPDATE professional SET maindescription = %s, title = %s WHERE personid = {personId}"
    cur.execute(query, (description, jobTitle))

    conn.commit()
    conn.close()

    return {"status": "success"}

def updateCandidateSkills(personId: str, skills: list):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT profper.id FROM professionalprofile profper JOIN professional prof ON profper.professionalid = prof.id WHERE prof.personid = {personId}"
    cur.execute(query)
    profileId = cur.fetchone()[0]

    # Delete existing skills
    query = f"DELETE FROM professionalskill WHERE profileid = {profileId}"
    cur.execute(query)

    for skill in skills:
        # Associate skill with professional profile
        query = "INSERT INTO professionalskill (profileid, skillid, years) VALUES (%s, %s, %s)"
        cur.execute(query, (profileId, skill["skill"], skill['years']))

    conn.commit()
    conn.close()

    return {"status": "success"}

def updateCandidateFeatures(personId: str, features: list, cultural: list):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT profper.id FROM professionalprofile profper JOIN professional prof ON profper.professionalid = prof.id WHERE prof.personid = {personId}"
    cur.execute(query)
    profileId = cur.fetchone()[0]

    # Delete existing features
    query = f"DELETE FROM professionalfeature WHERE profileid = {profileId}"
    cur.execute(query)

    for feature in features:
        feature["level"] = int(feature["level"])

        if feature["level"] <1:
            feature["level"] = 1
        elif feature["level"] >3:
            feature["level"] = 3
        # Associate feature with professional profile
        query = "INSERT INTO professionalfeature (profileid, title, level) VALUES (%s, %s, %s)"
        cur.execute(query, (profileId, feature["title"], feature['level']))

    # Delete existing cultural experiences
    query = f"DELETE FROM professionalculturalexperience WHERE profileid = {profileId}"
    cur.execute(query)

    for feature in cultural:
        feature["level"] = int(feature["level"])

        if feature["level"] <1:
            feature["level"] = 1
        elif feature["level"] >3:
            feature["level"] = 3
        # Associate cultural experience with professional profile
        query = "INSERT INTO professionalculturalexperience (profileid, title, level) VALUES (%s, %s, %s)"
        cur.execute(query, (profileId, feature["title"], feature['level']))

    conn.commit()
    conn.close()

    return {"status": "success"}

class PortfolioExperience(BaseModel):
    description: str
    mainRole: str
    companyName: str
    startDate: int | str
    finishDate: int | str | None
    isPresent: bool
    skills: list[str]
    features: list[str]
class profilePortfolioUpdateRequest(BaseModel):
    personId: str
    portfolio: list[PortfolioExperience]

def updateCandidatePortfolio(personId: str, portfolio: list[PortfolioExperience]):
    conn = client.getConnection()
    cur = conn.cursor()

    print(personId)

    query = f"SELECT profper.id FROM professionalprofile profper JOIN professional prof ON profper.professionalid = prof.id WHERE prof.personid = {personId}"
    cur.execute(query)
    profileId = cur.fetchone()[0]

    print(profileId)

    # Delete existing portfolio experience
    query = f"DELETE FROM professionalexperience WHERE profileid = {profileId}"
    cur.execute(query)

    for experience in portfolio:
        print(experience)

        if experience.finishDate is not None and experience.finishDate != "":
            experience.finishDate = int(experience.finishDate)
            query = "INSERT INTO professionalexperience (profileid, description, mainrole, companyname, startdate, finishdate, ispresent) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id"
            cur.execute(query, (profileId, experience.description, experience.mainRole, experience.companyName, experience.startDate, experience.finishDate, experience.isPresent))
        else:
            query = "INSERT INTO professionalexperience (profileid, description, mainrole, companyname, startdate, ispresent) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id"
            cur.execute(query, (profileId, experience.description, experience.mainRole, experience.companyName, experience.startDate, experience.isPresent))
        experienceId = cur.fetchone()[0]

        print(experienceId)
        print(cur.statusmessage)

        for skill in experience.skills:
            query = "INSERT INTO portfolioskill (professionalexperienceid, skillid) VALUES (%s, %s)"
            cur.execute(query, (experienceId, skill))

        for feature in experience.features:
            query = "INSERT INTO portfoliofeature (professionalexperienceid, title) VALUES (%s, %s)"
            cur.execute(query, (experienceId, feature))

    conn.commit()
    conn.close()

    return {"status": "success"}
