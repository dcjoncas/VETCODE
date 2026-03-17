from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import httpx
from jose import jwt
import requests
import os
from dotenv import load_dotenv

load_dotenv()
AUTHORIZE_URL = f'https://login.microsoftonline.com/{os.getenv("AZURE_TENNANT_ID")}/oauth2/v2.0/authorize'
TOKEN_URL = f'https://login.microsoftonline.com/{os.getenv("AZURE_TENNANT_ID")}/oauth2/v2.0/token'
JWKS_URL = f'https://login.microsoftonline.com/{os.getenv("AZURE_TENNANT_ID")}/discovery/v2.0/keys'
LOGIN_CLIENT_ID = os.getenv("LOGIN_CLIENT_ID")
LOGIN_REDIRECT = os.getenv("LOGIN_REDIRECT")
LOGIN_CLIENT_SECRET = os.getenv("LOGIN_CLIENT_SECRET")

router = APIRouter()

@router.get("/login")
def login():
    params = {
        "client_id": LOGIN_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": LOGIN_REDIRECT,
        "response_mode": "query",
        "scope": "openid profile email",
    }

    url = AUTHORIZE_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(url)

@router.get("/auth/callback")
async def callback(request: Request):
    code = request.query_params.get("code")

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            TOKEN_URL,
            data={
                "client_id": LOGIN_CLIENT_ID,
                "client_secret": LOGIN_CLIENT_SECRET,
                "code": code,
                "redirect_uri": LOGIN_REDIRECT,
                "grant_type": "authorization_code",
            },
        )

    tokens = token_response.json()
    id_token = tokens.get("id_token")

    return {"id_token": id_token}

jwks = requests.get(JWKS_URL).json()

def verify_token(token):
    return jwt.decode(
        token,
        jwks,
        algorithms=["RS256"],
        audience=LOGIN_CLIENT_ID,
        issuer=f"https://login.microsoftonline.com/{os.getenv("AZURE_TENNANT_ID")}/v2.0"
    )