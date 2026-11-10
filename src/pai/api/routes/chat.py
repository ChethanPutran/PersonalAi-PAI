"""
Chat endpoint — receives a user goal as text and drives the orchestrator.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user
from pai.app_context import get_app_context, get_orchestrator, get_db_session
from pai.app_context import PAIAppContext
from pai.orchestration.orchestrator import (
    OrchestrationError,
    TaskOrchestrator,
)
from pai.storage.repositories.chat import ChatRepository

router = APIRouter(prefix="/chat", tags=["chat"])

# Total time we allow a synchronous chat request to spend in the orchestrator.
CHAT_TIMEOUT_SECONDS = 120.0


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------

class ChatRequest(BaseModel):
    device_id: str = Field(..., description="Source device making the request")
    message: str = Field(..., min_length=1, description="User goal")
    mode: str = Field("text", description="'text' or 'voice'")
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None


class ChatTaskResult(BaseModel):
    task_id: Optional[str] = None
    status: Optional[str] = None
    capability: Optional[str] = None
    device_id: Optional[str] = None
    executor_id: Optional[str] = None
    error: Optional[str] = None
    output: Any = None
    reply: Optional[str] = None 


class ChatResponse(BaseModel):
    message: str
    status: str
    session_id: str   
    request_id: Optional[str] = None
    plan_id: Optional[str] = None
    task_id: Optional[str] = None
    tasks: List[ChatTaskResult] = []


# ------------------------------------------------------------------
# Response formatter
# ------------------------------------------------------------------

CHAT_CAPABILITY = "chat.reply"


def _format_chat_message(
    goal: str,
    status: str,
    results: List[Dict[str, Any]],
    failed: List[str],
) -> str:
    if status == "completed":
        if not results:
            return f"Done — no tasks were needed for: {goal!r}."

        # ── 1. If any task was a chat.reply, return its text verbatim.
        #    The LLM already wrote what the user should see.
        chat_replies = [
            r["reply"]
            for r in results
            if (r.get("capability") == CHAT_CAPABILITY) and r.get("reply")
        ]
        if chat_replies:
            return "\n\n".join(chat_replies)

        # ── 2. Otherwise summarise tool results.
        lines = [f"Completed {len(results)} task(s):"]
        for r in results:
            cap = r.get("capability") or r.get("type") or "task"
            dev = r.get("device_id") or "device"
            ok = r.get("status", "completed") == "completed"
            marker = "✓" if ok else "✗"
            lines.append(f"  {marker} {cap} on {dev}")
        return "\n".join(lines)

    if failed:
        return (
            f"I ran into an error. {len(failed)} task(s) failed: "
            f"{', '.join(failed)}"
        )

    return f"Request finished with status: {status}"


def _extract_tasks(results: List[Dict[str, Any]]) -> List[ChatTaskResult]:
    out: List[ChatTaskResult] = []
    for r in results:
        out.append(
            ChatTaskResult(
                task_id=r.get("task_id") or r.get("id"),
                status=r.get("status"),
                capability=r.get("capability") or r.get("type"),
                device_id=r.get("device_id"),
                executor_id=r.get("executor_id"),
                error=r.get("error"),
                output=r.get("output", r.get("result")),
                reply=r.get("reply"),
            )
        )
    return out

def _title_from_goal(goal: str) -> str:
    g = goal.strip().replace("\n", " ")
    return (g[:60] + "…") if len(g) > 60 else (g or "New chat")

# ------------------------------------------------------------------
# Main chat route
# ------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
    db_session: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    logger.info(
        "chat: user={} device={} goal={!r}",
        user_id,
        request.device_id,
        request.message,
    )
    session_id = request.session_id or uuid.uuid4().hex
    repo = ChatRepository(db_session)

    # 1. Ensure session exists + persist the user turn
    try:
        await repo.get_or_create_session(
            user_id, session_id, title=_title_from_goal(request.message),
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Session not owned by user")

    await repo.append_message(session_id, "user", request.message)
    await db_session.commit()

    # 2. Run orchestrator (unchanged), but always pass the concrete session_id
    try:
        result = await asyncio.wait_for(
            orchestrator.run(
                user_id=user_id,
                goal=request.message,
                session_id=session_id,
                source_device_id=request.device_id,
                context={
                    "mode": request.mode,
                    "device_id": request.device_id,
                },
            ),
            timeout=CHAT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(
                    "chat: orchestration timed out after {}s for goal={!r}",
                    CHAT_TIMEOUT_SECONDS,
                    request.message,
                )
        message = f"Execution exceeded {CHAT_TIMEOUT_SECONDS:.0f}s."
        await repo.append_message(
            session_id, "assistant", message,
            status="failed", metadata={"error": "timeout"},
        )
        
        await db_session.commit()
        return ChatResponse(
            message=message, status="failed", session_id=session_id,
            tasks=[],
        )
    
    except OrchestrationError as exc:
        logger.error("chat: orchestration error: {}", exc)
        message = f"Orchestration error: {exc}"
        await repo.append_message(
            session_id, "assistant", message,
            status="failed", metadata={"error": str(exc)},
        )
        await db_session.commit()
        return ChatResponse(
            message=message, status="failed", session_id=session_id, tasks=[],
        )
    except Exception as exc:
        logger.exception("chat: unexpected failure: {}", exc)
        message = f"Orchestrator failed: {exc}"
        await repo.append_message(
            session_id, "assistant", message,
            status="failed", metadata={"error": str(exc)},
        )
        await db_session.commit()
        return ChatResponse(
            message=message, status="failed", session_id=session_id, tasks=[],
        )
    # --------------------------------------------------------------
    # Format result
    # --------------------------------------------------------------

    # 3. Format + persist assistant turn
    status = result.get("status", "unknown")
    results_raw = list(result.get("results") or [])
    failed = list(result.get("failed") or [])
    tasks = _extract_tasks(results_raw)
    primary_task_id = tasks[0].task_id if tasks else None
    message = _format_chat_message(
        goal=request.message, status=status,
        results=results_raw, failed=failed,
    )

    await repo.append_message(
        session_id, "assistant", message,
        plan_id=result.get("plan_id"),
        task_id=primary_task_id,
        status=status,
        metadata={"tasks": [t.model_dump() for t in tasks]},
    )
    await db_session.commit()

    return ChatResponse(
        message=message,
        status=status,
        session_id=session_id,               # <-- client stores this
        request_id=result.get("request_id"),
        plan_id=result.get("plan_id"),
        task_id=primary_task_id,
        tasks=tasks,
    )



class ChatSessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    plan_id: Optional[str] = None
    task_id: Optional[str] = None
    status: Optional[str] = None
    tasks: List[ChatTaskResult] = []


class ChatSessionDetail(BaseModel):
    session: ChatSessionSummary
    messages: List[ChatMessageOut]


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)


def _session_summary(s) -> ChatSessionSummary:
    return ChatSessionSummary(
        id=s.id, title=s.title,
        message_count=s.message_count or 0,
        last_message_at=s.last_message_at,
        created_at=s.created_at, updated_at=s.updated_at,
    )


@router.get("/sessions", response_model=List[ChatSessionSummary])
async def list_sessions(
    limit: int = 50, offset: int = 0,
    user_id: str = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    repo = ChatRepository(db_session)
    rows = await repo.list_sessions(user_id, limit=limit, offset=offset)
    return [_session_summary(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    repo = ChatRepository(db_session)
    session = await repo.get_session_with_messages(session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = [
        ChatMessageOut(
            id=m.id, role=m.role, content=m.content, created_at=m.created_at,
            plan_id=m.plan_id, task_id=m.task_id, status=m.status,
            tasks=[ChatTaskResult(**t) for t in (m.metadata_json or {}).get("tasks", [])],
        )
        for m in session.messages
    ]
    return ChatSessionDetail(session=_session_summary(session), messages=messages)


@router.patch("/sessions/{session_id}", response_model=ChatSessionSummary)
async def rename_session(
    session_id: str, body: RenameRequest,
    user_id: str = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    repo = ChatRepository(db_session)
    ok = await repo.rename_session(session_id, user_id, body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    await db_session.commit()
    # re-fetch to return the canonical summary
    session = await repo.get_session_with_messages(session_id, user_id)
    return _session_summary(session)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    repo = ChatRepository(db_session)
    ok = await repo.delete_session(session_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    await db_session.commit()