"""A screen tree reaches the agent as a compact element list, never as raw JSON with a base64 screenshot."""
import json

import pytest

from pai.agent.computer import find_observation, format_observation
from pai.agent.tools import CapabilityTool, ToolContext


def node(id, role, name="", **kw):
    return {"id": id, "role": role, "name": name, "text": "", "bounds": [10, 20, 300, 40], "enabled": True,
            "focused": False, "checked": None, "clickable": False, "scrollable": False, "editable": False,
            "children": [], **kw}


OBS = {"app": {"id": "com.android.settings", "name": "Settings", "title": "Settings"},
       "screen": {"width": 1080, "height": 2400}, "observation_id": "obs7", "truncated": False,
       "screenshot": {"mime": "image/png", "base64": "A" * 200_000, "width": 1080, "height": 2400},
       "tree": [node("1", "window", children=[
           node("2", "other", children=[node("3", "text", "Connected devices"),
                                        node("4", "switch", "Bluetooth", checked=False, clickable=True),
                                        node("5", "button", "Pair new device", clickable=True, enabled=False)]),
           node("6", "input", "Search settings", editable=True, focused=True)])]}


def test_the_list_is_short_readable_and_carries_ids_roles_states_and_positions():
    out = format_observation({"status": "completed", "result": OBS})
    assert "APP Settings" in out and "screen 1080x2400" in out and "observation obs7" in out
    assert "[4] switch 'Bluetooth' (off, clickable) @10,20 300x40" in out
    assert "[5] button 'Pair new device' (disabled, clickable)" in out
    assert "[6] input 'Search settings' (focused, editable)" in out
    assert "AAAA" not in out and "screenshot was captured" in out          # the image never reaches the prompt
    assert "[1]" not in out and "[2]" not in out                           # empty containers are noise
    assert len(out) < len(json.dumps(OBS)) / 50


def test_a_huge_screen_is_cut_to_the_budget_and_says_so():
    big = dict(OBS, screenshot=None, tree=[node(str(i), "button", f"Item number {i}", clickable=True) for i in range(500)])
    out = format_observation(big, budget=1500)
    assert len(out) <= 1500 + 200 and "more elements not shown" in out
    assert "[0] button 'Item number 0'" in out


def test_a_screen_without_a_tree_tells_the_agent_what_to_do_instead():
    out = format_observation({"app": {"name": "Game"}, "screen": {"width": 1, "height": 1}, "tree": None,
                              "screenshot": {"base64": "x"}})
    assert "no UI tree" in out


def test_anything_that_is_not_an_observation_is_untouched():
    other = {"status": "completed", "result": {"ok": True}}
    assert format_observation(other) is other and find_observation("text") is None


class Source:
    def __init__(self, out): self.out = out

    async def execute(self, **kw): return self.out


CTX = ToolContext(user_id="u", agent_id="a", session_id="s", step=1, call_id="c", device_id=None) \
    if "call_id" in ToolContext.__dataclass_fields__ else None


@pytest.mark.asyncio
async def test_only_computer_observe_results_are_reformatted():
    ctx = ToolContext(**{k: (None if k in ("device_id", "session_id") else "x") if k != "step" else 1
                         for k in ToolContext.__dataclass_fields__})
    observe = CapabilityTool(Source({"status": "completed", "result": OBS}), "computer.observe", "d")
    assert "APP Settings" in await observe.run({}, ctx)
    click = CapabilityTool(Source({"status": "completed", "result": {"ok": True}, "device_id": "d1"}), "computer.click", "d")
    assert await click.run({}, ctx) == {"status": "completed", "result": {"ok": True}, "device_id": "d1"}
