from fastapi import APIRouter, Form
from azure.storage import chatLogs
from openAI import candidateChat
import json

router = APIRouter(
    prefix="/api/chat",
    tags=["chat", "candidates"]
)

@router.post("/scheduleChat")
async def scheduleChats(profileid: str = Form(default="")):
    print(f"Scheduling for candidate: {profileid}")
    return chatLogs.scheduleChat(profileid)

@router.get("/getChat/{urlcode}")
async def getChat(urlcode: str):
    print(f"Retrieving chat for candidate: {urlcode}")
    return chatLogs.getChat(urlcode)

@router.get("/getUrlCode/{personid}")
async def getId(personid: str):
    print(f"Retrieving chat for candidate: {personid}")
    return chatLogs.getChatUrl(personid)

@router.post("/sendChat")
async def getChat(transcript: str = Form(...), candidateName: str = Form("Not Found"), chatUrl: str = Form("Not Found")):
    transcript_list = json.loads(transcript)
    print(f"Sending chat for candidate: {candidateName}")
    return candidateChat.askQuestions(transcript_list, candidateName, chatUrl)

@router.post("/sendChat/{questionNumber}")
async def getChat(transcript: str = Form(...), candidateName: str = Form("Not Found"), chatUrl: str = Form("Not Found"), questionNumber: int = 0):
    transcript_list = json.loads(transcript)
    print(f"Sending chat for candidate: {candidateName}")
    return candidateChat.askQuestion(transcript_list, candidateName, chatUrl, questionNumber)