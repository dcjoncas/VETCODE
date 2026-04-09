import re

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

def processSkillYears(resumeText: str, skillName: str):
    systemInstructions = [{"role": "system",
                    "content":'''You are an AI assisstant. It is your job to analyze a resume's text and return only the number of years of experience for the specified skill being asked for. ADD NO ADDITIONAL COMMENTARY. ONLY RETURN AN INTEGER VALUE.'''}]

    aiClient = getOpenAPIClient()

    personalityInput = [{"role": "user",
                                "content":f'''How many years of experience does the candidate have with {skillName}?\n{resumeText}'''}]
        
    aiInput = systemInstructions + personalityInput

    response = aiClient.chat.completions.create(
                    model="gpt-3.5-turbo",  # Specify the model
                    messages=aiInput,
                    max_completion_tokens=100, # Limit the response length to manage costs
                    temperature=0.7 # Control the randomness of the response
                )

    experienceLevel = int(re.sub(r'[^0-9]', '',response.choices[0].message.content.strip()))

    if experienceLevel < 1:
        experienceLevel = 1
    elif experienceLevel > 10:
        experienceLevel = 10

    aiClient.close()

    return experienceLevel

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
            max_completion_tokens=500, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )
    
    answer = response.choices[0].message.content.strip()

    client.close()

    return answer

def candidateCulturalExperience(resumeText: str):
    systemInstructions = [{"role": "system",
                    "content":'''You are an AI assisstant. It is your job to analyze a resume's text and return only a comma separated list of the candidate's high level, abstract cultural experiences, such as the industries they've worked in or general types of roles they've held. ADD NO ADDITIONAL COMMENTARY. DO NOT RETURN LOCATION NAMES. ONLY RETURN THE RAW DATA.'''}]

    client = getOpenAPIClient()

    personalityInput = [{"role": "user",
                                "content":f'''Return a comma separated list of the candidate's high level cultural experiences. Return only 4 or 5 list items:\n{resumeText}'''}]
        
    fullTranscript = systemInstructions + personalityInput

    response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=fullTranscript,
            max_completion_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )
    
    experienceList = response.choices[0].message.content.strip().split(',')

    processedExperienceList = []

    for experience in experienceList:
        experience = experience.strip()
        systemInstructions = [{"role": "system",
                    "content":'''You are an AI assisstant. It is your job to analyze a resume's text and determine how much experience a candidate has in a specific area on a scale of 1 to 3. ADD NO ADDITIONAL COMMENTARY. ONLY RETURN AN INTEGER VALUE.'''}]
        
        personalityInput = [{"role": "user",
                                "content":f'''How much experience does the candidate have in {experience}? Return only a number from 1 to 3:\n{resumeText}'''}]
        
        fullTranscript = systemInstructions + personalityInput

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=fullTranscript,
            max_completion_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        experienceLevel = int(re.sub(r'[^0-9]', '',response.choices[0].message.content.strip()))

        if experienceLevel < 1:
            experienceLevel = 1
        elif experienceLevel > 3:
            experienceLevel = 3

        processedExperienceList.append({"experience": experience, "level": experienceLevel})

    client.close()

    return processedExperienceList