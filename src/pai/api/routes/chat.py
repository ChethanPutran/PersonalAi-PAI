from fastapi import APIRouter
from fastapi import Depends
from pydantic import BaseModel
from typing import Optional

from pai.app_context import get_orchestrator
from pai.orchestration.orchestrator import TaskOrchestrator

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    device_id: str
    message: str
    mode: str = "text"
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    message: str
    task_id: Optional[str] = None


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, orchestrator: TaskOrchestrator = Depends(get_orchestrator)):

    # IMPORTANT:
    # This is where your existing orchestrator should be called.

    # Example:
    #
    # result = await orchestrator.handle(
    #     user_id=...,
    #     device_id=request.device_id,
    #     message=request.message,
    # )

    return ChatResponse(
        message=f"Received: {request.message}",
        task_id=None,
    )