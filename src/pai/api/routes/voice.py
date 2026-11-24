"""Realtime voice: ``WS /voice/stream`` (see pai/voice/models.py for the wire protocol)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, ValidationError

from pai.api.dependencies import get_current_user
from pai.app_context import PAIAppContext, get_app_context
from pai.security.tokens.auth import decode_access_token
from pai.voice.models import (
    ErrorEvent, ServerEvent, SessionStart, client_message_adapter,
)
from pai.voice.service import VoiceService
from pai.voice.session import VoiceSession

router = APIRouter(prefix="/voice", tags=["voice"])

START_TIMEOUT_SECONDS = 10.0

# WebSocket close codes (4xxx = application specific)
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_BUSY = 4429
CLOSE_UNAVAILABLE = 4503


class VoiceCapabilities(BaseModel):
    available: bool
    reason: str = ""
    stt: Optional[str] = None
    tts: Optional[str] = None
    partial_transcripts: bool = False
    barge_in: bool = False
    input_sample_rate: int
    output_sample_rate: int


def _service(context: PAIAppContext) -> Optional[VoiceService]:
    return getattr(context, "voice", None)


@router.get("/capabilities", response_model=VoiceCapabilities)
async def voice_capabilities(
    _user: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> VoiceCapabilities:
    svc = _service(context)
    if svc is None or not svc.available:
        cfg = svc.config if svc else None
        return VoiceCapabilities(
            available=False, reason=(svc.unavailable_reason if svc else "voice is not configured"),
            input_sample_rate=cfg.input_sample_rate if cfg else 16000,
            output_sample_rate=cfg.output_sample_rate if cfg else 24000)
    return VoiceCapabilities(
        available=True, stt=svc.stt.name, tts=svc.tts.name, partial_transcripts=svc.stt.supports_partials,
        barge_in=svc.config.barge_in, input_sample_rate=svc.config.input_sample_rate,
        output_sample_rate=svc.tts.sample_rate)


class _Wire:
    """Serializes writes to the socket (events and audio come from different tasks)."""

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self._lock = asyncio.Lock()
        self.alive = True

    async def event(self, e: ServerEvent) -> None:
        if not self.alive:
            return
        async with self._lock:
            try:
                await self.ws.send_text(e.model_dump_json(exclude_none=True))
            except Exception:
                self.alive = False

    async def audio(self, pcm: bytes) -> None:
        if not self.alive:
            return
        async with self._lock:
            try:
                await self.ws.send_bytes(pcm)
            except Exception:
                self.alive = False


@router.websocket("/stream")
async def voice_stream(websocket: WebSocket) -> None:
    # ── 1. auth (token in the query string, like the device WebSocket) ──
    user_id: Optional[str] = None
    token = websocket.query_params.get("token")
    if token:
        with contextlib.suppress(Exception):
            user_id = decode_access_token(token)
    if user_id is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    context: PAIAppContext = websocket.app.state.context
    svc = _service(context)
    await websocket.accept()
    wire = _Wire(websocket)

    if svc is None or not svc.available:
        await wire.event(ErrorEvent(code="voice_unavailable", fatal=True,
                                    message=(svc.unavailable_reason if svc else "voice is not configured")))
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return
    if not svc.try_acquire(user_id):
        await wire.event(ErrorEvent(code="too_many_sessions", fatal=True,
                                    message="too many active voice sessions"))
        await websocket.close(code=CLOSE_BUSY)
        return

    session: Optional[VoiceSession] = None
    try:
        # ── 2. first message must be session.start ──
        start = await _read_start(websocket, wire)
        if start is None:
            return
        chat_session_id = start.session_id or uuid.uuid4().hex
        try:
            await svc.runner.ensure_chat_session(user_id, chat_session_id)   # type: ignore[union-attr]
        except PermissionError:
            await wire.event(ErrorEvent(code="forbidden", fatal=True, message="session belongs to another user"))
            await websocket.close(code=CLOSE_FORBIDDEN)
            return

        voice_session_id = uuid.uuid4().hex
        input_rate = start.input_sample_rate or svc.config.input_sample_rate
        session = VoiceSession(
            config=svc.config, stt=svc.stt, tts=svc.tts, runner=svc.runner,   # type: ignore[arg-type]
            user_id=user_id, chat_session_id=chat_session_id, device_id=start.device_id,
            send_event=wire.event, send_audio=wire.audio, input_sample_rate=input_rate,
            language=start.language, voice=start.voice,
            on_close=lambda reason: _close_socket(websocket),
        )
        svc.register(session)
        await wire.event(svc.ready_event(
            voice_session_id=voice_session_id, chat_session_id=chat_session_id, input_rate=input_rate))
        await session.start()
        logger.info("voice session {} started (user={} chat={} client={})",
                    voice_session_id, user_id[:8], chat_session_id, start.client.name)

        # ── 3. pump frames ──
        while wire.alive and not session._closed:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                break
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await session.on_audio(message["bytes"])
            elif message.get("text") is not None:
                try:
                    msg = client_message_adapter.validate_json(message["text"])
                except (ValidationError, json.JSONDecodeError) as exc:
                    await wire.event(ErrorEvent(code="bad_message", message=str(exc)[:200]))
                    continue
                if isinstance(msg, SessionStart):
                    await wire.event(ErrorEvent(code="already_started", message="session already started"))
                    continue
                await session.on_message(msg)
    except Exception:
        logger.exception("voice stream crashed")
        await wire.event(ErrorEvent(code="internal", message="internal error", fatal=True))
    finally:
        if session is not None:
            await session.close("disconnected" if wire.alive else "socket_error")
            svc.unregister(session)
        svc.release(user_id)
        with contextlib.suppress(Exception):
            await websocket.close()


async def _close_socket(websocket: WebSocket) -> None:
    """Once the session has ended (client left, idle timeout, server shutdown) the socket
    must go too — otherwise the receive loop would wait on a dead conversation."""
    with contextlib.suppress(Exception):
        await websocket.close(code=1001)


async def _read_start(websocket: WebSocket, wire: _Wire) -> Optional[SessionStart]:
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=START_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await wire.event(ErrorEvent(code="start_timeout", fatal=True, message="send session.start first"))
        return None
    except WebSocketDisconnect:
        return None
    text = message.get("text")
    if text is None:
        await wire.event(ErrorEvent(code="bad_start", fatal=True, message="first frame must be session.start"))
        return None
    try:
        msg = client_message_adapter.validate_json(text)
    except (ValidationError, json.JSONDecodeError):
        msg = None
    if not isinstance(msg, SessionStart):
        await wire.event(ErrorEvent(code="bad_start", fatal=True, message="first message must be session.start"))
        return None
    return msg
