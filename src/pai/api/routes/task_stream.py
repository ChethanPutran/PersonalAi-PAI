"""Shared live-status streaming for a chat session (WebSocket and SSE).

Both transports show the same thing: every ``task.*`` and ``dag.*`` event whose
payload carries this ``session_id``. The subscribe / filter / bounded-queue logic and
the session-ownership rule live here so the two transports cannot drift apart.
"""
from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from pai.event_bus.events import Event
from pai.storage.models import ChatSessionModel

STREAM_PATTERNS: tuple[str, ...] = ("task.*", "dag.*")
SSE_HEARTBEAT_SECONDS = 15.0
SSE_RETRY_MS = 3000


class StreamedEvent(BaseModel):
    """One event as shown to a client."""
    event: str                 # e.g. "task.started", "dag.task_finished"
    data: dict[str, Any]       # the event-bus payload (free-form by nature)

    def ws_payload(self) -> dict[str, Any]:
        # Flat payload + "event". ("type" is taken: task payloads carry the task's own type.)
        return {**self.data, "event": self.event}

    def sse_frame(self, event_id: int) -> str:
        body = json.dumps(self.data, default=str)
        return f"id: {event_id}\nevent: {self.event}\ndata: {body}\n\n"


class StreamAccess(StrEnum):
    OK = "ok"
    FORBIDDEN = "forbidden"        # session belongs to another user
    UNAVAILABLE = "unavailable"    # no event bus


async def check_stream_access(context: Any, session_id: str, user_id: str) -> StreamAccess:
    """A not-yet-existing session is allowed (the first POST creates it under the caller);
    an existing one must belong to ``user_id``."""
    async with context.database.session() as db:
        result = await db.execute(select(ChatSessionModel).where(ChatSessionModel.id == session_id))
        existing = result.scalar_one_or_none()
    if existing is not None and existing.user_id != user_id:
        return StreamAccess.FORBIDDEN
    if context.orchestrator.event_bus is None:
        return StreamAccess.UNAVAILABLE
    return StreamAccess.OK


class SessionEventStream:
    """``async with SessionEventStream(bus, session_id) as s: ev = await s.next()``.

    The bus awaits each handler and does not isolate slow ones, so the handler only
    pushes to a bounded local queue (drop-oldest on overflow) that the transport drains.
    """

    def __init__(self, event_bus: Any, session_id: str, *, maxsize: int = 100,
                 patterns: tuple[str, ...] = STREAM_PATTERNS) -> None:
        self._bus, self._session_id, self._patterns = event_bus, session_id, patterns
        self._queue: asyncio.Queue[StreamedEvent] = asyncio.Queue(maxsize=maxsize)
        self._sub_ids: list[str] = []

    async def __aenter__(self) -> "SessionEventStream":
        self._sub_ids = [await self._bus.subscribe(p, self._on_event) for p in self._patterns]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        for sub_id in self._sub_ids:          # best effort: the bus may already be shut down
            try:
                await self._bus.unsubscribe(sub_id)
            except Exception:
                pass
        self._sub_ids = []

    async def _on_event(self, event: Event) -> None:
        if event.data.get("session_id") != self._session_id:
            return
        item = StreamedEvent(event=getattr(event.event_type, "value", str(event.event_type)),
                             data=dict(event.data))
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def next(self) -> StreamedEvent:
        return await self._queue.get()

    async def next_or_none(self, timeout: float) -> Optional[StreamedEvent]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


async def sse_events(
    stream: SessionEventStream,
    *,
    session_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """The text/event-stream body. Each event carries a per-connection ``id:``; a comment
    heartbeat keeps proxies from closing an idle connection and lets us notice a
    disconnect. (The bus is live-only, so ``Last-Event-ID`` cannot replay missed events.)"""
    yield f"retry: {SSE_RETRY_MS}\n\n"
    yield f": connected session={session_id}\n\n"
    event_id = 0
    while True:
        item = await stream.next_or_none(heartbeat_seconds)
        if await is_disconnected():
            logger.debug("SSE client for session {} went away", session_id)
            return
        if item is None:
            yield ": keep-alive\n\n"
            continue
        event_id += 1
        yield item.sse_frame(event_id)
