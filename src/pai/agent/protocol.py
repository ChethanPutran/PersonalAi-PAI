"""The agent <-> model wire format: one JSON object per reply.

The LLM layer has no native tool calling, so the contract is plain JSON that works with every
provider:

    {"thought": "why", "action": {"tool": "<name>", "args": {...}}}

Tool results come back to the model inside an explicit boundary marking them as DATA.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict

THOUGHT_MAX = 2000
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


class ProtocolError(ValueError):
    """The model's reply is not a valid action."""


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: Dict[str, Any]
    thought: str


def extract_json_object(text: str) -> str:
    t = (text or "").strip()
    fenced = _FENCE.search(t)
    if fenced:
        t = fenced.group(1).strip()
    start = t.find("{")
    if start < 0:
        raise ProtocolError("no JSON object found in the reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    raise ProtocolError("the JSON object is not closed (reply cut off?)")


def parse_reply(text: str) -> ToolCall:
    try:
        obj = json.loads(extract_json_object(text))
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("the reply must be a JSON object")
    action = obj.get("action")
    if action is None and "tool" in obj:           # tolerate a missing wrapper
        action = {"tool": obj["tool"], "args": obj.get("args", {})}
    if not isinstance(action, dict):
        raise ProtocolError('missing "action": {"tool": ..., "args": {...}}')
    tool = action.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ProtocolError('"action.tool" must be a non-empty string')
    args = action.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError('"action.args" must be a JSON object')
    thought = obj.get("thought", "")
    return ToolCall(tool=tool.strip(), args=args, thought=str(thought)[:THOUGHT_MAX])


def escape_boundaries(text: str) -> str:
    """Neutralise our <<<BEGIN/END markers inside untrusted text so it cannot fake a boundary."""
    return (text or "").replace("<<<", "<​<​<")


def wrap_observation(tool: str, body: str) -> str:
    return f"<<<BEGIN_TOOL_RESULT tool={tool}>>>\n{escape_boundaries(body)}\n<<<END_TOOL_RESULT>>>"


def canonical_reply(call: ToolCall) -> str:
    """What goes back into the history as the assistant's turn (normalised, bounded)."""
    return json.dumps({"thought": call.thought[:500], "action": {"tool": call.tool, "args": call.args}},
                      ensure_ascii=False, default=str)
