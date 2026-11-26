"""Tools an agent can call.

A tool is a named, schema-described action. The agent never gets a tool the user does not
already have: every tool is one of the user's own capabilities, run through the same
orchestrator path (authorization, confirmation, audit, device selection) as any other request.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

import jsonschema

from pai.agent.computer import OBSERVE_CAPABILITY, format_observation
from pai.agent.errors import AgentSpawnError

#: Never offered to an agent: no agents spawning agents yet, and chat.reply is not an action.
DENY_PREFIXES = ("agent.", "chat.")
#: Names the loop itself handles; a capability can never shadow them.
CONTROL_TOOLS = ("finish", "ask_user", "say")

_CONTROL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "finish": {"type": "object", "properties": {"result": {"type": "string", "minLength": 1}}, "required": ["result"]},
    "ask_user": {"type": "object", "properties": {
        "question": {"type": "string", "minLength": 1},
        "options": {"type": "array", "items": {"type": "string"}}}, "required": ["question"]},
    "say": {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}}, "required": ["text"]},
}


def control_schema(name: str) -> Dict[str, Any]:
    return _CONTROL_SCHEMAS[name]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    agent_id: str
    session_id: Optional[str]
    step: int
    #: Unique per call. The orchestrator de-duplicates identical actions within one request id,
    #: so reusing an id would replay an old result instead of acting again.
    call_id: str
    device_id: Optional[str] = None


class Tool(Protocol):
    spec: ToolSpec

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any: ...


class CapabilitySource(Protocol):
    """Where an agent's tools come from (production: the orchestrator; tests: a fake)."""

    async def describe(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """capability id -> {"description": str, "parameters": JSON schema} for this user, live."""

    async def execute(self, *, user_id: str, capability: str, params: Dict[str, Any],
                      call_id: str, session_id: Optional[str], agent_id: str,
                      device_id: Optional[str]) -> Dict[str, Any]: ...


class CapabilityTool:
    """One user capability exposed as an agent tool."""

    def __init__(self, source: CapabilitySource, capability: str, description: str,
                 parameters: Optional[Dict[str, Any]] = None) -> None:
        self._source = source
        self.spec = ToolSpec(capability, description or capability,
                             parameters or {"type": "object", "properties": {}})

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        out = await self._source.execute(
            user_id=ctx.user_id, capability=self.spec.name, params=args, call_id=ctx.call_id,
            session_id=ctx.session_id, agent_id=ctx.agent_id, device_id=ctx.device_id)
        if self.spec.name == OBSERVE_CAPABILITY:       # a screen tree: compact text, never raw JSON + base64
            return format_observation(out)
        if isinstance(out, dict) and "result" in out:
            slim = {"status": out.get("status"), "result": out["result"]}
            if out.get("device_id"):
                slim["device_id"] = out["device_id"]
            return slim
        return out


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: Dict[str, Tool] = {}
        for t in tools:
            self.add(t)

    def add(self, tool: Tool) -> None:
        if tool.spec.name in CONTROL_TOOLS:
            raise ValueError(f"{tool.spec.name!r} is reserved for the agent loop")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def render_catalog(self) -> str:
        """Prompt text: one entry per tool with a compact argument signature."""
        lines = []
        for name in self.names():
            spec = self._tools[name].spec
            lines.append(f"- {name}: {spec.description.strip()}\n    args: {render_params(spec.parameters)}")
        return "\n".join(lines)


def render_params(schema: Dict[str, Any]) -> str:
    """{"lens": "front"|"back" (optional, default back), "outputPath": string}"""
    props = (schema or {}).get("properties") or {}
    if not props:
        return "{}"
    required = set((schema or {}).get("required") or [])
    parts = []
    for key, p in props.items():
        p = p or {}
        typ = "|".join(json.dumps(v) for v in p["enum"]) if p.get("enum") else str(p.get("type", "any"))
        extra = []
        if key in required:
            extra.append("required")
        if "default" in p and p["default"] is not None:
            extra.append(f"default {json.dumps(p['default'])}")
        desc = (p.get("description") or "").strip()
        if desc:
            extra.append(desc[:120])
        parts.append(f'"{key}": {typ}' + (f" ({'; '.join(extra)})" if extra else ""))
    return "{" + ", ".join(parts) + "}"


def resolve_tool_patterns(patterns: Sequence[str], available: Iterable[str]) -> List[str]:
    """Turn the requested tool names/globs into the exact capability ids to grant.

    Exact names must exist; globs ("browser.*") must match something. Denied prefixes are never
    granted, even by a glob that would match them. Raises AgentSpawnError with an actionable
    message otherwise.
    """
    pool = sorted(set(available))
    grantable = [c for c in pool if not c.startswith(DENY_PREFIXES)]
    chosen: set[str] = set()
    problems: List[str] = []
    for raw in patterns:
        p = (raw or "").strip()
        if not p:
            continue
        if any(ch in p for ch in "*?["):
            hits = [c for c in grantable if fnmatch.fnmatchcase(c, p)]
            if hits:
                chosen.update(hits)
            else:
                problems.append(f"{p!r} matches no capability the user has")
        elif p.startswith(DENY_PREFIXES):
            problems.append(f"{p!r} is not allowed for agents")
        elif p in grantable:
            chosen.add(p)
        else:
            problems.append(f"{p!r} is not an available capability")
    if problems:
        raise AgentSpawnError("; ".join(problems) + f". Available: {', '.join(grantable[:60]) or 'none'}")
    if not chosen:
        raise AgentSpawnError("no tools selected")
    return sorted(chosen)


def validate_args(schema: Dict[str, Any], args: Any) -> Optional[str]:
    """None if ``args`` satisfies the JSON schema, else a short readable reason."""
    if not isinstance(args, dict):
        return "args must be a JSON object"
    try:
        errors = sorted(jsonschema.Draft202012Validator(schema or {"type": "object"}).iter_errors(args),
                        key=lambda e: list(e.absolute_path))
    except Exception:                        # SchemaError, UnknownType, bad $ref...: a tool's own broken
        return None                          # schema must never block (or crash) the agent
    if not errors:
        return None
    return "; ".join(
        f"{'.'.join(str(x) for x in e.absolute_path) or 'args'}: {e.message}" for e in errors[:3])
