"""Computer control from the agent's side: turning a screen observation into something a model can use.

The device plugins (``computer_control`` on Linux and Android, see docs/computer_control.md) return the UI as a
JSON tree, optionally with a base64 screenshot. Dropped into a prompt as is, that is tens of thousands of tokens
of boilerplate (and megabytes of base64) per step, slow and expensive, and it pushes the goal out of the context.
``format_observation`` keeps what the agent needs to choose the next action: the app, the screen size and a
reading-order list of the elements with their ids, roles, labels, states and positions, within a fixed budget.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

OBSERVE_CAPABILITY = "computer.observe"
#: Characters of rendered UI that fit the agent's observation limit (4000 by default) with headroom for the framing.
DEFAULT_BUDGET = 3300
_TEXT_MAX = 70
_BOILERPLATE_ROLES = {"other", "window", "scroll", "list", "image"}


_STRUCTURED_FIELDS = ("app", "screen", "tree", "screenshot", "target", "missing", "features", "apps")


def _decode(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Native (C++) plugins return flat string maps: structured fields arrive as JSON text. Decode them."""
    out = dict(obs)
    for key in _STRUCTURED_FIELDS:
        value = out.get(key)
        if isinstance(value, str) and value[:1] in ("{", "["):
            try:
                out[key] = json.loads(value)
            except ValueError:
                pass
    for key in ("truncated",):
        if isinstance(out.get(key), str):
            out[key] = out[key].strip().lower() == "true"
    return out


def find_observation(result: Any) -> Optional[Dict[str, Any]]:
    """The observe payload inside whatever the capability call wrapped around it ({status, result: {...}})."""
    node = result
    for _ in range(4):
        if not isinstance(node, dict):
            return None
        if "tree" in node or ("screen" in node and "app" in node):
            return _decode(node)
        node = node.get("result") or node.get("data")
    return None


def _walk(nodes: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(nodes, dict):
        nodes = [nodes]
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        yield n
        yield from _walk(n.get("children"))


def _label(n: Dict[str, Any]) -> str:
    text = str(n.get("name") or n.get("text") or "").replace("\n", " ").strip()
    return text if len(text) <= _TEXT_MAX else text[:_TEXT_MAX - 1] + "…"


def _useful(n: Dict[str, Any]) -> bool:
    """Worth a line: something to act on, or something readable."""
    if n.get("clickable") or n.get("editable") or n.get("scrollable") or n.get("checked") is not None:
        return True
    if _label(n):
        return True
    return n.get("role") not in _BOILERPLATE_ROLES


def _row(n: Dict[str, Any]) -> str:
    flags: List[str] = []
    if n.get("checked") is True:
        flags.append("on")
    elif n.get("checked") is False:
        flags.append("off")
    for key, word in (("focused", "focused"), ("editable", "editable"), ("scrollable", "scrollable")):
        if n.get(key):
            flags.append(word)
    if n.get("enabled") is False:
        flags.append("disabled")
    if n.get("clickable") and "editable" not in flags:
        flags.append("clickable")
    b = n.get("bounds") or []
    where = f" @{b[0]},{b[1]} {b[2]}x{b[3]}" if len(b) == 4 else ""
    label = _label(n)
    return f"[{n.get('id', '?')}] {n.get('role', 'other')}{f' {label!r}' if label else ''}" \
           f"{' (' + ', '.join(flags) + ')' if flags else ''}{where}"


def format_observation(result: Any, *, budget: int = DEFAULT_BUDGET) -> Any:
    """Compact text for a ``computer.observe`` result; anything else is returned untouched."""
    obs = find_observation(result)
    if obs is None:
        return result
    app = obs.get("app") or {}
    screen = obs.get("screen") or {}
    head = [f"APP {app.get('name') or app.get('id') or '?'}"
            + (f" - {app['title']}" if app.get("title") else "")
            + f" | screen {screen.get('width', '?')}x{screen.get('height', '?')}"
            + f" | observation {obs.get('observation_id', '?')}"]
    if obs.get("screenshot"):
        head.append("(a screenshot was captured; it is not shown here, rely on the element list)")
    rows = [_row(n) for n in _walk(obs.get("tree")) if _useful(n)]
    if obs.get("tree") is None:
        head.append("(no UI tree available on this screen: use coordinates from a screenshot, or ask the user)")
    out, used, shown = list(head), sum(len(h) + 1 for h in head), 0
    for r in rows:
        if used + len(r) + 1 > budget:
            break
        out.append(r)
        used += len(r) + 1
        shown += 1
    omitted = len(rows) - shown
    if omitted > 0 or obs.get("truncated"):
        out.append(f"[{max(omitted, 0)} more elements not shown: scroll, or act on what is shown and observe again]")
    return "\n".join(out)
