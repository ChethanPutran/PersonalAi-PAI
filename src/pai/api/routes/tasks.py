"""API Routes - Tasks management."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse


import asyncio
from loguru import logger
import contextlib
from sqlalchemy import select

from pydantic import BaseModel

from pai.api.routes.task_stream import (
    SessionEventStream, StreamAccess, check_stream_access, sse_events,
)
from pai.event_bus.events import Event
from pai.planning.dag_models import CancelOutcome
from pai.security.tokens.auth import decode_access_token
from pai.api.dependencies import get_current_user
from pai.api.routes.system import _serialize_task
from pai.app_context import PAIAppContext, get_app_context, get_orchestrator
from pai.orchestration.orchestrator import TaskOrchestrator
from pai.storage.models import ChatSessionModel
from pai.tasks.lifecycle import InvalidTaskTransition
from pai.tasks.models import Task, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ============================================================
# Helpers
# ============================================================

async def _require_owned_task(
    task_id: str,
    user_id: str,
    orchestrator: TaskOrchestrator,
) -> Task:
    """Fetch `task_id` and verify the caller owns it.

    Returns the Task if the caller may access it.
    Raises 404 if it doesn't exist; 403 if it belongs to someone
    else. Note: 403 rather than 404 on the cross-user case, so a
    caller cannot distinguish "doesn't exist" from "exists but is
    someone else's" by probing ids.
    """
    task = await orchestrator.task_manager.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.user_id and task.user_id != user_id:
        raise HTTPException(403, "Task belongs to another user")
    return task


# ============================================================
# List
# ============================================================

@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """List tasks belonging to the authenticated user.

    Optional `?status=running` filter — must match a `TaskStatus`
    value or the route 400s. The previous version accepted a bare
    string, so `?status=RUNNING` (wrong case) or `?status=garbage`
    silently passed through to the store, which returned an empty
    list instead of telling the caller the filter was invalid.
    """
    parsed_status: Optional[TaskStatus] = None
    if status:
        try:
            parsed_status = TaskStatus(status)
        except ValueError as exc:
            raise HTTPException(
                400,
                f"Unknown status {status!r}. "
                f"Valid values: {[s.value for s in TaskStatus]}",
            ) from exc

    tm = orchestrator.task_manager
    try:
        # Was `tm.list_tasks(status=None, user_id=user_id)` — list_tasks()
        # never accepted user_id, so the call raised TypeError, the route
        # raised 500, and the Flutter TasksScreen swallowed the exception
        # and rendered an empty list. `list()` is the user-scoped method.
        raw = await tm.list(
            status=parsed_status,
            user_id=user_id,
        )
    except Exception as exc:
        logger.exception("list_tasks failed for user %s", user_id)
        raise HTTPException(500, f"Failed to list tasks: {exc}")

    return {"tasks": [_serialize_task(t) for t in (raw or [])]}


# ============================================================
# Get
# ============================================================

@router.get("/{task_id}")
async def get_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Get task details. Enforces ownership."""
    task = await _require_owned_task(task_id, user_id, orchestrator)
    return {"task": _serialize_task(task)}


# ============================================================
# Pause / resume / cancel
# ============================================================

@router.post("/{task_id}/pause")
async def pause_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Pause a task. Only the owner may pause."""
    await _require_owned_task(task_id, user_id, orchestrator)

    try:
        task = await orchestrator.task_manager.pause(task_id)
    except InvalidTaskTransition as exc:
        # Wrong state — e.g. trying to pause a completed task.
        # 409 (conflict) so the client can distinguish this from a
        # generic 400 validation error.
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("pause failed for task %s", task_id)
        raise HTTPException(500, f"Pause failed: {exc}")

    if task is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Resume a paused task. Only the owner may resume."""
    await _require_owned_task(task_id, user_id, orchestrator)

    try:
        task = await orchestrator.task_manager.resume(task_id)
    except InvalidTaskTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("resume failed for task %s", task_id)
        raise HTTPException(500, f"Resume failed: {exc}")

    if task is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Cancel a task. Only the owner may cancel.

    Note the `await` below — the previous version did
    `task = orchestrator.task_manager.cancel(task_id)` without it, which
    assigned a coroutine object to `task`, then the truthiness check on
    that coroutine passed, and FastAPI tried to serialize a coroutine as
    the response body — silently producing `{}` or erroring during JSON
    encoding depending on the FastAPI version.
    """
    await _require_owned_task(task_id, user_id, orchestrator)

    try:
        task = await orchestrator.task_manager.cancel(task_id)
    except Exception as exc:
        logger.exception("cancel failed for task %s", task_id)
        raise HTTPException(500, f"Cancel failed: {exc}")

    if task is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Retry a FAILED task (grants one more attempt). Only the owner may retry."""
    await _require_owned_task(task_id, user_id, orchestrator)
    try:
        task = await orchestrator.task_manager.retry(task_id)
    except InvalidTaskTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize_task(task)


# ============================================================
# Kill ONE task of a running DAG (independent tasks keep running)
# ============================================================

