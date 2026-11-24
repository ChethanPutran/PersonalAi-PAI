"""The ReAct loop: think -> act -> observe, until the goal is met or a budget runs out.

One reply per step, as a JSON action. Control tools (``finish``, ``ask_user``, ``say``) are handled
here; every other tool is one of the user's capabilities, run through the orchestrator.

Safety properties kept by this loop:
  * tool output is DATA: it is sanitised, size-bounded and wrapped in a boundary; only the goal
    and live user messages are instructions;
  * every tool call gets a unique call id (so a repeated action really runs again);
  * a denied or failed action comes back as an observation the model must react to;
  * hard budgets: steps, wall-clock time, per-tool timeout, wait-for-user timeout.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Protocol

from loguru import logger

from pai.agent.models import AgentEventKind, AgentState
from pai.agent.protocol import (ProtocolError, ToolCall, canonical_reply, escape_boundaries,
                                parse_reply, wrap_observation)
from pai.agent.run import AgentRun
from pai.agent.tools import CONTROL_TOOLS, ToolContext, control_schema, validate_args
from pai.llm.models import LLMMessage

OBSERVATION_PREVIEW = 500
HISTORY_CHAR_BUDGET = 150_000


class LLMClient(Protocol):
    async def complete(self, messages: List[LLMMessage], *, temperature: float, max_tokens: int) -> str: ...


class PromptTemplate(Protocol):
    def render(self, **variables: Any) -> str: ...


Filter = Callable[[str], str]


@dataclass
class _Turn:
    assistant: str            # the model's (normalised) reply; "" for a user-message turn
    feedback: str             # what the model is told next (observation / error / user message)
    collapsed: str            # stand-in once this turn is old
    keep: bool = False        # never collapse (user messages)


def _identity(text: str) -> str:
    return text


class ReactLoop:
    def __init__(self, *, llm: LLMClient, prompt: Callable[[], PromptTemplate],
                 observation_filter: Filter = _identity, response_filter: Filter = _identity,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._llm = llm
        self._prompt = prompt
        self._obs_filter = observation_filter
        self._resp_filter = response_filter
        self._sleep = sleep

    # ── public ───────────────────────────────────────────────────────────
    async def run(self, run: AgentRun) -> None:
        lim = run.spec.limits
        deadline = time.monotonic() + lim.max_runtime_seconds
        system = self._prompt().render(
            # The default name is cut from the goal, so it gets the same treatment as the goal itself.
            agent_name=escape_boundaries(" ".join(run.name.split()))[:80],
            goal=escape_boundaries(run.spec.goal),
            tools_block=run.tools.render_catalog(), max_steps=lim.max_steps)
        history: List[_Turn] = []
        format_errors = 0
        await run.set_state(AgentState.RUNNING)

        for step in range(1, lim.max_steps + 1):
            run.steps = step
            await run.wait_if_paused()
            if time.monotonic() >= deadline:
                return await run.fail("max_runtime: the time budget ran out")

            for text in run.drain_inbox():
                history.append(_Turn("", f"USER MESSAGE (live, from the user — follow it): {escape_boundaries(text)}",
                                     "", keep=True))

            reply = await self._ask_model(run, system, history, deadline)
            if reply is None:
                return                                   # run already failed
            try:
                call = parse_reply(reply)
            except ProtocolError as exc:
                format_errors += 1
                await run.emit(AgentEventKind.ERROR, category="format", error=str(exc))
                if format_errors >= lim.max_format_errors:
                    return await run.fail(f"format: {format_errors} unusable replies in a row ({exc})")
                history.append(_Turn(reply[:500], f"FORMAT ERROR: {exc}. Reply with exactly ONE JSON object: "
                                     '{"thought": "...", "action": {"tool": "<name>", "args": {...}}}',
                                     "[an earlier reply was not valid JSON]"))
                continue
            format_errors = 0

            # Empty text is skipped, not filtered: PromptGuard answers "" with a canned
            # "Okay — I've handled that" reply, which would show up as a made-up thought.
            thought = self._resp_filter(call.thought) if call.thought.strip() else ""
            if thought:
                await run.emit(AgentEventKind.THOUGHT, step=step, text=thought)
            await run.emit(AgentEventKind.ACTION, step=step, tool=call.tool, args=call.args)

            if call.tool in CONTROL_TOOLS:
                done = await self._control(run, call, history, step)
                if done:
                    return
                continue

            feedback, collapsed = await self._call_tool(run, call, step)
            history.append(_Turn(canonical_reply(call), feedback, collapsed))

        await run.fail(f"max_steps: used all {lim.max_steps} steps without finishing")

    # ── model I/O ────────────────────────────────────────────────────────
    async def _ask_model(self, run: AgentRun, system: str, history: List[_Turn],
                         deadline: float) -> Optional[str]:
        lim = run.spec.limits
        messages = self._messages(system, history, lim.history_full_observations)
        last: Optional[Exception] = None
        for attempt in range(lim.llm_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await run.fail("max_runtime: the time budget ran out")
                return None
            try:
                return await asyncio.wait_for(
                    self._llm.complete(messages, temperature=lim.temperature, max_tokens=lim.max_tokens),
                    timeout=remaining)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                await run.fail("max_runtime: the time budget ran out")
                return None
            except Exception as exc:
                last = exc
                logger.warning("agent {} LLM call failed (attempt {}): {}", run.id, attempt + 1, exc)
                await run.emit(AgentEventKind.ERROR, category="llm", error=f"{type(exc).__name__}: {exc}"[:300])
                if attempt < lim.llm_retries:
                    await self._sleep(min(2 ** attempt, 5))
        await run.fail(f"llm_error: {type(last).__name__}: {last}"[:300])
        return None

    @staticmethod
    def _messages(system: str, history: List[_Turn], full_window: int) -> List[LLMMessage]:
        raw: List[LLMMessage] = [LLMMessage("system", system),
                                 LLMMessage("user", "Begin. Reply with your first JSON action.")]
        n = len(history)
        budget = HISTORY_CHAR_BUDGET
        for i in range(n - 1, -1, -1):                   # newest first, so the budget keeps the recent ones
            t = history[i]
            recent = i >= n - full_window
            fb = t.feedback if (recent or t.keep) and budget > 0 else (t.collapsed or "[earlier step omitted]")
            budget -= len(fb) + len(t.assistant)
            raw.insert(2, LLMMessage("user", fb))
            if t.assistant:
                raw.insert(2, LLMMessage("assistant", t.assistant))
        merged: List[LLMMessage] = []                    # providers differ on consecutive same-role turns
        for m in raw:
            if merged and merged[-1].role == m.role == "user":
                merged[-1] = LLMMessage("user", merged[-1].content + "\n\n" + m.content)
            else:
                merged.append(m)
        return merged

    # ── control tools ────────────────────────────────────────────────────
    async def _control(self, run: AgentRun, call: ToolCall, history: List[_Turn], step: int) -> bool:
        """Returns True when the run is over."""
        err = validate_args(control_schema(call.tool), call.args)
        if err:
            history.append(_Turn(canonical_reply(call), f"ERROR: invalid arguments for {call.tool}: {err}",
                                 f"[{call.tool}: invalid arguments]"))
            await run.emit(AgentEventKind.ERROR, category="args", tool=call.tool, error=err)
            return False

        if call.tool == "finish":
            await run.complete(self._resp_filter(str(call.args["result"])))
            return True

        if call.tool == "say":
            text = self._resp_filter(str(call.args["text"]))
            await run.emit(AgentEventKind.MESSAGE, text=text)
            history.append(_Turn(canonical_reply(call), "OK: the user was told.", "[told the user]"))
            return False

        # ask_user
        question = self._resp_filter(str(call.args["question"]))
        options = [self._resp_filter(str(o)) for o in call.args.get("options") or []][:10]
        answer = await run.ask(question, options, run.spec.limits.ask_timeout_seconds)
        if answer is None:
            await run.fail("user_timeout: no answer to the agent's question in time")
            return True
        history.append(_Turn(canonical_reply(call),
                             f"USER MESSAGE (answer to your question — follow it): {escape_boundaries(answer)}",
                             "", keep=True))
        return False

    # ── capability tools ─────────────────────────────────────────────────
    async def _call_tool(self, run: AgentRun, call: ToolCall, step: int) -> tuple[str, str]:
        """Run one tool; returns (what the model is told, short stand-in for later)."""
        lim = run.spec.limits
        tool = run.tools.get(call.tool)
        if tool is None:
            names = ", ".join(run.tools.names() + list(CONTROL_TOOLS))
            return await self._failed(run, call, f"unknown tool {call.tool!r}. Use one of: {names}")

        err = validate_args(tool.spec.parameters, call.args)
        if err:
            return await self._failed(run, call, f"invalid arguments: {err}")

        ctx = ToolContext(user_id=run.user_id, agent_id=run.id, session_id=run.session_id, step=step,
                          call_id=f"{run.id}:{step}:{uuid.uuid4().hex[:8]}", device_id=run.spec.device_id)
        try:
            result = await asyncio.wait_for(tool.run(call.args, ctx), timeout=lim.tool_timeout_seconds)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return await self._failed(
                run, call, f"the tool did not finish within {lim.tool_timeout_seconds:.0f}s "
                           "(it may still be running); do not assume it succeeded")
        except PermissionError as exc:
            return await self._failed(
                run, call, f"DENIED: {exc}. Do not retry this action or look for a way around it; "
                           "ask the user or finish and explain.")
        except Exception as exc:
            return await self._failed(run, call, f"{type(exc).__name__}: {exc}")

        body = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        truncated = len(body) > lim.observation_max_chars
        body = self._obs_filter(body[:lim.observation_max_chars]) + ("\n[output truncated]" if truncated else "")
        await run.emit(AgentEventKind.OBSERVATION, step=step, tool=call.tool, ok=True,
                       preview=body[:OBSERVATION_PREVIEW], truncated=truncated)
        return wrap_observation(call.tool, body), f"[earlier result of {call.tool}: ok]"

    async def _failed(self, run: AgentRun, call: ToolCall, reason: str) -> tuple[str, str]:
        reason = reason[:1000]
        await run.emit(AgentEventKind.OBSERVATION, step=run.steps, tool=call.tool, ok=False, preview=reason)
        return f"ERROR from {call.tool}: {escape_boundaries(reason)}", f"[earlier {call.tool} call failed]"

