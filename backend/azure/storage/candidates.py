import azure.storage.client as client
import azure.storage.processingFunctions as processing

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

def searchCandidatesByNameEmail(query: str, limit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.id JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.firstname ILIKE '%{query}%' OR person.lastname ILIKE '%{query}%' OR person.goesbyname ILIKE '%{query}%' OR prof.email ILIKE '%{query}%' GROUP BY person.id, prof.email ORDER BY id DESC LIMIT {limit};"
    
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
    print(queryArray)

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.id JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) GROUP BY person.id, prof.email ORDER BY skillMatches DESC LIMIT {limit};"
    
    cur.execute(query, (queryArray,))
    results = cur.fetchall()
    print(f'Search results for "{query}": {results}')

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