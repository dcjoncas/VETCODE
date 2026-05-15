import azureUtils.storage.client as client
import openAI.jobProcessing as aiProcessing

def _sync_identity_sequence(cur, table: str, column: str = "id"):
    allowed_tables = {"jobdescription", "jobskills", "jobpersonalities", "skill"}
    if table not in allowed_tables:
        return

    cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, column))
    row = cur.fetchone()
    sequence_name = row[0] if row else None
    if not sequence_name:
        return

    cur.execute(f"SELECT COALESCE(MAX({column}), 0) FROM {table}")
    max_id = cur.fetchone()[0] or 0
    if max_id > 0:
        cur.execute("SELECT setval(%s, %s, true)", (sequence_name, max_id))
    else:
        cur.execute("SELECT setval(%s, 1, false)", (sequence_name,))

def _resolve_skill_id(cur, skill_title: str):
    clean_title = (skill_title or "").strip()
    if not clean_title:
        return None
    cur.execute(
        """
        SELECT id
        FROM skill
        WHERE LOWER(title) = LOWER(%s)
        ORDER BY id DESC
        LIMIT 1
        """,
        (clean_title,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    _sync_identity_sequence(cur, "skill")
    cur.execute(
        "INSERT INTO skill (title, description, type, active) VALUES (%s, %s, %s, %s) RETURNING id",
        (clean_title, clean_title, 1, True),
    )
    return cur.fetchone()[0]

def uploadJob(company: str, title: str, domain: str, jd_text: str, skills: list[str]):
    conn = client.getConnection()
    cur = conn.cursor()

    try:
        # Add jd to jd table
        _sync_identity_sequence(cur, "jobdescription")
        query = "INSERT INTO jobdescription (domain, company, jobtitle, description) VALUES (%s, %s, %s, %s) RETURNING id"
        
        cur.execute(query, (domain, company, title, jd_text))

        jobId = cur.fetchone()[0]

        print(f"Job ID: {jobId}")

        for skill in skills:
            skill_id = _resolve_skill_id(cur, skill)
            if not skill_id:
                continue

            # TODO: Get AI to determine the number of years required by a company for a skill
            _sync_identity_sequence(cur, "jobskills")
            query = "INSERT INTO jobskills (jobid, skillid) VALUES (%s, %s)"
            cur.execute(query, (jobId, skill_id))

        try:
            aiProcessing.processPersonalities(jobId, jd_text, cur)
        except Exception as personality_error:
            print(f"Job personality processing skipped for {jobId}: {personality_error}")
        conn.commit()
        conn.close()

        return {'jd_id':jobId}

    except Exception as e:
        print(f'Cannot insert job description: {e}')
        conn.close()

def updateJob(jobId: int, company: str, title: str, domain: str, jd_text: str, skills: list[str]):
    conn = client.getConnection()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id FROM jobdescription WHERE id = %s AND domain = %s",
            (jobId, domain),
        )
        if not cur.fetchone():
            conn.close()
            return {"updated": False, "jd_id": jobId}

        cur.execute(
            """
            UPDATE jobdescription
            SET company = %s, jobtitle = %s, description = %s
            WHERE id = %s AND domain = %s
            """,
            (company, title, jd_text, jobId, domain),
        )

        cur.execute("DELETE FROM jobskills WHERE jobid = %s", (jobId,))
        cur.execute("DELETE FROM jobpersonalities WHERE jobid = %s", (jobId,))

        for skill in skills:
            skill_id = _resolve_skill_id(cur, skill)
            if not skill_id:
                continue

            _sync_identity_sequence(cur, "jobskills")
            cur.execute(
                "INSERT INTO jobskills (jobid, skillid) VALUES (%s, %s)",
                (jobId, skill_id),
            )

        try:
            aiProcessing.processPersonalities(jobId, jd_text, cur)
        except Exception as personality_error:
            print(f"Job personality processing skipped for {jobId}: {personality_error}")

        conn.commit()
        conn.close()
        return {"updated": True, "jd_id": jobId, "company": company, "title": title, "domain": domain}
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"Cannot update job description {jobId}: {e}")
        raise
# Test
def getJob(jobId: int, domain: str = None):
    conn = client.getConnection()
    cur = conn.cursor()

    params = [jobId]
    domain_filter = ""
    if domain and domain != "all":
        domain_filter = " AND job.domain = %s"
        params.append(domain)

    query = f"SELECT job.id, job.domain, job.company, job.jobtitle, ARRAY_REMOVE(ARRAY_AGG(DISTINCT skill.title), NULL), ARRAY_REMOVE(ARRAY_AGG(DISTINCT skill.id), NULL), job.description FROM jobdescription job LEFT JOIN jobskills js ON job.id = js.jobid LEFT JOIN skill ON js.skillid = skill.id WHERE job.id = %s{domain_filter} GROUP BY job.id, job.domain, job.company, job.jobtitle, job.description LIMIT 1"
    cur.execute(query, tuple(params))

    result = cur.fetchone()
    if not result:
        conn.close()
        return None

    personality_params = [jobId]
    personality_domain_filter = ""
    if domain and domain != "all":
        personality_domain_filter = " AND job.domain = %s"
        personality_params.append(domain)
    query = f"SELECT p.title, jp.personalityid, jp.score FROM jobdescription job LEFT JOIN jobpersonalities jp ON job.id=jp.jobid JOIN personality p ON jp.personalityid = p.id WHERE job.id = %s{personality_domain_filter}"
    cur.execute(query, tuple(personality_params))

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
        'description':result[6],
        'personalities':personalityArray
    }

def deleteJob(jobId: int, domain: str = None):
    conn = client.getConnection()
    cur = conn.cursor()

    params = [jobId]
    domain_filter = ""
    if domain and domain != "all":
        domain_filter = " AND domain = %s"
        params.append(domain)

    try:
        cur.execute(f"SELECT id, company, jobtitle FROM jobdescription WHERE id = %s{domain_filter}", tuple(params))
        result = cur.fetchone()
        if not result:
            conn.close()
            return {"deleted": False, "job_id": jobId}

        cur.execute("DELETE FROM jobskills WHERE jobid = %s", (jobId,))
        cur.execute("DELETE FROM jobpersonalities WHERE jobid = %s", (jobId,))
        cur.execute("DELETE FROM jobdescription WHERE id = %s", (jobId,))
        conn.commit()
        conn.close()
        return {
            "deleted": True,
            "job_id": result[0],
            "company": result[1],
            "title": result[2],
        }
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"Cannot delete job description {jobId}: {e}")
        raise

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
