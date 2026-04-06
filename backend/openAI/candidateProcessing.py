from openAI.client import getOpenAPIClient

def processGeneral(resumeText: str, requestedInfo: str):
    systemInstructions = [{"role": "system",
                    "content":'''You are an AI assisstant. It is your job to analyze a resume's text and return only the specific data being asked for. ADD NO ADDITIONAL COMMENTARY. ONLY RETURN THE RAW DATA.'''}]

    aiClient = getOpenAPIClient()

    personalityInput = [{"role": "user",
                                "content":f'''What is the candidate's {requestedInfo}?\n{resumeText}'''}]
        
    aiInput = systemInstructions + personalityInput

    response = aiClient.chat.completions.create(
                    model="gpt-3.5-turbo",  # Specify the model
                    messages=aiInput,
                    max_completion_tokens=100, # Limit the response length to manage costs
                    temperature=0.7 # Control the randomness of the response
                )
    
    answer = response.choices[0].message.content.strip()

    aiClient.close()

    return answer

def candidateDescription(resumeText: str):
    systemInstructions = [{"role": "system",
                    "content":'''You are an AI assisstant. It is your job to analyze a resume's text and return only a description of the candidate to be circulated to hiring managers.'''}]

    client = getOpenAPIClient()

    personalityInput = [{"role": "user",
                                "content":f'''Write a description of the candidate based on their resume:\n{resumeText}'''}]
        
    fullTranscript = systemInstructions + personalityInput

    response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=fullTranscript,
            max_completion_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )
    
    answer = response.choices[0].message.content.strip()

    client.close()

    return answer