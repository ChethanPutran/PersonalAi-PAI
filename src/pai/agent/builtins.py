"""Built-in capabilities that let PAI itself start and talk to agents.

The planner can plan ``agent.start`` when a goal is open-ended and needs observe/act loops
(browse for a playlist, fill in a web form). Spawned agents cannot call ``agent.*`` themselves.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pai.agent.errors import AgentError
from pai.agent.models import AgentLimits, AgentSpec, AgentState
from pai.agent.runtime import AgentRuntime
from pai.orchestration.builtin_capabilities import BuiltinCapability


def _tools(value: Any) -> List[str]:
    if isinstance(value, str):                      # planners sometimes emit "a.b, c.*"
        value = [v for v in value.replace(",", " ").split() if v]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v) for v in value]


def build_agent_capabilities(runtime: AgentRuntime) -> list[BuiltinCapability]:
    async def _start(params: Dict[str, Any], *, user_id: str, task: Any = None, **_):
        goal = str(params.get("goal") or "").strip()
        if not goal:
            raise ValueError("agent.start needs a goal")
        limits: Dict[str, Any] = {}
        if params.get("max_steps") is not None:
            limits["max_steps"] = int(params["max_steps"])
        try:
            spec = AgentSpec(goal=goal, tools=_tools(params.get("tools")), name=params.get("name") or None,
                             device_id=params.get("device_id") or None, limits=AgentLimits(**limits))
        except Exception as exc:
            raise ValueError(f"invalid agent request: {exc}") from exc
        try:
            info = await runtime.spawn(user_id, spec, session_id=getattr(task, "session_id", None))
        except AgentError as exc:
            raise ValueError(str(exc)) from exc
        return {"agent_id": info.id, "name": info.name, "state": info.state.value, "tools": info.tools,
                "hint": "The agent works in the background. Follow it live in the app; send it a "
                        "message with agent.message, stop it with agent.cancel."}

    async def _message(params: Dict[str, Any], *, user_id: str, **_):
        try:
            await runtime.send(user_id, str(params["agent_id"]), str(params.get("text") or ""))
        except AgentError as exc:
            raise ValueError(str(exc)) from exc
        return {"delivered": True}

    async def _status(params: Dict[str, Any], *, user_id: str, **_):
        try:
            run = runtime.get(user_id, str(params["agent_id"]))
        except AgentError as exc:
            raise ValueError(str(exc)) from exc
        recent = [e.wire() for e in run.log.snapshot(limit=int(params.get("events", 10) or 10))]
        return {**run.info().model_dump(mode="json"), "recent_events": recent}

    async def _cancel(params: Dict[str, Any], *, user_id: str, **_):
        try:
            info = await runtime.cancel(user_id, str(params["agent_id"]))
        except AgentError as exc:
            raise ValueError(str(exc)) from exc
        return {"agent_id": info.id, "state": info.state.value}

    async def _list(params: Dict[str, Any], *, user_id: str, **_):
        state = AgentState(params["state"]) if params.get("state") else None
        return {"agents": [i.model_dump(mode="json", include={"id", "name", "goal", "state", "steps", "created_at"})
                           for i in runtime.list(user_id, state)]}

    agent_id = {"agent_id": {"type": "string"}}
    return [
        BuiltinCapability(
            id="agent.start",
            description=(
                "Start a background agent that works step by step toward a goal (looking at the "
                "result of each action before choosing the next) using only the capabilities listed "
                "in `tools`. Use it for open-ended multi-step tasks that need observing and acting "
                "in a loop, e.g. browsing a site for a specific playlist or filling in a web form. "
                "The user can talk to the agent while it works. `tools` are capability ids or "
                "prefix globs like 'browser.*' that the user actually has."),
            parameters={"type": "object", "properties": {
                "goal": {"type": "string", "description": "What the agent must achieve, in plain words."},
                "tools": {"type": "array", "items": {"type": "string"},
                          "description": "Capability ids or prefix globs the agent may use."},
                "name": {"type": "string"},
                "max_steps": {"type": "integer", "description": "Step budget (default 25, max 100)."},
                "device_id": {"type": "string", "description": "Preferred device for the agent's actions."},
            }, "required": ["goal", "tools"]},
            handler=_start),
        BuiltinCapability(
            id="agent.message", description="Send a message to a running agent (answer its question or change its course).",
            parameters={"type": "object", "properties": {**agent_id, "text": {"type": "string"}},
                        "required": ["agent_id", "text"]}, handler=_message),
        BuiltinCapability(
            id="agent.status", description="Show an agent's state, progress and most recent events.",
            parameters={"type": "object", "properties": {**agent_id, "events": {"type": "integer", "default": 10}},
                        "required": ["agent_id"]}, handler=_status),
        BuiltinCapability(
            id="agent.cancel", description="Stop a running agent.",
            parameters={"type": "object", "properties": agent_id, "required": ["agent_id"]}, handler=_cancel),
        BuiltinCapability(
            id="agent.list", description="List the user's agents (running and recently finished).",
            parameters={"type": "object", "properties": {"state": {"type": "string"}}}, handler=_list),
    ]
