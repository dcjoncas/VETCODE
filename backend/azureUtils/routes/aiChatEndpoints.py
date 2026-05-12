from azureUtils.storage import candidates
from fastapi import APIRouter, Form, HTTPException
from azureUtils.storage import chatLogs
from openAI import candidateChat
import json

router = APIRouter(
    prefix="/api/chat",
    tags=["chat", "candidates"]
)

@router.post("/scheduleChat")
async def scheduleChats(profileid: str = Form(default=""), domain: str = Form(default="dev")):
    print(f"Scheduling for candidate: {profileid}")
    candidate_domain = candidates.getCandidateDomain(profileid)
    if candidate_domain and candidate_domain != domain:
        raise HTTPException(status_code=403, detail="Candidate does not belong to this domain.")
    return chatLogs.scheduleChat(profileid)

@router.get("/getChat/{urlcode}")
async def getChat(urlcode: str, domain: str = "dev"):
    print(f"Retrieving chat for candidate: {urlcode}")
    return chatLogs.getChat(urlcode, domain)

@router.get("/getUrlCode/{personid}")
async def getId(personid: str):
    print(f"Retrieving chat for candidate: {personid}")
    return chatLogs.getChatUrl(personid)

@router.get("/getEmail/{personid}")
async def getEmail(personid: str):
    print(f"Retrieving email for candidate: {personid}")
    return candidates.getEmail(personid)

@router.post("/sendChat")
async def getChat(transcript: str = Form(...), candidateName: str = Form("Not Found"), chatUrl: str = Form("Not Found")):
    transcript_list = json.loads(transcript)
    print(f"Sending chat for candidate: {candidateName}")
    return candidateChat.openEndedQuestion(transcript_list, candidateName, chatUrl)

@router.post("/sendChat/{questionNumber}")
async def getChat(transcript: str = Form(...), candidateName: str = Form("Not Found"), chatUrl: str = Form("Not Found"), questionNumber: int = 0, domain: str = Form("dev")):
    transcript_list = json.loads(transcript)
    print(f"Sending {questionNumber} chat for candidate: {candidateName}")
    return candidateChat.askQuestion(transcript_list, candidateName, chatUrl, questionNumber, domain)
