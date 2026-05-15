import azureUtils.storage.client as client
import azureUtils.storage.processingFunctions as processing
from azureUtils.storage.candidates import getProfessionalProfileId, getSurveyId
from datetime import datetime, timedelta
import random
import string
import json
import os
from openAI import engineeringSurvey

FALLBACK_CHAT_PATH = os.path.join("data", "profile_completion_chats.json")

def _sync_identity_sequence(cur, table: str, column: str = "id"):
    allowed_tables = {"aichatlogs", "professionalprofile", "professionalsurvey", "professionalsurveyquestion"}
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

def _read_fallback_chats():
    try:
        if os.path.exists(FALLBACK_CHAT_PATH):
            with open(FALLBACK_CHAT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Failed to read fallback chat store: {e}")
    return {}

def _write_fallback_chats(records: dict):
    try:
        os.makedirs(os.path.dirname(FALLBACK_CHAT_PATH), exist_ok=True)
        with open(FALLBACK_CHAT_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
    except Exception as e:
        print(f"Failed to write fallback chat store: {e}")

def isLocalChatUrl(chatUrl: str):
    return str(chatUrl or "").startswith("LOCAL-")

def _fallback_token():
    characters = string.ascii_letters + string.digits
    return "LOCAL-" + ''.join(random.choices(characters, k=16))

def _candidate_name_parts(profileid: str):
    try:
        conn = client.getConnection()
        cur = conn.cursor()
        cur.execute("SELECT firstname, lastname FROM person WHERE id = %s LIMIT 1", (profileid,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0] or "Candidate", row[1] or ""
    except Exception as e:
        print(f"Failed to read candidate name for fallback chat: {e}")
    return "Candidate", ""

def _create_fallback_chat(profileid: str, domain: str = "dev"):
    records = _read_fallback_chats()
    for token, record in records.items():
        if str(record.get("personId", "")) == str(profileid) and record.get("domain", "dev") == (domain or "dev"):
            return token
    token = _fallback_token()
    first_name, last_name = _candidate_name_parts(profileid)
    records[token] = {
        "urlCode": token,
        "personId": str(profileid),
        "firstName": first_name,
        "lastName": last_name,
        "domain": domain or "dev",
        "chatEnd": str((datetime.now() + timedelta(weeks=1)).date()),
        "chatClosed": False,
        "aiTranscript": [],
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }
    _write_fallback_chats(records)
    return token

def _fallback_chat_record(urlcode: str):
    return _read_fallback_chats().get(str(urlcode or ""))

def getPersonId(chatUrl: str):
    if isLocalChatUrl(chatUrl):
        record = _fallback_chat_record(chatUrl)
        return record.get("personId") if record else None
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        query = f"SELECT person.id FROM person JOIN aichatlogs ai ON person.id = ai.personid WHERE ai.urlcode = '{chatUrl}';"
        
        cur.execute(query)
        result = cur.fetchone()

        conn.close()
        return result[0] if result else None
    
    except Exception as e:
        print(f'Failed to grab candidate ID for {chatUrl}: {e}')
        return None

def getChatUrl(personId: str):
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        try:
            query = f"SELECT urlcode FROM aichatlogs WHERE personid = '{personId}' ORDER BY id DESC LIMIT 1;"
            
            cur.execute(query)
            result = cur.fetchone()

            conn.close()
            
            return result[0]
        
        except Exception as e:
            query = f"SELECT id FROM professionalsurvey ps JOIN professionalprofile pp ON ps.profileid = pp.id JOIN professional ON pp.professionalid = professional.id WHERE professional.personid = '{personId}' ORDER BY ps.id DESC LIMIT 1;"
            
            cur.execute(query)
            result = cur.fetchone()

            conn.close()
            
            return 'Candidate has already completed a legacy survey'
    
    except Exception as e:
        print(f'Failed to grab chat URL for {personId}: {e}')
        records = _read_fallback_chats()
        for token, record in records.items():
            if str(record.get("personId", "")) == str(personId):
                return token
        return None
    
    except Exception as e:
        print(f'Failed to grab survey ID for {profId}: {e}')
        return None

def scheduleChat(profileid: str, domain: str = "dev"):
    weekFromNow = (datetime.now() + timedelta(weeks=1)).date()

    # Create URL
    characters = string.ascii_letters + string.digits
    # Use random.choices to select characters and join them into a string
    random_string = ''.join(random.choices(characters, k=10))

    conn = None
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        # Count distinct candidates in the person table
        _sync_identity_sequence(cur, "aichatlogs")
        query = "INSERT INTO aichatlogs (personid, enddate, urlCode) VALUES (%s, %s, %s)"

        cur.execute(query, (profileid, weekFromNow, random_string))

        professional_profile_id = ensureProfessionalProfileId(profileid, cur)
        if not professional_profile_id:
            conn.rollback()
            conn.close()
            return _create_fallback_chat(profileid, domain)

        # TODO: Send email with link to the candidate
        createSurvey(professional_profile_id, random_string, cur)
        conn.commit()
        conn.close()

        return random_string
    except Exception as e:
        print(f"Failed to schedule Azure chat for {profileid}; using fallback chat link: {e}")
        try:
            if conn:
                conn.rollback()
                conn.close()
        except Exception:
            pass
        return _create_fallback_chat(profileid, domain)

def ensureProfessionalProfileId(personId: str, cur = None):
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = client.getConnection()
        cur = conn.cursor()

    cur.execute(
        """
        SELECT profper.id
        FROM professional prof
        JOIN professionalprofile profper ON prof.id = profper.professionalid
        WHERE prof.personid = %s
        ORDER BY profper.id DESC
        LIMIT 1
        """,
        (personId,),
    )
    result = cur.fetchone()
    if result:
        if owns_connection:
            conn.close()
        return result[0]

    cur.execute("SELECT id FROM professional WHERE personid = %s LIMIT 1", (personId,))
    professional = cur.fetchone()
    if not professional:
        if owns_connection:
            conn.close()
        return None

    _sync_identity_sequence(cur, "professionalprofile")
    cur.execute(
        "INSERT INTO professionalprofile (professionalid) VALUES (%s) RETURNING id",
        (professional[0],),
    )
    created = cur.fetchone()
    if owns_connection:
        conn.commit()
        conn.close()
    return created[0] if created else None

def createSurvey(profprofileId: int, token: str, cur = None):
    owns_connection = cur is None
    conn = None
    if owns_connection:
        conn = client.getConnection()
        cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "INSERT INTO professionalsurvey (profileid, token) VALUES (%s, gen_random_uuid())"
    
    _sync_identity_sequence(cur, "professionalsurvey")
    cur.execute(query, (profprofileId,))

    if owns_connection:
        conn.commit()
        conn.close()

def ensureSurveyId(personId: str, token: str = ""):
    existing_id = getSurveyId(personId)
    if existing_id:
        return existing_id

    conn = client.getConnection()
    cur = conn.cursor()
    try:
        professional_profile_id = ensureProfessionalProfileId(personId, cur)
        if not professional_profile_id:
            conn.rollback()
            return None

        _sync_identity_sequence(cur, "professionalsurvey")
        cur.execute(
            "INSERT INTO professionalsurvey (profileid, token) VALUES (%s, gen_random_uuid()) RETURNING id",
            (professional_profile_id,),
        )
        created = cur.fetchone()
        conn.commit()
        return created[0] if created else None
    except Exception as e:
        print(f"Failed to ensure survey for {personId}: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def countQuestions(domain: str = "dev") -> int:
    if engineeringSurvey.is_engineer_domain(domain):
        return len(engineeringSurvey.get_questions())

    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "SELECT COUNT(DISTINCT id) FROM question"

    cur.execute(query)
    result = cur.fetchall()

    conn.close()

    return result[0][0]

def getQuestions(domain: str = "dev"):
    if engineeringSurvey.is_engineer_domain(domain):
        return engineeringSurvey.get_questions()

    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "SELECT description FROM question"

    cur.execute(query)
    result = cur.fetchall()

    processedResults = []
    for row in result:
        processedResults.append(row[0])

    conn.close()

    return processedResults

def getQuestion(questionId: int, domain: str = "dev") -> str:
    if engineeringSurvey.is_engineer_domain(domain):
        return engineeringSurvey.get_question(questionId)

    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = f"SELECT description FROM question WHERE id = {questionId}"

    cur.execute(query)
    result = cur.fetchall()

    conn.close()

    return result[0][0]

def upsertSurveyAnswer(questionId: int, surveyResponse: int, profId: int):
    print(f'profId: {profId}')
    conn = client.getConnection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id
            FROM professionalsurveyquestion
            WHERE professionalsurveyid = %s AND surveyquestionid = %s
            LIMIT 1
            """,
            (profId, questionId),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE professionalsurveyquestion SET answer = %s WHERE id = %s",
                (surveyResponse, existing[0]),
            )
        else:
            _sync_identity_sequence(cur, "professionalsurveyquestion")
            cur.execute(
                "INSERT INTO professionalsurveyquestion (professionalsurveyid, surveyquestionid, answer) VALUES (%s, %s, %s)",
                (profId, questionId, surveyResponse),
            )

    except Exception as e:
        print(f'Cannot insert candidate answers: {e}')

    conn.commit()
    conn.close()

def getChat(urlcode: str, domain: str = "dev"):
    fallback_record = _fallback_chat_record(urlcode)
    if fallback_record:
        openAiTranscript = fallback_record.get("aiTranscript") or []
        if not openAiTranscript:
            startText = f"Hi there {fallback_record.get('firstName', 'Candidate')}! Thanks for taking the time to connect with us today.<br><br>I'm an AI recruitment assistant helping our team complete your DevReady profile. I will ask a few quick questions about your work style and career goals. You can skip anything you prefer not to answer. Ready to get started?"
            openAiTranscript = [{'role':'assistant', 'content': startText}]
        try:
            question_count = countQuestions(domain)
        except Exception:
            question_count = len(engineeringSurvey.get_questions()) if engineeringSurvey.is_engineer_domain(domain) else 10
        return {
            "firstName": fallback_record.get("firstName", "Candidate"),
            "lastName": fallback_record.get("lastName", ""),
            "chatId": fallback_record.get("urlCode", urlcode),
            "personId": fallback_record.get("personId", ""),
            "chatEnd": fallback_record.get("chatEnd", ""),
            "chatClosed": fallback_record.get("chatClosed", False),
            "aiTranscript": openAiTranscript,
            "surveyId": None,
            "questionCount": question_count,
            "domain": "engineer" if engineeringSurvey.is_engineer_domain(domain) else "dev"
        }
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        # Count distinct candidates in the person table
        query = f"SELECT person.firstname, person.lastname, ai.* FROM person JOIN aichatlogs ai ON person.id = ai.personid WHERE ai.urlcode = '{urlcode}' ORDER BY ai.id DESC LIMIT 1"
        
        cur.execute(query)
        row = cur.fetchone()

        conn.close()

        openAiTranscript = []

        if row[6] is not None and len(row[6]) > 0:
            for r in row[6]:
                splitLocation = r.find(':')
                if r[0:splitLocation] == "DevReady AI":
                    openAiTranscript.append({'role':'assistant', 'content': r[splitLocation+1:]})
                else:
                    openAiTranscript.append({'role':'user', 'content': r[splitLocation+1:]})
        else:
            startText = f"Hi there {row[0]}! 👋 Thanks for taking the time to connect with us today.<br><br>I'm an AI recruitment assistant helping our team learn more about potential candidates. I'd love to ask you a few quick questions about what your thought process is as a developer. This will help us see how your skills might align with current or upcoming roles.<br><br>I've got a list of questions for you, and you can skip any that you'd prefer not to answer. Please answer each question on a scale from 1 to 5. Ready to get started?"
            openAiTranscript = [{'role':'assistant', 'content': startText}]

        return {
            "firstName": row[0],
            "lastName": row[1],
            "chatId": row[2],
            "personId": row[3],
            "chatEnd": row[4],
            "chatClosed": row[5],
            "aiTranscript": openAiTranscript,
            "surveyId": getSurveyId(row[3]),
            "questionCount": countQuestions(domain),
            "domain": "engineer" if engineeringSurvey.is_engineer_domain(domain) else "dev"
        }
    
    except Exception as e:
        print(f'Failed to grab chat logs for {urlcode}: {e}')
        return None

def saveChat(chatUrl: str, userName: str, aiTranscript: list):
    print(f'Saving transcript for {userName}')

    if isLocalChatUrl(chatUrl):
        records = _read_fallback_chats()
        record = records.get(chatUrl, {})
        record["aiTranscript"] = aiTranscript
        record["updatedAt"] = datetime.now().isoformat()
        records[chatUrl] = record
        _write_fallback_chats(records)
        return

    try:
        fixedTranscript = []

        for item in aiTranscript:
            if item['role'] == 'user':
                fixedTranscript.append(f"{userName}:{item['content']}")
            elif item['role'] == 'assistant':
                fixedTranscript.append(f"DevReady AI:{item['content']}")

        conn = client.getConnection()
        cur = conn.cursor()

        # Count distinct candidates in the person table
        query = "UPDATE aichatlogs SET transcript = %s WHERE urlcode = %s"
        cur.execute(query, (fixedTranscript, chatUrl))

        conn.commit()
        conn.close()
    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when saving transcript: {e}")
        raise
