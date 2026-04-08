import azureUtils.storage.client as client
import azureUtils.storage.processingFunctions as processing
from azureUtils.storage.candidates import getProfessionalProfileId, getSurveyId
from datetime import datetime, timedelta
import random
import string
import json

def getPersonId(chatUrl: str):
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        query = f"SELECT person.id FROM person JOIN aichatlogs ai ON person.id = ai.personid WHERE ai.urlcode = '{chatUrl}';"
        
        cur.execute(query)
        result = cur.fetchone()

        conn.close()
        
        return result[0]
    
    except Exception as e:
        print(f'Failed to grab candidate ID for {chatUrl}: {e}')
        return None

def getChatUrl(personId: str):
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        query = f"SELECT ai.urlcode FROM person JOIN aichatlogs ai ON person.id = ai.personid WHERE person.id = '{personId}';"
        
        cur.execute(query)
        result = cur.fetchone()

        conn.close()
        
        return result[0]
    
    except Exception as e:
        print(f'Failed to grab chat URL for {personId}: {e}')
        return None
    
def getSurveyId(profId: str):
    try:
        conn = client.getConnection()
        cur = conn.cursor()

        query = f"SELECT id FROM professionalsurvey WHERE profileid = '{profId}' ORDER BY id DESC;"
        
        cur.execute(query)
        result = cur.fetchone()

        conn.close()
        
        return result[0]
    
    except Exception as e:
        print(f'Failed to grab candidate ID for {profId}: {e}')
        return None

def scheduleChat(profileid: str):
    weekFromNow = (datetime.now() + timedelta(weeks=1)).date()

    # Create URL
    characters = string.ascii_letters + string.digits
    # Use random.choices to select characters and join them into a string
    random_string = ''.join(random.choices(characters, k=10))

    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "INSERT INTO aichatlogs (personid, enddate, urlCode) VALUES (%s, %s, %s)"
    
    cur.execute(query, (profileid, weekFromNow, random_string))

    # TODO: Send email with link to the candidate

    createSurvey(getProfessionalProfileId(profileid), random_string)
    conn.commit()
    conn.close()
    
    return random_string

def createSurvey(profprofileId: int, token: str):
    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "INSERT INTO professionalsurvey (profileid, token) VALUES (%s, gen_random_uuid())"
    
    cur.execute(query, (profprofileId,))

    conn.commit()
    conn.close()

def countQuestions() -> int:
    conn = client.getConnection()
    cur = conn.cursor()

    # Count distinct candidates in the person table
    query = "SELECT COUNT(DISTINCT id) FROM question"

    cur.execute(query)
    result = cur.fetchall()

    conn.close()

    return result[0][0]

def getQuestions():
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

def getQuestion(questionId: int) -> str:
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

    profSurvId = profId

    try:
        # Count distinct candidates in the person table
        query = "INSERT INTO professionalsurveyquestion (professionalsurveyid, surveyquestionid, answer) VALUES (%s, %s, %s)"

        print(query)
        print(profSurvId)
        
        cur.execute(query, (profSurvId, questionId, surveyResponse))

    except Exception as e:
        print(f'Cannot insert candidate answers: {e}')

    conn.commit()
    conn.close()

def getChat(urlcode: str):
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
            "questionCount": countQuestions()
        }
    
    except Exception as e:
        print(f'Failed to grab chat logs for {urlcode}: {e}')
        return None

def saveChat(chatUrl: str, userName: str, aiTranscript: list):
    print(f'Saving transcript for {userName}')

    try:
        fixedTranscript = []

        for item in aiTranscript:
            if item['role'] == 'user':
                fixedTranscript.append(f'{userName}:{item['content']}')
            elif item['role'] == 'assistant':
                fixedTranscript.append(f'DevReady AI:{item['content']}')

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