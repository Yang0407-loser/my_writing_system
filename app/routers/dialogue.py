"""对话模式 API。"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from ..agents.dialogue_agent import DialogueAgent, get_quick_prompts
from ..utils.llm_client import set_api_key

router = APIRouter(prefix="/api/dialogue", tags=["dialogue"])


class DialogueRequest(BaseModel):
    session_context: dict = {}
    user_message: str = ""


class SummarizeRequest(BaseModel):
    session_context: dict = {}
    conversation_history: list[dict] = []


@router.post("/chat")
def chat(req: DialogueRequest, x_api_key: str = Header("", alias="X-API-Key")):
    if x_api_key: set_api_key(x_api_key)
    agent = DialogueAgent()
    reply = agent.chat(req.session_context, req.user_message)
    return {"reply": reply}


@router.post("/summarize")
def summarize(req: SummarizeRequest, x_api_key: str = Header("", alias="X-API-Key")):
    if x_api_key: set_api_key(x_api_key)
    agent = DialogueAgent()
    result = agent.summarize(req.session_context, req.conversation_history)
    return result


@router.get("/quick-prompts")
def quick_prompts():
    return {"prompts": get_quick_prompts()}
