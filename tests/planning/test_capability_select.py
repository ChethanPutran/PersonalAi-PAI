"""Capability retrieval: the planner is shown the capabilities that fit the request, or everything."""
import json
from types import SimpleNamespace

import pytest

import pai.planning.goal_decomposer as gd
from pai.config.models.prompts import PromptRegistryConfig
from pai.planning.capability_select import missing_from, restrict, select_capabilities
from pai.planning.goal_decomposer import GoalDecomposer
from pai.prompts.registry import PromptRegistry

P = {"type": "object", "properties": {}}


def cap(desc):
    return {"description": desc, "parameters": P}


CAPS = {
    "chat.reply": cap("Reply to the user"),
    "schedule.create": cap("Schedule something for later"),
    "schedule.list": cap("List schedules"),
    "task.list": cap("List tasks"),
    "shell.exec": cap("Run a shell command"),
    "camera.capture": cap("Take a photo with the camera"),
    "camera.record": cap("Record a video"),
    "files.list": cap("List directory contents"),
    "files.read": cap("Read a file"),
    "files.copy": cap("Copy a file"),
    "browser.navigate": cap("Open a web page"),
    "audio.volume": cap("Set the volume"),
    "flashlight.set": cap("Turn the flashlight on or off"),
    "location.get": cap("Where the device is"),
    "contacts.search": cap("Find a contact"),
    "clipboard.read": cap("Read the clipboard"),
}


def test_picks_the_matching_family_and_keeps_siblings_together():
    s = select_capabilities("take my photo", CAPS, min_total=5)
    assert s.confident
    assert {"camera.capture", "camera.record"} <= set(s.ids)       # siblings travel together
    assert "flashlight.set" not in s.ids and "location.get" not in s.ids
    # always offered
    assert {"chat.reply", "schedule.create", "schedule.list", "task.list", "shell.exec"} <= set(s.ids)


def test_synonyms_reach_capabilities_whose_text_lacks_the_word():
    s = select_capabilities("turn on the torch please", CAPS, min_total=5)
    assert s.confident and "flashlight.set" in s.ids


def test_doubt_means_everything():
    assert not select_capabilities("hmm", CAPS, min_total=5).confident                       # too short
    assert not select_capabilities("quantum chromodynamics explained", CAPS, min_total=5).confident  # no match
    s = select_capabilities("take my photo", {k: v for k, v in list(CAPS.items())[:5]}, min_total=12)
    assert not s.confident and s.reason == "few_capabilities"
    assert set(s.ids) == set(list(CAPS)[:5])


def test_sticky_capabilities_are_kept():
    s = select_capabilities("take my photo", CAPS, min_total=5, sticky=["location.get"])
    assert "location.get" in s.ids


def test_restrict_and_missing():
    s = select_capabilities("take my photo", CAPS, min_total=5)
    shown = restrict(CAPS, s)
    assert set(shown) == set(s.ids) < set(CAPS)
    assert missing_from(["camera.capture", "location.get", None], shown) == ["location.get"]
    assert restrict(CAPS, select_capabilities("hmm", CAPS, min_total=5)) == CAPS


# ── decomposer wiring ──────────────────────────────────────────────────────

class _Span:
    def set_attribute(self, *a): ...
    def set_error(self, *a): ...


class _Tracer:
    def span(self, *a, **k):
        class C:
            async def __aenter__(s): return _Span()
            async def __aexit__(s, *e): return False
        return C()


def _decomposer(monkeypatch, replies, prompts):
    class LLM:
        async def generate_request(self, req, route=None):
            prompts.append(req.messages[0].content)
            return SimpleNamespace(content=replies.pop(0), finish_reason="stop")

    class Router:
        async def describe_capabilities_with_schema(self, user_id):
            return {k: v for k, v in CAPS.items() if k != "chat.reply"}

    class Guard:
        def inspect_request(self, **kw):
            return SimpleNamespace(memory_block="", history_block="")

        def sanitize_response(self, raw_text, **kw):
            return SimpleNamespace(clean_text=raw_text)

    class Tracker:
        async def record(self, **kw): ...

    monkeypatch.setattr(gd, "get_tracer", lambda: _Tracer())
    d = GoalDecomposer(LLM(), Router(), None, None,
                       PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True)), Tracker(), Guard())
    d.capability_retrieval = True
    return d


def _plan(capability):
    return json.dumps([{"id": "task_1", "name": "x", "capability": capability, "type": "one_shot", "parameters": {}}])


async def test_prompt_shrinks_when_retrieval_is_confident(monkeypatch):
    prompts: list = []
    d = _decomposer(monkeypatch, [_plan("camera.capture")], prompts)
    tasks = await d.decompose("take my photo", {"user_id": "u"})
    assert [t.capability for t in tasks] == ["camera.capture"]
    assert len(prompts) == 1
    assert "camera.capture" in prompts[0] and "flashlight.set" not in prompts[0]


async def test_plan_naming_an_unseen_capability_is_replanned_with_everything(monkeypatch):
    prompts: list = []
    d = _decomposer(monkeypatch, [_plan("flashlight.set"), _plan("flashlight.set")], prompts)
    tasks = await d.decompose("take my photo", {"user_id": "u"})
    assert [t.capability for t in tasks] == ["flashlight.set"]
    assert len(prompts) == 2
    assert "flashlight.set" not in prompts[0] and "flashlight.set" in prompts[1]


async def test_off_by_default_shows_everything(monkeypatch):
    prompts: list = []
    d = _decomposer(monkeypatch, [_plan("camera.capture")], prompts)
    d.capability_retrieval = False
    await d.decompose("take my photo", {"user_id": "u"})
    assert "flashlight.set" in prompts[0]
