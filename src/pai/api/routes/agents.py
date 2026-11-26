"""Agents API: start a long-running agent, talk to it live, watch it work.

    POST   /agents                  start (goal + the capabilities it may use)
    GET    /agents                  list mine
    GET    /agents/{id}             state + progress
    POST   /agents/{id}/messages    say something to it (answers its question or changes course)
    POST   /agents/{id}/pause|resume|cancel
    GET    /agents/{id}/events      SSE: replay (Last-Event-ID / ?after=) then live
    WS     /agents/{id}/ws          both ways: events out, messages/commands in

Everything is scoped to the caller; another user's agent is a plain 404.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from pai.agent.errors import (AgentError, AgentLimitError, AgentNotFound, AgentSpawnError, AgentStateError)
from pai.agent.models import AgentInfo, AgentLimits, AgentSpec, AgentState
from pai.agent.runtime import AgentRuntime
from pai.api.dependencies import get_current_user
from pai.app_context import PAIAppContext, get_app_context
from pai.security.tokens.auth import decode_access_token

from pai.agent.plugin import AGENT_PLUGIN_DISABLED_MESSAGE, AGENT_PLUGIN_ID
router = APIRouter(prefix="/agents", tags=["agents"])

SSE_KEEPALIVE_SECONDS = 15.0
WS_UNAUTHORIZED = 4401
WS_NOT_FOUND = 4404
WS_UNAVAILABLE = 4503


class SpawnRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    tools: List[str] = Field(min_length=1, max_length=64,
                             description="Capability ids or prefix globs ('browser.*') the agent may use")
    name: Optional[str] = Field(default=None, max_length=80)
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    max_steps: Optional[int] = Field(default=None, ge=1, le=100)
    max_runtime_seconds: Optional[float] = Field(default=None, gt=0, le=4 * 3600)


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _runtime(context: PAIAppContext) -> AgentRuntime:
    if context.agents is None:
        raise HTTPException(503, "agents are not available on this server")
    return context.agents


def _http(exc: AgentError) -> HTTPException:
    code = {AgentNotFound: 404, AgentLimitError: 429, AgentSpawnError: 422, AgentStateError: 409}.get(type(exc), 400)
    return HTTPException(code, str(exc))


@router.post("", status_code=201, response_model=AgentInfo)
async def start_agent(body: SpawnRequest, user_id: str = Depends(get_current_user),
                      context: PAIAppContext = Depends(get_app_context)) -> AgentInfo:
    if not await context.orchestrator.plugin_manager.is_enabled_for_user(user_id, AGENT_PLUGIN_ID):
        raise HTTPException(403, detail={"code": "agent_plugin_disabled", "message": AGENT_PLUGIN_DISABLED_MESSAGE})
    limits: Dict[str, Any] = {k: v for k, v in (("max_steps", body.max_steps),
                                                ("max_runtime_seconds", body.max_runtime_seconds)) if v is not None}
    try:
        spec = AgentSpec(goal=body.goal, tools=body.tools, name=body.name, device_id=body.device_id,
                         limits=AgentLimits(**limits))
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return await _runtime(context).spawn(user_id, spec, session_id=body.session_id)
    except AgentError as exc:
        raise _http(exc) from exc


@router.get("", response_model=List[AgentInfo])
async def list_agents(state: Optional[AgentState] = None, user_id: str = Depends(get_current_user),
                      context: PAIAppContext = Depends(get_app_context)) -> List[AgentInfo]:
    return _runtime(context).list(user_id, state)


@router.get("/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: str, user_id: str = Depends(get_current_user),
                    context: PAIAppContext = Depends(get_app_context)) -> AgentInfo:
    try:
        return _runtime(context).info(user_id, agent_id)
    except AgentError as exc:
        raise _http(exc) from exc


@router.post("/{agent_id}/messages", status_code=202)
async def message_agent(agent_id: str, body: MessageRequest, user_id: str = Depends(get_current_user),
                        context: PAIAppContext = Depends(get_app_context)) -> Dict[str, Any]:
    try:
        await _runtime(context).send(user_id, agent_id, body.text)
    except AgentError as exc:
        raise _http(exc) from exc
    return {"delivered": True}


async def _control(action: str, agent_id: str, user_id: str, context: PAIAppContext) -> AgentInfo:
    runtime = _runtime(context)
    try:
        return await getattr(runtime, action)(user_id, agent_id)
    except AgentError as exc:
        raise _http(exc) from exc


@router.post("/{agent_id}/pause", response_model=AgentInfo)
async def pause_agent(agent_id: str, user_id: str = Depends(get_current_user),
                      context: PAIAppContext = Depends(get_app_context)) -> AgentInfo:
    return await _control("pause", agent_id, user_id, context)


@router.post("/{agent_id}/resume", response_model=AgentInfo)
async def resume_agent(agent_id: str, user_id: str = Depends(get_current_user),
                       context: PAIAppContext = Depends(get_app_context)) -> AgentInfo:
    return await _control("resume", agent_id, user_id, context)


@router.post("/{agent_id}/cancel", response_model=AgentInfo)
async def cancel_agent(agent_id: str, user_id: str = Depends(get_current_user),
                       context: PAIAppContext = Depends(get_app_context)) -> AgentInfo:
    return await _control("cancel", agent_id, user_id, context)


@router.get("/{agent_id}/events")
async def agent_events(
    agent_id: str, request: Request,
    after: Optional[int] = Query(default=None, ge=0, description="Resume after this seq (same as Last-Event-ID)"),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    user_id: str = Depends(get_current_user), context: PAIAppContext = Depends(get_app_context),
) -> StreamingResponse:
    try:
        log = _runtime(context).log(user_id, agent_id)
    except AgentError as exc:
        raise _http(exc) from exc
    cursor = after if after is not None else int(last_event_id) if (last_event_id or "").isdigit() else 0

    async def frames():
        nonlocal cursor
        while not context.shutting_down.is_set() and not await request.is_disconnected():
            batch = await log.next_batch(cursor, timeout=SSE_KEEPALIVE_SECONDS)
            if batch is None:
                return                                   # finished and fully delivered
            if not batch:
                yield ": keep-alive\n\n"
                continue
            for event in batch:
                cursor = event.seq
                yield f"id: {event.seq}\nevent: {event.kind.value}\ndata: {json.dumps(event.wire(), default=str)}\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.websocket("/{agent_id}/ws")
async def agent_ws(websocket: WebSocket, agent_id: str) -> None:
    """Talk to the agent. Server -> client: events (``{"seq", "kind", ...}``). Client -> server:
    ``{"type": "message", "text": "..."}``, ``{"type": "pause"|"resume"|"cancel"}``, ``{"type": "ping"}``.
    Reconnect with ``?after=<last seq>`` to replay what was missed."""
    token = websocket.query_params.get("token")
    try:
        user_id = decode_access_token(token) if token else None
    except Exception:
        user_id = None
    if not user_id:
        await websocket.close(code=WS_UNAUTHORIZED)
        return
    context: PAIAppContext = websocket.app.state.context
    if context.agents is None:
        await websocket.close(code=WS_UNAVAILABLE)
        return
    try:
        log = context.agents.log(user_id, agent_id)
    except AgentNotFound:
        await websocket.close(code=WS_NOT_FOUND)
        return

    await websocket.accept()
    after = websocket.query_params.get("after", "0")
    cursor = int(after) if after.isdigit() else 0

    async def pump() -> None:
        nonlocal cursor
        while not context.shutting_down.is_set():
            batch = await log.next_batch(cursor, timeout=SSE_KEEPALIVE_SECONDS)
            if batch is None:
                await websocket.send_json({"kind": "closed"})
                return
            for event in batch:
                cursor = event.seq
                await websocket.send_json(event.wire())

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            frame = await websocket.receive_json()
            kind = frame.get("type") if isinstance(frame, dict) else None
            try:
                if kind == "message":
                    await context.agents.send(user_id, agent_id, str(frame.get("text") or ""))
                elif kind in ("pause", "resume", "cancel"):
                    await getattr(context.agents, kind)(user_id, agent_id)
                elif kind == "ping":
                    await websocket.send_json({"kind": "pong"})
                else:
                    await websocket.send_json({"kind": "error", "detail": f"unknown frame type {kind!r}"})
            except (AgentError, ValueError) as exc:
                await websocket.send_json({"kind": "error", "detail": str(exc)})
    except Exception:                                    # disconnect / bad frame: just stop
        pass
    finally:
        pump_task.cancel()
        await asyncio.gather(pump_task, return_exceptions=True)     # no "exception never retrieved" noise
        try:
            await websocket.close()
        except Exception:
            pass
