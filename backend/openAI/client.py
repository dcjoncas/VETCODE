import openai
import os
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def getOpenAPIClient() -> openai.OpenAI:
    return openai.OpenAI(api_key=OPENAI_API_KEY)