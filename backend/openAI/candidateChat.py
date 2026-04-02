from openAI.client import getOpenAPIClient
from azure.storage.chatLogs import getQuestions, saveChat, getQuestion, upsertSurveyAnswer, getPersonId, getSurveyId
import re

candidateQuestions = getQuestions()
totalQuestions = len(candidateQuestions)

def askQuestions(transcript: list, candidateName: str, chatUrl: str):
    systemInstructions = [{"role": "system",
                          "content":f'''You are an AI recruitment assistant. You will be chating with {candidateName}. MAKE NO HIRING PROMISES.
    It is your job to talk to them about the following statements in a casual yet professional manner. Focus on one statement at a time. Bring them up organically.\n{candidateQuestions}'''}]

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
    
def askQuestion(transcript: list, candidateName: str, chatUrl: str, questionNumber: int):
    userResponse = transcript[len(transcript)-1]['content']
    
    try:
        question = getQuestion(questionNumber)
        question = f'On a scale from 1 to 5, how much do you agree with the statement "{question[0].lower() + question[1:]}"'
        questionAnswer = 0

        systemInstructions = [{"role": "system",
                            "content":f'''You are an AI recruitment assistant. You will be chating with {candidateName}. MAKE NO HIRING PROMISES. NEVER SAY END THE CONVERSATION OR TELL THE CANDIDATE IT IS THE LAST QUESTION. THERE ARE {totalQuestions} QUESTIONS TO ANSWER.
        It is your job to ask them about the following statement in a casual yet professional manner. FOCUS ONLY ON THE FOLLOWING STATEMENT:\n{question}'''}]

    except Exception as e:
        systemInstructions = [{"role": "system",
                            "content":f'''You are an AI recruitment assistant. You will be chating with {candidateName}. MAKE NO HIRING PROMISES.
                            Have a casual, yet professional conversation with them asking about their career goals and work experience.'''}]

    client = getOpenAPIClient()

    fullTranscript = systemInstructions + transcript

    response = client.chat.completions.create(
            model="gpt-5.4-mini",  # Specify the model
            messages=fullTranscript,
            max_completion_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

    client.close()

    question = response.choices[0].message.content.strip()

    # If first question
    if (questionNumber <= 1) or ('skip' in userResponse.lower()):
        # question = "Great, let's start with the first question: " + question
        print('skipping')
        transcript.append({"role":"assistant", "content":question})
        saveChat(chatUrl,candidateName,transcript)
        return {"aiTranscript": transcript, "recentMessage": question, "answered": True}
    # If candidate wants to skip
    #elif 'skip' in userResponse.lower():
        # question = "No worries, let's move on. " + question
        #return {"aiTranscript": transcript, "recentMessage": question, "answered": True}
    else:
        try:
            questionAnswer = int(re.sub(r'[^0-9]', '', userResponse))
        except Exception as e:
            print(f'Could not pull valid value from user response. Attempting with AI: {e}')

            try:
                questionAnswer = getNumber(userResponse)
            except Exception as e:
                print(f'Could not pull value using AI. Asking user to repeat: {e}')
                systemInstructions = [{"role": "system",
                        "content":f'''The previous answer from the candidate could not be processed. Ask the candidate to repeat their answer to the following question as an integer value.
                        FOCUS ONLY ON THE FOLLOWING STATEMENT:\n{question}'''}]
                
                client = getOpenAPIClient()

                fullTranscript = systemInstructions + transcript

                response = client.chat.completions.create(
                        model="gpt-5.4-mini",  # Specify the model
                        messages=fullTranscript,
                        max_completion_tokens=100, # Limit the response length to manage costs
                        temperature=0.7 # Control the randomness of the response
                    )
                
                question = response.choices[0].message.content.strip()

                client.close()

                transcript.append({"role":"assistant", "content":question})
                saveChat(chatUrl,candidateName,transcript)
                return {"aiTranscript": transcript, "recentMessage": question, "answered": False}

        print(f'{candidateName} Answered: {questionAnswer}')

        if questionAnswer > 5 and questionAnswer < 10:
            questionAnswer = 5
        elif questionAnswer > 5:
            # Account for decimals in answer above
            questionAnswer = round(float(questionAnswer)/(10**(len(str(questionAnswer))-1)))
        elif questionAnswer < 1:
            questionAnswer = 1
        else:
            # Account for AI not understanding what an integer is
            round(questionAnswer)

        print(f'{candidateName} Corrected Answer: {questionAnswer}')

    upsertSurveyAnswer(questionNumber-1, questionAnswer, getSurveyId(getPersonId(chatUrl)))

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