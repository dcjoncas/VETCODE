from openAI.client import getOpenAPIClient
from azureUtils.storage.chatLogs import getQuestions, saveChat, getQuestion, upsertSurveyAnswer, getPersonId, getSurveyId, ensureSurveyId
from openAI import engineeringSurvey
import re

candidateQuestionsByDomain = {}

def get_candidate_questions(domain: str = "dev"):
    if engineeringSurvey.is_engineer_domain(domain):
        questions = engineeringSurvey.get_questions()
        return questions
    normalized_domain = (domain or "dev").strip().lower()
    if normalized_domain not in candidateQuestionsByDomain:
        candidateQuestionsByDomain[normalized_domain] = getQuestions(normalized_domain)
    return candidateQuestionsByDomain[normalized_domain]

def askQuestions(transcript: list, candidateName: str, chatUrl: str):
    questions = get_candidate_questions()
    systemInstructions = [{"role": "system",
                          "content":f'''You are an AI recruitment assistant. You will be chating with {candidateName}. MAKE NO HIRING PROMISES.
    It is your job to talk to them about the following statements in a casual yet professional manner. Focus on one statement at a time. Bring them up organically.\n{questions}'''}]

    client = getOpenAPIClient()

    fullTranscript = systemInstructions + transcript

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=fullTranscript,
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        client.close()

        transcript.append({"role":"assistant", "content":response.choices[0].message.content.strip()})
        saveChat(chatUrl,candidateName,transcript)

        # Return the transcript to keep track of conversation along with most recent message
        return {"aiTranscript": transcript, "recentMessage": response.choices[0].message.content.strip()}

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        return {}
    
# simple method to use the AI in some way as part of askQuestion
def getNumber(text: str):
    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Respond with the number in the following text as a numerical integer value. Add no additional text or commentary. if no number is in the text simply respond with 'N/A': " + text}
            ],
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        client.close()

        # Return the transcript to keep track of conversation along with most recent message
        return int(re.sub(r'[^0-9]', '',response.choices[0].message.content.strip()))

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        raise

def _question_text(question_number: int, domain: str = "dev") -> str:
    if engineeringSurvey.is_engineer_domain(domain):
        return engineeringSurvey.get_question(question_number)
    return getQuestion(question_number, domain)

def _next_question_message(question_number: int, domain: str = "dev") -> str:
    statement = _question_text(question_number, domain)
    clean_statement = (statement[0].lower() + statement[1:]) if statement else "this statement"
    return (
        f"Question {question_number}: On a scale from 1 to 5, how much do you agree with the statement: "
        f"**\"{clean_statement}\"** You can reply with just a number from **1 to 5**, "
        "and if you'd like, a brief explanation too."
    )

def _parse_scale_answer(user_response: str):
    match = re.search(r"\b([1-5])\b", str(user_response or ""))
    return int(match.group(1)) if match else None

def _answered_questions_from_transcript(transcript: list):
    answers = []
    current_question = None
    for item in transcript or []:
        role = item.get("role")
        content = str(item.get("content") or "")
        if role == "assistant":
            match = re.search(r"Question\s+(\d+)\s*:", content, re.IGNORECASE)
            if match:
                current_question = int(match.group(1))
        elif role == "user" and current_question:
            answer = None if "skip" in content.lower() else _parse_scale_answer(content)
            if answer is not None:
                answers.append((current_question, answer))
            current_question = None
    return answers

def saveProgress(transcript: list, candidateName: str, chatUrl: str, domain: str = "dev"):
    person_id = getPersonId(chatUrl)
    saved = 0
    answers = _answered_questions_from_transcript(transcript)

    if person_id:
        for question_number, answer in answers:
            if engineeringSurvey.is_engineer_domain(domain):
                engineeringSurvey.save_answer(person_id, question_number, answer)
                saved += 1
            else:
                survey_id = getSurveyId(person_id) or ensureSurveyId(person_id, chatUrl)
                if survey_id:
                    upsertSurveyAnswer(question_number, answer, survey_id)
                    saved += 1

    saveChat(chatUrl, candidateName, transcript)
    return {
        "ok": True,
        "savedAnswers": saved,
        "answeredQuestions": len(answers),
        "recentMessage": f"Saved {saved} personality answer{'s' if saved != 1 else ''} to the profile.",
        "aiTranscript": transcript,
    }
    
def askQuestion(transcript: list, candidateName: str, chatUrl: str, questionNumber: int, domain: str = "dev"):
    questions = get_candidate_questions(domain)
    current_total = len(questions)
    userResponse = transcript[len(transcript)-1]['content']

    if questionNumber <= 1:
        question = _next_question_message(1, domain)
        transcript.append({"role":"assistant", "content":question})
        saveChat(chatUrl,candidateName,transcript)
        return {"aiTranscript": transcript, "recentMessage": question, "answered": False}

    if 'skip' in userResponse.lower():
        questionAnswer = None
    else:
        questionAnswer = _parse_scale_answer(userResponse)
        if questionAnswer is None:
            retry = "Please choose a number from **1 to 5** for the current question."
            transcript.append({"role":"assistant", "content":retry})
            saveChat(chatUrl,candidateName,transcript)
            return {"aiTranscript": transcript, "recentMessage": retry, "answered": False}

    print(f'{candidateName} Answered: {questionAnswer}')

    person_id = getPersonId(chatUrl)
    answered_question_number = questionNumber - 1
    if questionAnswer is not None:
        if engineeringSurvey.is_engineer_domain(domain):
            engineeringSurvey.save_answer(person_id, answered_question_number, questionAnswer)
        else:
            survey_id = getSurveyId(person_id) or ensureSurveyId(person_id, chatUrl)
            if survey_id:
                upsertSurveyAnswer(answered_question_number, questionAnswer, survey_id)

    if questionNumber > current_total:
        question = "Thanks, your DevReady profile chat is complete. Our team will review the completed profile and follow up with next steps."
    else:
        question = _next_question_message(questionNumber, domain)
    transcript.append({"role":"assistant", "content":question})

    saveChat(chatUrl,candidateName,transcript)

    # Return the transcript to keep track of conversation along with most recent message
    return {"aiTranscript": transcript, "recentMessage": question, "answered": True}

def openEndedQuestion(transcript: list, candidateName: str, chatUrl: str):
    systemInstructions = [{"role": "system",
                          "content":f'''You are an AI recruitment assistant. You will be chating with {candidateName}. MAKE NO HIRING PROMISES.
    Have a casual, yet professional conversation with them asking about their career goals and work experience.'''}]

    fullTranscript = systemInstructions + transcript

    client = getOpenAPIClient()

    response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=fullTranscript,
            max_completion_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )
    
    question = response.choices[0].message.content.strip()

    client.close()

    transcript.append({"role":"assistant", "content":question})
    saveChat(chatUrl,candidateName,transcript)
    return {"aiTranscript": transcript, "recentMessage": question}
