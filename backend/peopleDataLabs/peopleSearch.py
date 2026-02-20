import os
from dotenv import load_dotenv
import requests
import math
import json
#from peopledatalabs import PDLPY

# Load environment variables
load_dotenv()

PDL_API_KEY = os.getenv("PDL_API_KEY")

def searchSkills(skillList: list[str], size: int = 5):
    url = "https://api.peopledatalabs.com/v5/person/search"

    headers = {
        'Content-Type': 'application/json',
        'X-Api-Key': PDL_API_KEY,
    }

    # Prospects should match at least 80% of the skills in the list
    meet80min = math.ceil(len(skillList) * 0.8)

    payload = {
        "query": {
            "bool": {
                "should": [
                    {"term": {"skills": skill}} for skill in skillList
                ]
            }
        },
        "size": size
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to retrieve data, status code: {response.status_code}")
    
def searchSkillsAndLocation(skillList: list[str], locationCity: str = "", locationState: str = "", locationCountry: str = "", size: int = 5):
    url = "https://api.peopledatalabs.com/v5/person/search"

    headers = {
        'Content-Type': 'application/json',
        'X-Api-Key': PDL_API_KEY,
    }

    print("Skill Input: " + str(skillList))
    print("Location City Input: " + locationCity)

    shouldArray = []
    mustArray = []
    # Prospects should match at least 80% of the skills in the list
    meet80min = math.ceil(len(skillList) * 0.8)

    skillListLowercase = [skill.lower() for skill in skillList]

    shouldArray = [{"match": skillListLowercase}]

    #for skill in skillList:
        #shouldArray.append({"match": {"skills": skill.lower()}})
    
    if len(locationCity) > 0:
        mustArray.append({"match": {"location_locality": locationCity.lower()}})
    if len(locationState) > 0:
        mustArray.append({"match": {"location_region": locationState.lower()}})
    if len(locationCountry) > 0:
        mustArray.append({"match": {"location_country": locationCountry.lower()}})

    payload = {
        "query": {
            "bool": {
                "must": mustArray,
                "should": shouldArray
            }
        },
        "size": size
    }

    print("Payload for PeopleDataLabs Search: " + str(payload))

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"PeopleDataLabs API Error: {response.status_code}, Response: {response.text}")
        raise Exception(f"Failed to retrieve data, status code: {response.status_code}")
    