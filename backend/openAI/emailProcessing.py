from openAI.client import getOpenAPIClient
from pydantic import BaseModel
from azureUtils.storage.jobs import getJob

class candidateScores(BaseModel):
    id: str
    name: str
    score: str

def shortlistClientEmail(jobId: int, candidates: list[candidateScores]):
    jobData = getJob(int(jobId))

    candidateString = ""

    # TODO: Add candidate descriptions
    for candidate in candidates:
        candidateString = f"{candidateString}\n{candidate.name}: {candidate.score}"

    print(candidateString)

    systemInstructions = [{"role": "system",
                            "content":f'''You are an AI recruiter assistant. Your job is to generate an email to send to the hiring managers at our client company, {jobData["company"]}, telling them about a list of candidates. MAKE ALL CANDIDATES SHINE. RETURN ONLY THE EMAIL BODY. Here is the job description: {jobData["description"]}'''}]
    userInstructions = [{'role':'user', 'content': f"Here is the list of candidates and their associated job match scores:{candidateString}"}]

    fullTranscript = systemInstructions + userInstructions

    client = getOpenAPIClient()

    response = client.chat.completions.create(
            model="gpt-5.4-mini",  # Specify the model
            messages=fullTranscript,
            temperature=0.7 # Control the randomness of the response
        )

    client.close()

    return {'email':response.choices[0].message.content.strip()}