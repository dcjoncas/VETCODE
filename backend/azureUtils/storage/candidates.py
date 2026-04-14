from pydantic import BaseModel

import azureUtils.storage.client as client
import azureUtils.storage.processingFunctions as processing

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
    query = f"SELECT profper.id FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid WHERE person.id = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def getSurveyId(personId: str):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT profper.id FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalsurvey profsur ON profper.id = profsur.profileid WHERE person.id = {personId};"
    
    cur.execute(query)
    result = cur.fetchone()

    conn.close()
    
    return result[0]

def searchCandidatesByNameEmail(query: str, limit: int = 5):
    conn = client.getConnection()
    cur = conn.cursor()

    # Search for user by firstname, lastname, goesbyname, or email using ILIKE for case-insensitive search
    # Order by id descending to get the most recent matches first, and limit the number of results
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE (person.firstname || ' ' || person.lastname) ILIKE '%{query}%' OR (person.goesbyname || ' ' || person.lastname) ILIKE '%{query}%' OR prof.email ILIKE '%{query}%' GROUP BY person.id, prof.email ORDER BY id DESC LIMIT {limit};"
    
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
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN (SELECT profileid, skillid FROM professionalskill UNION SELECT profileid, skillid FROM resumeskill) allskills ON allskills.profileid = profper.id JOIN skill ON allskills.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) GROUP BY person.id, prof.email ORDER BY skillMatches DESC LIMIT {limit};"
    
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

def searchCandidatesBySkillsNamesPaginated(nameQuery: str, skillQuery: str, pageLimit: int = 5, currentPage: int = 0):
    conn = client.getConnection()
    cur = conn.cursor()

    queryArray = [item.strip() for item in skillQuery.split(',')]

    # Search for user by skills attached to the account
    # Order by id descending to get the most recent matches first, and limit the number of results
    wildcard = f'%{nameQuery}%'
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, COUNT(DISTINCT skill.title) AS skillMatches, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT platact.step), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) AND ((person.firstname || ' ' || person.lastname) ILIKE %s OR (person.goesbyname || ' ' || person.lastname) ILIKE %s OR prof.email ILIKE %s) GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY skillMatches DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"

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
    query = f"SELECT person.id, person.firstname, person.lastname, prof.email, ARRAY_AGG(DISTINCT platact.step), ARRAY_AGG(DISTINCT skill.title), address.city, address.state, address.country FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid LEFT JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalskill profskill ON profper.id = profskill.profileid LEFT JOIN skill ON profskill.skillid = skill.id LEFT JOIN platformactivity platact ON platact.profileid = profper.id WHERE person.firstname ILIKE '%{query}%' OR person.lastname ILIKE '%{query}%' OR person.goesbyname ILIKE '%{query}%' OR prof.email ILIKE '%{query}%' GROUP BY person.id, prof.email, address.city, address.state, address.country ORDER BY id DESC LIMIT {pageLimit} OFFSET {pageLimit * currentPage};"

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
        query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.personid JOIN professionalprofile profper ON prof.id = profper.professionalid JOIN professionalskill profskill ON profper.id = profskill.profileid JOIN skill ON profskill.skillid = skill.id JOIN platformactivity platact ON platact.profileid = profper.id WHERE skill.title ILIKE ANY(%s) AND (person.firstname ILIKE %s OR person.lastname ILIKE %s OR person.goesbyname ILIKE %s OR prof.email ILIKE %s);"
        cur.execute(query, (queryArray, wildcard, wildcard, wildcard, wildcard))
        results = cur.fetchall()

        rowCount = results[0][0] if results else 0
        pages = (rowCount // pageLimit) + (1 if rowCount and rowCount % pageLimit > 0 else 0)

        conn.close()

        return [rowCount, pages]
    else:
        query = f"SELECT COUNT(DISTINCT person.id) FROM person JOIN professional prof ON person.id = prof.personid WHERE person.firstname ILIKE '%{nameQuery}%' OR person.lastname ILIKE '%{nameQuery}%' OR person.goesbyname ILIKE '%{nameQuery}%' OR prof.email ILIKE '%{nameQuery}%';"
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
    query = f"SELECT pe.description, pe.mainrole, pe.workexperience, pe.companyname, pe.startdate, pe.finishdate, pe.ispresent, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT pf.title), ARRAY_AGG(DISTINCT skill.id) FROM person JOIN professional prof ON person.id = prof.personid LEFT JOIN address ON person.id = address.personid JOIN professionalprofile profper ON prof.id = profper.professionalid LEFT JOIN professionalexperience pe ON profper.id = pe.profileid LEFT JOIN portfolioskill por ON pe.id = por.professionalexperienceid JOIN skill ON por.skillid = skill.id LEFT JOIN portfoliofeature pf ON pe.id = pf.professionalexperienceid WHERE person.id = {profileId} GROUP BY pe.description, pe.mainrole, pe.workexperience, pe.companyname, pe.startdate, pe.finishdate, pe.ispresent ORDER BY pe.startdate DESC"
    cur.execute(query)

    portfolioSkillResult = cur.fetchall()

    portfolioSkillArray = []

    for row in portfolioSkillResult:
        portfolioSkillInnerArray = []

        for i in range(len(portfolioSkillResult)):
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
    
def uploadProfile(skills: list, fullName: str, candidateDescription: str, email: str = None, linkedInUrl: str = None, culturalExperiences: list = [], candidateCity: str = None, candidateState: str = None, candidateCountry: str = None, candidateTitle: str = None):
    print(f"Uploading profile for {fullName} with email {email} and LinkedIn URL {linkedInUrl}. Skills: {skills}")
    conn = client.getConnection()
    cur = conn.cursor()

    splitName = fullName.split(" ")
    firstName = splitName[0]
    lastName = splitName[-1] if len(splitName) > 1 else ""

    query = "INSERT INTO person (firstname, lastname, leadsource) VALUES (%s, %s, %s) RETURNING id"    
    cur.execute(query, (firstName, lastName, 1))
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

    query = f"SELECT profper.id FROM professionalprofile profper JOIN professional prof ON profper.professionalid = prof.id WHERE prof.personid = {personId}"
    cur.execute(query)
    profileId = cur.fetchone()[0]

    # Delete existing portfolio experience
    query = f"DELETE FROM professionalexperience WHERE profileid = {profileId}"
    cur.execute(query)

    for experience in portfolio:
        print(experience)

        if experience.finishDate is not None and experience.finishDate != "":
            experience.finishDate = int(experience.finishDate)
            query = "INSERT INTO professionalexperience (profileid, description, mainrole, companyname, startdate, finishdate, ispresent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
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