class DagTaskCancelResponse(BaseModel):
    request_id: str
    task_id: str
    outcome: CancelOutcome


@router.post("/dag/{request_id}/tasks/{spec_id}/cancel", response_model=DagTaskCancelResponse)
async def cancel_dag_task(
    request_id: str,
    spec_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> DagTaskCancelResponse:
    dag = orchestrator.dag_scheduler
    owner = dag.owner_of(request_id)
    if owner is None:
        raise HTTPException(404, "No running plan with that request id")
    if owner != user_id:
        raise HTTPException(403, "Not your plan")
    outcome = await dag.cancel(request_id, spec_id)
    if outcome == CancelOutcome.NOT_FOUND:
        raise HTTPException(404, "Task not found in plan")
    return DagTaskCancelResponse(request_id=request_id, task_id=spec_id, outcome=outcome)


# ============================================================
# HIL (Confirm / Cancel) — for tasks that require human approval
# ============================================================
@router.post("/{task_id}/confirm")
async def confirm_task(
    task_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    tm = context.orchestrator.task_manager
    task = await tm.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.user_id != user_id:
        raise HTTPException(403, "Not your task")
    if task.status != TaskStatus.PAUSED:
        raise HTTPException(
            400,
            f"Task is {task.status.value}, not PAUSED — nothing to confirm",
        )
    await tm.resume(task_id)
    return {"task_id": task_id, "status": "confirmed"}


@router.post("/{task_id}/deny")
async def deny_task(
    task_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    tm = context.orchestrator.task_manager
    task = await tm.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.user_id != user_id:
        raise HTTPException(403, "Not your task")
    await tm.cancel(task_id, reason="User denied confirmation")
    return {"task_id": task_id, "status": "denied"}


@router.websocket("/{session_id}/stream")
async def chat_status_stream(websocket: WebSocket, session_id: str) -> None:
    """Live status updates for an in-flight chat request.

    The client opens this once when the chat screen mounts, and keeps
    it open across messages. Each `POST /chat` made while the WS is
    open produces a stream of `chat.status` events on the event bus;
    this route filters them by session_id and forwards the payload.

    Auth: `?token=<jwt>` on the query string (same pattern as the
    device WS at `/devices/{id}/ws`).

    Ownership: a not-yet-existing session is allowed — the first POST
    creates it under the caller. An existing session owned by another
    user is rejected with 4403.
    """
    # ── 1. Auth ──
    token = websocket.query_params.get("token")
    user_id: Optional[str] = None
    if token:
        try:
            user_id = decode_access_token(token)
        except Exception as exc:
            logger.debug("chat stream token decode failed: %s", exc)

    if user_id is None:
        await websocket.close(code=4401)
        return

    context = websocket.app.state.context
    event_bus = context.orchestrator.event_bus

    # ── 2. Ownership + availability ──
    access = await check_stream_access(context, session_id, user_id)
    if access == StreamAccess.FORBIDDEN:
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "Session belongs to another user",
        })
        await websocket.close(code=4403)
        return

    if access == StreamAccess.UNAVAILABLE:
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "Status stream unavailable",
        })
        await websocket.close(code=4511)
        return

    # ── 3. Subscribe (shared with the SSE route) ──
    stream = SessionEventStream(event_bus, session_id)
    await stream.__aenter__()

    await websocket.accept()
    await websocket.send_json({"type": "connected", "session_id": session_id})

    # ── 4. Drain queue → WS, detect disconnect ──
    async def _forward() -> None:
        while True:
            item = await stream.next()
            await websocket.send_json(item.ws_payload())

    async def _wait_disconnect() -> None:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                return

    try:
        forward_task = asyncio.create_task(_forward())
        disconnect_task = asyncio.create_task(_wait_disconnect())
        await asyncio.wait(
            {forward_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in (forward_task, disconnect_task):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
    except Exception:
        logger.exception("chat status stream error for %s", session_id)
    finally:
        await stream.__aexit__(None, None, None)    # best-effort unsubscribe
        with contextlib.suppress(Exception):
            await websocket.close()


# ============================================================
# SSE twin of the status WebSocket (same events, same access rule)
# ============================================================

@router.get("/{session_id}/events")
async def task_events_sse(
    session_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> StreamingResponse:
    """Live ``task.*`` / ``dag.*`` events for a chat session as ``text/event-stream``.

    Auth: ``Authorization: Bearer`` / cookie, or ``?token=`` (for ``EventSource``, which
    cannot set headers; tokens in URLs end up in logs, so prefer the header).
    """
    access = await check_stream_access(context, session_id, user_id)
    if access == StreamAccess.FORBIDDEN:
        raise HTTPException(403, "Session belongs to another user")
    if access == StreamAccess.UNAVAILABLE:
        raise HTTPException(503, "Status stream unavailable")

    stream = SessionEventStream(context.orchestrator.event_bus, session_id)
    await stream.__aenter__()

    async def body():
        try:
            async for chunk in sse_events(
                stream, session_id=session_id, is_disconnected=request.is_disconnected,
            ):
                yield chunk
        finally:
            await stream.__aexit__(None, None, None)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
