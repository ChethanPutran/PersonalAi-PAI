"""Fakes for the agent tests: a scripted LLM and a recording capability source."""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from pai.agent.runtime import AgentRuntime, RuntimeLimits
from pai.config.models.prompts import PromptRegistryConfig
from pai.llm.models import LLMMessage
from pai.prompts.registry import PromptRegistry


def act(tool: str, thought: str = "", **args: Any) -> str:
    return json.dumps({"thought": thought, "action": {"tool": tool, "args": args}})


def fin(result: str = "done") -> str:
    return act("finish", result=result)


class ScriptedLLM:
    """Replies come from a script: str (returned), Exception (raised), or callable(messages)."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: List[List[LLMMessage]] = []

    async def complete(self, messages: List[LLMMessage], *, temperature: float, max_tokens: int) -> str:
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("the LLM was called more times than the test scripted")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(messages)
            if inspect.isawaitable(r):
                r = await r
        return r

    def last_user_text(self) -> str:
        return "\n".join(m.content for m in self.calls[-1] if m.role == "user")


CAPS: Dict[str, Dict[str, Any]] = {
    "browser.open": {"description": "Open a web page.", "parameters": {
        "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    "browser.read": {"description": "Read the page text.", "parameters": {"type": "object", "properties": {}}},
    "files.read": {"description": "Read a file.", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    "agent.start": {"description": "spawn", "parameters": {"type": "object", "properties": {}}},
    "chat.reply": {"description": "chat", "parameters": {"type": "object", "properties": {}}},
}


class FakeSource:
    def __init__(self, handler: Optional[Callable[..., Any]] = None, caps: Optional[dict] = None) -> None:
        self.caps = caps if caps is not None else dict(CAPS)
        self.handler = handler or (lambda capability, params: {"ok": True, "echo": params})
        self.calls: List[Dict[str, Any]] = []

    async def describe(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        return self.caps

    async def execute(self, *, user_id, capability, params, call_id, session_id, agent_id, device_id):
        self.calls.append(dict(user_id=user_id, capability=capability, params=params, call_id=call_id,
                               session_id=session_id, agent_id=agent_id, device_id=device_id))
        out = self.handler(capability, params)
        if inspect.isawaitable(out):
            out = await out
        return {"status": "completed", "result": out, "device_id": device_id or "dev-1"}


async def _no_sleep(_: float) -> None:
    return None


def make_runtime(llm: ScriptedLLM, source: Optional[FakeSource] = None, limits: Optional[RuntimeLimits] = None,
                 **kw: Any) -> AgentRuntime:
    registry = PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True))
    return AgentRuntime(capability_source=source or FakeSource(), llm=llm,
                        prompt=lambda: registry.get("agent.react"),
                        limits=limits or RuntimeLimits(), sleep=_no_sleep, **kw)


async def finished(runtime: AgentRuntime, user: str, agent_id: str, timeout: float = 5.0):
    run = runtime.get(user, agent_id)
    await asyncio.wait_for(asyncio.shield(run.task), timeout)
    return run


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    end = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)
