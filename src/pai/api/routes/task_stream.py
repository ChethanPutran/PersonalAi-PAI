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
    should_stop: Callable[[], bool] = lambda: False,
) -> AsyncIterator[str]:
    """The text/event-stream body. Each event carries a per-connection ``id:``; a comment
    heartbeat keeps proxies from closing an idle connection and lets us notice a
    disconnect. (The bus is live-only, so ``Last-Event-ID`` cannot replay missed events.)"""
    yield f"retry: {SSE_RETRY_MS}\n\n"
    yield f": connected session={session_id}\n\n"
    event_id = 0
    idle = 0.0
    # Wait in short slices so a server shutdown (``should_stop``) ends the stream within ~1s
    # instead of after the next heartbeat.
    slice_s = min(1.0, heartbeat_seconds)
    while True:
        if should_stop():
            logger.debug("SSE stream for session {} ending: server shutting down", session_id)
            return
        item = await stream.next_or_none(slice_s)
        if await is_disconnected():
            logger.debug("SSE client for session {} went away", session_id)
            return
        if item is None:
            idle += slice_s
            if idle >= heartbeat_seconds:
                idle = 0.0
                yield ": keep-alive\n\n"
            continue
        idle = 0.0
        event_id += 1
        yield item.sse_frame(event_id)


# ============================================================================
# Per-USER task event stream  (GET /tasks/events)
# ============================================================================

class ReadyFrame(BaseModel):
    """``event: ready`` — the stream is live; ``seq`` is the newest event the server has."""
    seq: int
    replayed: int = 0


class ResyncFrame(BaseModel):
    """``event: resync`` — events were lost (too old / server restarted / client too slow).
    Reload the task list once; the stream continues from ``seq``."""
    seq: int
    reason: str


def _frame(event_id: int, name: str, body: BaseModel | dict[str, Any]) -> str:
    payload = body.model_dump_json() if isinstance(body, BaseModel) else json.dumps(body, default=str)
    return f"id: {event_id}\nevent: {name}\ndata: {payload}\n\n"


def parse_resume_point(last_event_id: Optional[str], after: Optional[int]) -> Optional[int]:
    """Where the client says it left off: ``Last-Event-ID`` wins, then ``?after=``; else None
    (a fresh connection). A malformed header is treated as fresh."""
    if last_event_id is not None and last_event_id.strip():
        try:
            return max(0, int(last_event_id.strip()))
        except ValueError:
            return None
    return after if after is None or after >= 0 else 0


async def user_task_sse(
    log: Any,                                   # TaskEventLog
    user_id: str,
    resume_after: Optional[int],
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    should_stop: Callable[[], bool] = lambda: False,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """SSE body for ONE user's events: replay what was missed, then stream live.

    Subscribes BEFORE replaying so nothing can fall in between; events seen twice are dropped
    by seq. Only ``user_id``'s log is ever read, so there is no cross-user path.
    """
    sub = await log.subscribe(user_id)
    try:
        yield f"retry: {SSE_RETRY_MS}\n\n"
        sent = resume_after or 0
        replayed = 0
        if resume_after is None:
            sent = await log.latest_seq(user_id)
        else:
            result = await log.replay(user_id, resume_after)
            if result.gap:
                sent = result.latest_seq
                yield _frame(sent, "resync", ResyncFrame(seq=sent, reason="history_unavailable"))
            else:
                for ev in result.events:
                    if ev.seq > sent:
                        sent = ev.seq
                        replayed += 1
                        yield _frame(ev.seq, ev.event, ev.data)
        yield _frame(sent, "ready", ReadyFrame(seq=sent, replayed=replayed))

        idle = 0.0
        slice_s = min(1.0, heartbeat_seconds)
        while True:
            if should_stop():
                return
            item = await sub.get(slice_s)
            if await is_disconnected():
                return
            if sub.needs_resync:
                sub.clear_resync()
                latest = await log.latest_seq(user_id)
                sent = max(sent, latest)
                idle = 0.0
                yield _frame(sent, "resync", ResyncFrame(seq=sent, reason="slow_consumer"))
                continue
            if item is None:
                idle += slice_s
                if idle >= heartbeat_seconds:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                continue
            if item.seq <= sent:                 # already delivered by the replay
                continue
            idle = 0.0
            sent = item.seq
            yield _frame(item.seq, item.event, item.data)
    finally:
        await sub.close()
