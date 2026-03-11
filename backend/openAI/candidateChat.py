from openAI.client import getOpenAPIClient

def askQuestions(transcript: list, candidateName: str):
    systemInstructions = [{"role": "system",
                          "content":f'''You are an AI recruitment assistant. You will be chating with {candidateName}. MAKE NO HIRING PROMISES.
    It is your job to ask them the following questions in a casual yet professional manner.\nWhat is the most important part of a company's culture to you?'''}]

    client = getOpenAPIClient()

    print(transcript)

    fullTranscript = systemInstructions + transcript

    print(fullTranscript)

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=fullTranscript,
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        # Extract and print the response text
        print('AI Response:' + response.choices[0].message.content.strip())

        client.close()

        transcript.append({"role":"assistant", "content":response.choices[0].message.content.strip()})
        # Return the transcript to keep track of conversation along with most recent message
        return {"aiTranscript": transcript, "recentMessage": response.choices[0].message.content.strip()}

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        return []