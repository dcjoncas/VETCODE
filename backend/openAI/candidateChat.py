from openAI.client import getOpenAPIClient
from azure.storage.chatLogs import getQuestions, saveChat

candidateQuestions = getQuestions()

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
        return []