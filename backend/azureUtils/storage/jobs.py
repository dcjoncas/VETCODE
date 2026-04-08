import azureUtils.storage.client as client
import openAI.jobProcessing as aiProcessing

def uploadJob(company: str, title: str, domain: str, jd_text: str, skills: list[str]):
    conn = client.getConnection()
    cur = conn.cursor()

    try:
        # Add jd to jd table
        query = "INSERT INTO jobdescription (domain, company, jobtitle, description) VALUES (%s, %s, %s, %s) RETURNING id"
        
        cur.execute(query, (domain, company, title, jd_text))

        jobId = cur.fetchone()[0]

        print(f"Job ID: {jobId}")

        for skill in skills:
            query = f"SELECT id FROM skill WHERE title ILIKE '%{skill}%' ORDER BY id DESC LIMIT 1"
            cur.execute(query)

            # TODO: Get AI to determine the number of years required by a company for a skill
            query = "INSERT INTO jobskills (jobid, skillid) VALUES (%s, %s)"
            cur.execute(query, (jobId, cur.fetchone()[0]))

        aiProcessing.processPersonalities(jobId, jd_text, cur)
        conn.commit()
        conn.close()

        return {'jd_id':jobId}

    except Exception as e:
        print(f'Cannot insert job description: {e}')
        conn.close()

def getJob(jobId: int):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT job.id, job.domain, job.company, job.jobtitle, ARRAY_AGG(DISTINCT skill.title), ARRAY_AGG(DISTINCT skill.id) FROM jobdescription job LEFT JOIN jobskills js ON job.id = js.jobid JOIN skill ON js.skillid = skill.id WHERE job.id = {jobId} GROUP BY job.id, job.domain, job.company, job.jobtitle"
    cur.execute(query)

    result = cur.fetchone()

    query = f"SELECT p.title, jp.personalityid, jp.score FROM jobdescription job LEFT JOIN jobpersonalities jp ON job.id=jp.jobid JOIN personality p ON jp.personalityid = p.id WHERE job.id = {jobId}"
    cur.execute(query)

    personalityResult = cur.fetchall()

    conn.close()

    personalityArray = []

    for row in personalityResult:
        personalityArray.append({'title':row[0], 'id':row[1], 'score': row[2]})

    return {
        'jd_id':result[0],
        'domain':result[1],
        'company':result[2],
        'title':result[3],
        'skills':result[4],
        'skillIds':result[5],
        'personalities':personalityArray
    }

def searchJobs(domain: str, searchQuery: str, limit: int):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT id, domain, company, jobtitle FROM jobdescription WHERE (jobtitle ILIKE '%{searchQuery}%' OR company ILIKE '%{searchQuery}%') AND domain = '{domain}' ORDER BY id DESC LIMIT {limit}"
    cur.execute(query)

    results = cur.fetchall()

    conn.close()

    processedResults = []

    for result in results:
        processedResults.append({
            'jd_id':result[0],
            'domain':result[1],
            'company':result[2],
            'title':result[3],
        })

    return processedResults

def listJobs(domain: str, limit: int):
    conn = client.getConnection()
    cur = conn.cursor()

    query = f"SELECT id, domain, company, jobtitle FROM jobdescription WHERE domain = '{domain}' ORDER BY id DESC LIMIT {limit}"
    cur.execute(query)

    results = cur.fetchall()

    conn.close()

    processedResults = []

    for result in results:
        processedResults.append({
            'jd_id':result[0],
            'domain':result[1],
            'company':result[2],
            'title':result[3],
        })

    return processedResults