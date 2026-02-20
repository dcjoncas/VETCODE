from openAI.client import getOpenAPIClient


def getPeopleSkills(jobDescription: str) -> list[str]:
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
        return []

def getPeopleCity(jobDescription: str) -> str:
    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."}, # System instructions
                {"role": "user", "content": f"From the following job description, return the name of the city that the job is located in. If no city is present in the job description, return 'No City Found'. Add no additional text or commentary. Do not return state, country, or anything similar: {jobDescription}"}
            ],
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        # Extract and print the response text
        print('AI City Response:' + response.choices[0].message.content.strip())

        if "no city" in response.choices[0].message.content.strip().lower():
            return ""

        return response.choices[0].message.content.strip()

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        return ""

def getPeopleState(jobDescription: str) -> str:
    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."}, # System instructions
                {"role": "user", "content": f"From the following job description, return the name of the state or province that the job is located in. If no state or province is present in the job description, return 'No State Found'. Add no additional text or commentary. Do not return city, country, or anything similar: {jobDescription}"}
            ],
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        # Extract and print the response text
        print('AI State Response:' + response.choices[0].message.content.strip())

        if "no state" in response.choices[0].message.content.strip().lower() or "no province" in response.choices[0].message.content.strip().lower():
            return ""

        return response.choices[0].message.content.strip()

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        return ""

def getPeopleCountry(jobDescription: str) -> str:
    client = getOpenAPIClient()

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."}, # System instructions
                {"role": "user", "content": f"From the following job description, return the name of the country that the job is located in. If no country is present in the job description, return 'No Country Found'. Add no additional text or commentary. Do not return city, state, or anything similar: {jobDescription}"}
            ],
            max_tokens=100, # Limit the response length to manage costs
            temperature=0.7 # Control the randomness of the response
        )

        # Extract and print the response text
        print('AI Country Response:' + response.choices[0].message.content.strip())

        if "no country" in response.choices[0].message.content.strip().lower():
            return ""

        return response.choices[0].message.content.strip()

    except Exception as e:
        # Handle potential API errors (e.g., authentication issues, rate limits)
        print(f"An API error occurred when calling ChatGPT: {e}")
        client.close()
        return ""