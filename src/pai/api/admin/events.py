"""Admin-scoped live event stream: task.* and dag.* events of ALL sessions as SSE."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable, List, Optional

from loguru import logger

from pai.api.routes.task_stream import StreamedEvent

ADMIN_PATTERNS = ("task.*", "dag.*")
HEARTBEAT_SECONDS = 15.0


class AdminEventStream:
    """``async with AdminEventStream(bus) as s: ev = await s.next_or_none(timeout)``.

    The bus awaits each handler, so the handler only pushes into a BOUNDED queue
    (drop-oldest when a slow browser can't keep up) — it never blocks other subscribers.
    """

    def __init__(self, bus: Any, *, maxsize: int = 1000) -> None:
        self._bus = bus
        self._queue: "asyncio.Queue[StreamedEvent]" = asyncio.Queue(maxsize=maxsize)
        self._sub_ids: List[Any] = []
        self.dropped = 0

    async def __aenter__(self) -> "AdminEventStream":
        self._sub_ids = [await self._bus.subscribe(p, self._on_event) for p in ADMIN_PATTERNS]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        for sub_id in self._sub_ids:
            try:
                await self._bus.unsubscribe(sub_id)
            except Exception:                       # the bus may already be shut down
                pass
        self._sub_ids = []

    async def _on_event(self, event: Any) -> None:
        item = StreamedEvent(event=str(getattr(event.event_type, "value", event.event_type)),
                             data=dict(event.data or {}))
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def next_or_none(self, timeout: float) -> Optional[StreamedEvent]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


async def admin_sse(
    stream: AdminEventStream,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    yield ": admin events connected\n\n"
    event_id = 0
    while True:
        item = await stream.next_or_none(heartbeat_seconds)
        if await is_disconnected():
            logger.debug("admin SSE client went away")
            return
        if item is None:
            yield ": keep-alive\n\n"
            continue
        event_id += 1
        yield item.sse_frame(event_id)
