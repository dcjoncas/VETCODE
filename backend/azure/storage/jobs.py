import azure.storage.client as client

def uploadJob(company: str, title: str, domain: str, jd_text: str, skills: list[str]):
    print(skills)
    conn = client.getConnection()
    cur = conn.cursor()

    try:
        # Add jd to jd table
        query = "INSERT INTO jobdescription (domain, company, jobtitle, description) VALUES (%s, %s, %s, %s) RETURNING id"
        
        cur.execute(query, (domain, company, title, jd_text))

        jobId = cur.fetchone()[0]

        for skill in skills:
            query = f"SELECT id FROM skill WHERE title ILIKE '%{skill}%' ORDER BY id DESC LIMIT 1"
            cur.execute(query)

            # TODO: Get AI to determine the number of years required by a company for a skill
            query = "INSERT INTO jobskills (jobid, skillid) VALUES (%s, %s)"
            cur.execute(query, (jobId, cur.fetchone()[0]))

        conn.commit()

    except Exception as e:
        print(f'Cannot insert job description: {e}')
    
    conn.close()