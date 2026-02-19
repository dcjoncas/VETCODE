from client import getOpenAPIClient

def getPeopleSkills(jobDescription: str):
    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."}, # System instructions
                {"role": "user", "content": f"From the following job description, return the required skills as a comma separated list. Add no additional text or commentary: {jobDescription}"}
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

def getPeopleCity(jobDescription: str):
    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."}, # System instructions
                {"role": "user", "content": f"From the following job description, return the name of the city that the job is located in. Add no additional text or commentary. Do not return state, country, or anything similar: {jobDescription}"}
            ],
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        # Extract and print the response text
        print('AI City Response:' + response.choices[0].message.content.strip())

        # Return the skills as a list by splitting the comma-separated string
        return response.choices[0].message.content.strip()

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()