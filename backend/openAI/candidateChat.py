from openAI.client import getOpenAPIClient

def askQuestions(transcript: str, candidateName: str):
    systemInstructions = f'''You are an AI recruitment assistant. You will be chating with {candidateName}.
    It is your job to ask them the following questions in a casual yet professional manner.'''

    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": systemInstructions}, # System instructions
                {"role": "user", "content": f"From the following job description, return the required skills as a comma separated list. Be specific. Add no additional text or commentary: {jobDescription}"}
            ],
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        # Extract and print the response text
        print('AI Skill Response:' + response.choices[0].message.content.strip())

        skillList = [s.strip() for s in response.choices[0].message.content.split(",") if s.strip()]

        client.close()

        # Return the skills as a list by splitting the comma-separated string
        return skillList

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        return []