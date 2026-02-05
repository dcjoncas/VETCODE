import os
from dotenv import load_dotenv
import requests
import json
#from peopledatalabs import PDLPY

# Load environment variables
load_dotenv()

PDL_API_KEY = os.getenv("PDL_API_KEY")

def searchSkills(skillList: list[str]):
    url = "https://api.peopledatalabs.com/v5/person/search"

    headers = {
        'Content-Type': 'application/json',
        'X-Api-Key': PDL_API_KEY,
    }

    payload = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"skills": skill}} for skill in skillList
                ]
            }
        },
        "size": 5
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        return response.json()
    else:
        return f"Failed to retrieve data, status code: {response.status_code}"
    
print(searchSkills(["Python", "Data Analysis"]))