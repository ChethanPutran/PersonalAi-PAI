"""Per-agent event log: ordered, bounded, replayable, live.

Clients (SSE/WebSocket) read it with ``stream(after=<last seq they saw>)``: they get what they
missed, then live events, and the stream ends once the log is closed and they are caught up.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from pai.agent.models import AgentEvent, AgentEventKind, utcnow


class EventLog:
    def __init__(self, capacity: int = 2000) -> None:
        self._events: Deque[AgentEvent] = deque(maxlen=capacity)
        self._seq = 0
        self._closed = False
        self._cond = asyncio.Condition()

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    async def append(self, kind: AgentEventKind, data: Dict[str, Any] | None = None) -> AgentEvent:
        async with self._cond:
            self._seq += 1
            event = AgentEvent(seq=self._seq, ts=utcnow(), kind=kind, data=data or {})
            self._events.append(event)
            self._cond.notify_all()
            return event

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    def snapshot(self, after: int = 0, limit: int | None = None) -> List[AgentEvent]:
        out = [e for e in self._events if e.seq > after]
        return out[-limit:] if limit else out

    async def next_batch(self, cursor: int, timeout: float | None = None) -> Optional[List[AgentEvent]]:
        """Events after ``cursor``; ``[]`` if ``timeout`` passed with none; ``None`` once the log is
        closed and the caller has seen everything. Safe for keep-alive polling (a timeout here
        does not tear down a generator, unlike ``wait_for(stream.__anext__())``)."""
        async with self._cond:
            def ready() -> bool:
                return self._closed or any(e.seq > cursor for e in self._events)
            try:
                await asyncio.wait_for(self._cond.wait_for(ready), timeout)
            except asyncio.TimeoutError:
                return []
            pending = [e for e in self._events if e.seq > cursor]
            return pending if pending else None

    async def stream(self, after: int = 0) -> AsyncIterator[AgentEvent]:
        cursor = after
        while True:
            batch = await self.next_batch(cursor)
            if batch is None:
                return                       # closed and fully drained
            for event in batch:
                cursor = event.seq
                yield event
