"""AgentRuntime: starts agents on demand and manages them while they run.

Responsibilities: spawn (resolve the tool allowlist, enforce limits), supervise (one asyncio task
per agent, always ending in a terminal state), route the user's live messages, pause / resume /
cancel, expose events for replay, and clean up. Everything is scoped to the owning user.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from loguru import logger

from pai.agent.errors import AgentLimitError, AgentNotFound
from pai.agent.events import EventLog
from pai.agent.loop import Filter, LLMClient, PromptTemplate, ReactLoop
from pai.agent.models import AgentEvent, AgentEventKind, AgentInfo, AgentSpec, AgentState
from pai.agent.run import AgentRun
from pai.agent.tools import CapabilitySource, CapabilityTool, ToolRegistry, resolve_tool_patterns
from pai.common.manager import Manager


@dataclass(frozen=True)
class RuntimeLimits:
    max_per_user: int = 3                 # concurrently running agents per user
    max_total: int = 20
    retain_finished: int = 100            # finished runs kept for inspection / replay
    finished_ttl_seconds: float = 3600.0
    shutdown_grace_seconds: float = 5.0


#: Event kinds that are also published on the event bus (low volume, no tool arguments/results).
_BUS_KINDS = {AgentEventKind.STATE, AgentEventKind.MESSAGE, AgentEventKind.QUESTION, AgentEventKind.FINAL}


class AgentRuntime(Manager):
    name = "agent_runtime"

    def __init__(self, *, capability_source: CapabilitySource, llm: LLMClient,
                 prompt: Callable[[], PromptTemplate], event_bus: Any = None,
                 limits: RuntimeLimits = RuntimeLimits(),
                 observation_filter: Optional[Filter] = None, response_filter: Optional[Filter] = None,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        super().__init__(event_bus=event_bus)
        self._source = capability_source
        self._limits = limits
        extra: Dict[str, Any] = {}
        if observation_filter:
            extra["observation_filter"] = observation_filter
        if response_filter:
            extra["response_filter"] = response_filter
        self._loop = ReactLoop(llm=llm, prompt=prompt, sleep=sleep, **extra)
        self._runs: Dict[str, AgentRun] = {}

    # ── lifecycle ────────────────────────────────────────────────────────
    async def _on_shutdown(self) -> None:
        tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self._limits.shutdown_grace_seconds)
        logger.info("agent runtime: stopped {} running agent(s)", len(tasks))

    # ── spawning ─────────────────────────────────────────────────────────
    async def spawn(self, user_id: str, spec: AgentSpec, *, session_id: Optional[str] = None) -> AgentInfo:
        await self._ensure_initialized()
        # The user's tools as of now; the agent gets only the requested subset of these.
        available = await self._source.describe(user_id)
        resolved = resolve_tool_patterns(spec.tools, available.keys())
        registry = ToolRegistry(
            CapabilityTool(self._source, cap, available[cap].get("description", ""), available[cap].get("parameters"))
            for cap in resolved)

        # No await between the limit check and the insert: concurrent spawns cannot overshoot.
        self._reap()
        live = [r for r in self._runs.values() if not r.state.terminal]
        if sum(1 for r in live if r.user_id == user_id) >= self._limits.max_per_user:
            raise AgentLimitError(
                f"you already have {self._limits.max_per_user} agents running; cancel one or wait for it to finish")
        if len(live) >= self._limits.max_total:
            raise AgentLimitError("the server is running as many agents as it allows; try again shortly")

        run = AgentRun(id=f"agt_{uuid.uuid4().hex[:12]}", user_id=user_id, session_id=session_id,
                       spec=spec, tools=registry, hook=self._on_event)
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._supervise(run), name=f"agent:{run.id}")
        logger.info("agent {} spawned for user {} with tools {}", run.id, user_id, resolved)
        return run.info()

    async def _supervise(self, run: AgentRun) -> None:
        try:
            await self._loop.run(run)
        except asyncio.CancelledError:
            await run.mark_cancelled()
        except Exception as exc:
            logger.exception("agent {} crashed", run.id)
            await run.fail(f"internal_error: {type(exc).__name__}")
        finally:
            if not run.state.terminal:
                await run.fail("ended unexpectedly")

    async def _on_event(self, run: AgentRun, event: AgentEvent) -> None:
        if event.kind in _BUS_KINDS:
            self._emit(f"agent.{event.kind.value}", {
                "agent_id": run.id, "user_id": run.user_id, "session_id": run.session_id,
                "seq": event.seq, **{k: v for k, v in event.data.items() if k != "args"}})

    # ── access (always scoped to the owner) ──────────────────────────────
    def _own(self, user_id: str, agent_id: str) -> AgentRun:
        run = self._runs.get(agent_id)
        if run is None or run.user_id != user_id:       # same answer for "missing" and "not yours"
            raise AgentNotFound(f"agent {agent_id!r} not found")
        return run

    def get(self, user_id: str, agent_id: str) -> AgentRun:
        return self._own(user_id, agent_id)

    def info(self, user_id: str, agent_id: str) -> AgentInfo:
        return self._own(user_id, agent_id).info()

    def list(self, user_id: str, state: Optional[AgentState] = None) -> List[AgentInfo]:
        self._reap()
        runs = [r for r in self._runs.values() if r.user_id == user_id and (state is None or r.state is state)]
        return [r.info() for r in sorted(runs, key=lambda r: r.created_at, reverse=True)]

    def events(self, user_id: str, agent_id: str, after: int = 0, limit: Optional[int] = None) -> List[AgentEvent]:
        return self._own(user_id, agent_id).log.snapshot(after, limit)

    def log(self, user_id: str, agent_id: str) -> EventLog:
        return self._own(user_id, agent_id).log

    async def stream(self, user_id: str, agent_id: str, after: int = 0) -> AsyncIterator[AgentEvent]:
        async for event in self._own(user_id, agent_id).log.stream(after):
            yield event

    # ── talking to / steering an agent ───────────────────────────────────
    async def send(self, user_id: str, agent_id: str, text: str) -> None:
        await self._own(user_id, agent_id).post_user_message(text)

    async def pause(self, user_id: str, agent_id: str) -> AgentInfo:
        run = self._own(user_id, agent_id)
        await run.pause()
        return run.info()

    async def resume(self, user_id: str, agent_id: str) -> AgentInfo:
        run = self._own(user_id, agent_id)
        await run.resume()
        return run.info()

    async def cancel(self, user_id: str, agent_id: str) -> AgentInfo:
        run = self._own(user_id, agent_id)
        if run.task is not None and not run.task.done():
            run.task.cancel()
            await asyncio.wait({run.task}, timeout=self._limits.shutdown_grace_seconds)
        if not run.state.terminal:
            await run.mark_cancelled()
        return run.info()

    # ── housekeeping ─────────────────────────────────────────────────────
    def _reap(self) -> None:
        now = time.time()
        finished = sorted((r for r in self._runs.values() if r.state.terminal),
                          key=lambda r: r.finished_at or r.created_at)
        for r in finished:
            age = now - (r.finished_at or r.created_at).timestamp()
            if age > self._limits.finished_ttl_seconds or len(finished) > self._limits.retain_finished:
                self._runs.pop(r.id, None)
                finished = [x for x in finished if x is not r]
