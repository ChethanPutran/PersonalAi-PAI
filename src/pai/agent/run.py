"""One agent run: its state, live inbox, event log and lifecycle transitions.

All methods run on the event loop (no threads). The ReAct loop drives a run; the runtime and
the API talk to it through ``post_user_message`` / ``pause`` / ``resume``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from pai.agent.errors import AgentLimitError, AgentStateError
from pai.agent.events import EventLog
from pai.agent.models import AgentEvent, AgentEventKind, AgentInfo, AgentSpec, AgentState, utcnow
from pai.agent.tools import ToolRegistry

MAX_USER_MESSAGE = 4000
MAX_UNREAD_MESSAGES = 50

EventHook = Callable[["AgentRun", AgentEvent], Awaitable[None]]


class AgentRun:
    def __init__(self, *, id: str, user_id: str, session_id: Optional[str], spec: AgentSpec,
                 tools: ToolRegistry, hook: Optional[EventHook] = None) -> None:
        self.id = id
        self.user_id = user_id
        self.session_id = session_id
        self.spec = spec
        self.tools = tools
        self.name = spec.name or (spec.goal[:60] + ("…" if len(spec.goal) > 60 else ""))
        self.state = AgentState.PENDING
        self.created_at: datetime = utcnow()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.steps = 0
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.pending_question: Optional[str] = None
        self.log = EventLog()
        self.task: Optional[asyncio.Task] = None
        self._inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_UNREAD_MESSAGES)
        self._paused = False
        self._resume = asyncio.Event()
        self._resume.set()
        self._hook = hook

    # ── snapshots ────────────────────────────────────────────────────────
    def info(self) -> AgentInfo:
        return AgentInfo(
            id=self.id, name=self.name, goal=self.spec.goal, state=self.state, tools=self.tools.names(),
            steps=self.steps, created_at=self.created_at, started_at=self.started_at,
            finished_at=self.finished_at, result=self.result, error=self.error,
            pending_question=self.pending_question, session_id=self.session_id,
            last_seq=self.log.last_seq)

    # ── events / state ───────────────────────────────────────────────────
    async def emit(self, kind: AgentEventKind, **data: Any) -> AgentEvent:
        event = await self.log.append(kind, data)
        if self._hook is not None:
            try:
                await self._hook(self, event)
            except Exception as exc:                 # observers must never break a run
                logger.warning("agent {} event hook failed: {}", self.id, exc)
        return event

    async def set_state(self, state: AgentState, **data: Any) -> None:
        if self.state.terminal or state == self.state:
            return
        self.state = state
        if state is AgentState.RUNNING and self.started_at is None:
            self.started_at = utcnow()
        if state.terminal:
            self.finished_at = utcnow()
            self.pending_question = None
        await self.emit(AgentEventKind.STATE, state=state.value, **data)
        if state.terminal:
            await self.log.close()

    async def complete(self, result: str) -> None:
        if self.state.terminal:
            return
        self.result = result
        await self.emit(AgentEventKind.FINAL, result=result)
        await self.set_state(AgentState.COMPLETED)

    async def fail(self, reason: str) -> None:
        if self.state.terminal:
            return
        self.error = reason
        await self.emit(AgentEventKind.ERROR, fatal=True, error=reason)
        await self.set_state(AgentState.FAILED, error=reason)

    async def mark_cancelled(self) -> None:
        await self.set_state(AgentState.CANCELLED)

    # ── talking to the agent ─────────────────────────────────────────────
    async def post_user_message(self, text: str) -> None:
        if self.state.terminal:
            raise AgentStateError(f"agent is {self.state.value}; it can no longer receive messages")
        text = (text or "").strip()
        if not text:
            raise ValueError("message is empty")
        text = text[:MAX_USER_MESSAGE]
        try:
            self._inbox.put_nowait(text)
        except asyncio.QueueFull:
            raise AgentLimitError("the agent has too many unread messages; wait for it to catch up") from None
        await self.emit(AgentEventKind.USER, text=text)

    def drain_inbox(self) -> List[str]:
        out: List[str] = []
        while True:
            try:
                out.append(self._inbox.get_nowait())
            except asyncio.QueueEmpty:
                return out

    async def ask(self, question: str, options: Optional[List[str]], timeout: float) -> Optional[str]:
        """Block until the user answers. Returns None on timeout.

        Messages the user sent while the model was still deciding to ask are returned at once
        (they are the freshest thing the user said); otherwise the next message is the answer.
        """
        queued = self.drain_inbox()
        if queued:
            return "\n".join(queued)
        self.pending_question = question
        await self.set_state(AgentState.WAITING_USER)
        await self.emit(AgentEventKind.QUESTION, question=question, options=options or [])
        try:
            return await asyncio.wait_for(self._inbox.get(), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending_question = None
            if not self.state.terminal:
                self.state = AgentState.PAUSED if self._paused else AgentState.RUNNING
                await self.emit(AgentEventKind.STATE, state=self.state.value)

    # ── pause / resume (cooperative: takes effect at the next step) ──────
    @property
    def paused(self) -> bool:
        return self._paused

    async def pause(self) -> None:
        if self.state.terminal:
            raise AgentStateError(f"agent is {self.state.value}")
        if self._paused:
            return
        self._paused = True
        self._resume.clear()
        if self.state is AgentState.RUNNING:
            await self.set_state(AgentState.PAUSED)

    async def resume(self) -> None:
        if self.state.terminal:
            raise AgentStateError(f"agent is {self.state.value}")
        if not self._paused:
            return
        self._paused = False
        self._resume.set()
        if self.state is AgentState.PAUSED:
            await self.set_state(AgentState.RUNNING)

    async def wait_if_paused(self) -> None:
        while self._paused:
            await self._resume.wait()
