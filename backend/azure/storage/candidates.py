import azure.storage.client as client
import azure.storage.processingFunctions as processing
from typing import Optional, List

def countCandidates():
    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = f"SELECT COUNT(id) as count FROM person;"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()
    
    return results[0][0]

def countCandidatesRecent():
    conn = client.getConnection()
    cur = conn.cursor()

    # Count candidates added in the last 7 days
    query = f"SELECT COUNT(person.id) as count FROM person JOIN professional ON person.id = professional.id WHERE professional.modifieddate >= CURRENT_DATE - INTERVAL '7 days';"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()
    
    return results[0][0]

def countCandidatesStatus():
    conn = client.getConnection()
    cur = conn.cursor()

    # Status Guide:
    # 1 = Draft
    # 2 = Pending
    # 3 = Published
    # 4 = Updated
    query = f"SELECT professional.status, COUNT(person.id) as count FROM person JOIN professional ON person.id = professional.id GROUP BY professional.status;"
    
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

def countCandidatesAll():
    totalCount = countCandidates()
    recentCount = countCandidatesRecent()
    statusCounts = countCandidatesStatus()

    return {
        "total": totalCount,
        "recent": recentCount,
        "statusCounts": statusCounts
    }

def getProfessionalProfileId(personId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT profper.id FROM person JOIN professional prof ON person.id = prof.id JOIN professionalprofile profper ON prof.id = profper.professionalid WHERE person.id = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def getSurveyId(personId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT profper.id FROM person JOIN professional prof ON person.id = prof.id JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalsurvey profsur ON profper.id = profsur.profileid WHERE person.id = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def searchCandidatesByNameEmail(query: str, limit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.id LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.firstname ILIKE '%{query}%' OR person.lastname ILIKE '%{query}%' OR person.goesbyname ILIKE '%{query}%' OR prof.email ILIKE '%{query}%' GROUP BY person.id, prof.email ORDER BY id DESC LIMIT {limit};"
    
    cur.execute(query)
    results = cur.fetchall()

    conn.close()

    resultsProcessed = []

    for r in results:
        resultsProcessed.append({
            "id":r[0],
            "firstName":r[1],
            "lastName":r[2],
            "email":r[3],
            "step": processing.stepProcessingOverall(r[4])
        })
    
    return resultsProcessed

def searchCandidatesBySkills(query: str, limit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    queryArray = [item.strip() for item in query.split(',')]

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.id JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) GROUP BY person.id, prof.email ORDER BY skillMatches DESC LIMIT {limit};"
    
    cur.execute(query, (queryArray,))
    results = cur.fetchall()

    conn.close()

    resultsProcessed = []

    for r in results:
        resultsProcessed.append({
            "id":r[0],
            "firstName":r[1],
            "lastName":r[2],
            "email":r[3],
            "skillCount":r[4],
            "skillMatches":r[5],
            "step": processing.stepProcessingOverall(r[6])
        })
    
    return resultsProcessed

def searchCandidatesBySkillsNamesPaginated(nameQuery: str, skillQuery: str, pageLimit: int = 5, currentPage: int = 0):
    conn = client.getConnection()
    cur = conn.cursor()

    queryArray = [item.strip() for item in skillQuery.split(',')]

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    wildcard = f'%{nameQuery}%'
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.id LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) AND (person.firstname ILIKE %s OR person.lastname ILIKE %s OR person.goesbyname ILIKE %s OR prof.email ILIKE %s) GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY skillMatches DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"

    cur.execute(query, (queryArray, wildcard, wildcard, wildcard, wildcard))
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

def searchCandidatesByNameEmailPaginated(query: str, pageLimit: int = 5, currentPage: int = 0):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT skill.title), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.id LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.firstname ILIKE '%{query}%' OR person.lastname ILIKE '%{query}%' OR person.goesbyname ILIKE '%{query}%' OR prof.email ILIKE '%{query}%' GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY id DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"

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

def searchPageCount(nameQuery: str, skillQuery: str = None, pageLimit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    if skillQuery:
        queryArray = [item.strip() for item in skillQuery.split(',')]
        wildcard = f'%{nameQuery}%'
        query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.id JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) AND (person.firstname ILIKE %s OR person.lastname ILIKE %s OR person.goesbyname ILIKE %s OR prof.email ILIKE %s);"
        cur.execute(query, (queryArray, wildcard, wildcard, wildcard, wildcard))
        results = cur.fetchall()

        rowCount = results[0][0] if results else 0
        pages = (rowCount // pageLimit) + (1 if rowCount and rowCount % pageLimit > 0 else 0)

        return [rowCount, pages]
    else:
        query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.id WHERE person.firstname ILIKE '%{nameQuery}%' OR person.lastname ILIKE '%{nameQuery}%' OR person.goesbyname ILIKE '%{nameQuery}%' OR prof.email ILIKE '%{nameQuery}%';"
        cur.execute(query)
        results = cur.fetchall()

        rowCount = results[0][0] if results else 0
        pages = (rowCount // pageLimit) + (1 if rowCount and rowCount % pageLimit > 0 else 0)

        return [rowCount, pages]