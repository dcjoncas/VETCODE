from openAI.client import getOpenAPIClient
import re
import psycopg.cursor as cursorType

# Get AI to determine which personality traits are most beneficial to the company
def processPersonalities(jobId: int, jobDescription: str, azureCursor: cursorType.Cursor):
    systemInstructions = [{"role": "system",
                    "content":'''You are an AI assisstant. It is your job to analyze a job description and return a single numerial value on a scale from 1 to 5 in 
                    regards to how well you believe a certain trait will benefit that job. ADD NO ADDITIONAL COMMENTARY. ONLY RETURN A NUMERICAL INTEGER VALUE BETWEEN 1 AND 5.'''}]

    aiClient = getOpenAPIClient()

    query = 'SELECT id, title FROM personality'
    azureCursor.execute(query)

    personalityCategories = azureCursor.fetchall()

    for personality in personalityCategories:
        personalityInput = [{"role": "user",
                                "content":f'''On a scale of 1 to 5, how important is it that a potential candidate be {personality[1]}?\n{jobDescription}'''}]
        
        aiInput = systemInstructions + personalityInput

        response = aiClient.chat.completions.create(
                        model="gpt-3.5-turbo",  # Specify the model
                        messages=aiInput,
                        max_completion_tokens=100, # Limit the response length to manage costs
                        temperature=0.7 # Control the randomness of the response
                    )
        
        score = int(re.sub(r'[^0-9]', '',response.choices[0].message.content.strip()))

        # Account for poor quality AI output
        if score > 5 and score < 10:
            score = 5
        elif score > 5:
            # Account for decimals in answer above
            score = round(float(score)/(10**(len(str(score))-1)))
        elif score < 1:
            score = 1

        query = f'INSERT INTO jobpersonalities (personalityid, jobid, score) VALUES ({personality[0]}, {jobId}, {score});'
        azureCursor.execute(query)

    aiClient.close()