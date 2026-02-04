import os
from dotenv import load_dotenv
import hmac
import hashlib
import base64
import time
import requests
import json

# Load environment variables
load_dotenv()
remoteKey = os.getenv("DUX_SOUP_REMOTE_KEY")
remoteURL = os.getenv("DUX_SOUP_REMOTE_URL")
userID = os.getenv("DUX_SOUP_USERID") 
duxSoupBaseURL = f'https://app.dux-soup.com/xapi/remote/control/{userID}/queue'

def calculate_hmac(message: str) -> str:
    # Convert strings to bytes
    key_bytes = remoteKey.encode("utf-8")
    message_bytes = message.encode("utf-8")

    # Calculate HMAC-SHA1
    digest = hmac.new(
        key_bytes,
        message_bytes,
        hashlib.sha1
    ).digest()

    return base64.b64encode(digest).decode("utf-8")

def getProfilePDF(profileURL) -> str:
    # Prepare payload
    payload = {
        "targeturl": duxSoupBaseURL,
        "timestamp": int(time.time() * 1000),
        "userid": userID,
        "command": "profiletopdf",
        "params": {
            "profile": profileURL
        }
    }

    # Stringify payload JSON and calculate HMAC signature
    message = json.dumps(payload, separators=(',', ':'), sort_keys=False)
    signature = calculate_hmac(message) 

    headers = {
        'Content-Type': 'application/json',
        'X-Dux-Signature': signature,
    }

    # Send POST Request
    response = requests.post(duxSoupBaseURL, headers=headers, data=message)

    if response.status_code == 200:
        returnedData = response.json()
        return returnedData['messageid']
    else:
        return f"Failed to retrieve profile data, status code: {response.status_code}"

# TEMP TEST CODE
print('Dux Soup Profiles module loaded.')
print('Running test profile fetch...')
print(duxSoupBaseURL)
data = getProfilePDF('https://www.linkedin.com/in/mack-schmaltz-786952a3/')
print('Test fetch complete.')
print('Results: ' + str(data))