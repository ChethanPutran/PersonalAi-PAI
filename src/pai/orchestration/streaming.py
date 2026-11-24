"""Streaming of the assistant's reply — shared by text chat (SSE / WS) and voice.

The orchestrator pushes SAFE text (already passed through the StreamingSanitizer) into a
``ReplySink`` as it is generated, and also publishes best-effort ``chat.delta`` events on
the event bus for clients that only listen there. A sink is lossless and ordered; the bus
is not (it may drop under load), so each event carries ``seq`` for gap detection.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class ReplySink(Protocol):
    async def on_delta(self, text: str) -> None:
        """A piece of the reply that is safe to show/speak. Never raises into the caller's
        generation loop for ordinary failures — implementations should be cheap."""

    async def on_done(self, text: str) -> None:
        """The complete (sanitized) reply. Called once, after the last delta."""


class ChatDeltaEvent(BaseModel):
    session_id: Optional[str] = None
    request_id: str
    seq: int
    delta: str


class ChatReplyDoneEvent(BaseModel):
    session_id: Optional[str] = None
    request_id: str
    text: str


class ReplyItem(BaseModel):
    """What a QueueReplySink yields."""
    kind: str                    # "delta" | "done"
    text: str = ""


class QueueReplySink:
    """Lossless, ordered hand-off to a consumer (e.g. an SSE response). Unbounded on
    purpose: the producer is an LLM (tens of tokens/second), the consumer a socket."""

    def __init__(self) -> None:
        self._queue: "asyncio.Queue[Optional[ReplyItem]]" = asyncio.Queue()

    async def on_delta(self, text: str) -> None:
        if text:
            self._queue.put_nowait(ReplyItem(kind="delta", text=text))

    async def on_done(self, text: str) -> None:
        self._queue.put_nowait(ReplyItem(kind="done", text=text))

    def close(self) -> None:
        self._queue.put_nowait(None)

    async def get(self) -> Optional[ReplyItem]:
        return await self._queue.get()
