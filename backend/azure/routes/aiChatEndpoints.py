from fastapi import APIRouter, Form
from azure.storage import chatLogs

router = APIRouter(
    prefix="/api/chat",
    tags=["chat", "candidates"]
)

@router.post("/scheduleChat")
async def count_candidates(profileid: str = Form(default="")):
    print(f"Scheduling for candidate: {profileid}")
    return chatLogs.scheduleChat(profileid)