"""Planner: task type/timeout come from the manifest; ambiguity becomes a question."""
from types import SimpleNamespace

import pytest

from pai.common.enums.task import TaskType
from pai.config.models.prompts import PromptRegistryConfig
from pai.planning.goal_decomposer import (
    CHAT_CAPABILITY_ID, ClarificationNeeded, GoalDecomposer, _CHAT_CAPABILITY_SPEC,
)
from pai.prompts.registry import PromptRegistry

CAMERA = {
    "description": "Take a photo",
    "task_type": "one_shot", "timeout_seconds": 30.0, "retryable": False,
    "parameters": {"type": "object", "required": ["lens"], "properties": {
        "lens": {"type": "string", "enum": ["front", "back"]}}},
}
STREAM = {"description": "Live camera", "task_type": "stream", "timeout_seconds": None,
          "parameters": {"type": "object", "properties": {}}}
DEFAULTED = {"description": "x", "parameters": {"type": "object", "required": ["lens"],
             "properties": {"lens": {"type": "string", "enum": ["front", "back"],
                                     "default": "back"}}}}
FREE = {"description": "x", "parameters": {"type": "object", "required": ["url"],
        "properties": {"url": {"type": "string", "description": "page to open"}}}}


def caps(**extra):
    return {"camera.capture": CAMERA, "camera.stream": STREAM, "cam.default": DEFAULTED,
            "web.open": FREE, CHAT_CAPABILITY_ID: dict(_CHAT_CAPABILITY_SPEC), **extra}


def norm(raw):
    return GoalDecomposer._normalize_tasks(raw, caps())


def test_type_and_timeout_come_from_manifest_not_llm():
    [t] = norm([{"id": "a", "capability": "camera.capture", "type": "stream", "timeout": 9999,
                 "parameters": {"lens": "front"}}])
    assert t.type == TaskType.ONE_SHOT and t.timeout == 30.0 and t.retryable is False
    [s] = norm([{"id": "b", "capability": "camera.stream", "type": "one_shot"}])
    assert s.type == TaskType.STREAM


def test_llm_garbage_type_never_fails_planning():
    [t] = norm([{"id": "a", "capability": "camera.stream", "type": "banana"}])
    assert t.type == TaskType.STREAM


def test_chat_reply_is_one_shot():
    [t] = norm([{"id": "a", "capability": CHAT_CAPABILITY_ID, "parameters": {"tone": "brief"}}])
    assert t.type == TaskType.ONE_SHOT


def test_missing_required_enum_asks_with_options():
    with pytest.raises(ClarificationNeeded) as e:
        norm([{"id": "a", "capability": "camera.capture", "parameters": {}}])
    [issue] = e.value.issues
    assert issue.parameter == "lens" and issue.reason == "missing"
    assert issue.options == ["front", "back"]
    assert isinstance(e.value, ValueError)


def test_invalid_enum_value_asks_but_wrong_case_is_forgiven():
    with pytest.raises(ClarificationNeeded) as e:
        norm([{"id": "a", "capability": "camera.capture", "parameters": {"lens": "selfie"}}])
    assert e.value.issues[0].reason == "invalid"
    [t] = norm([{"id": "a", "capability": "camera.capture", "parameters": {"lens": " BACK "}}])
    assert t.parameters["lens"] == "back"


def test_default_wins_over_asking():
    [t] = norm([{"id": "a", "capability": "cam.default", "parameters": {}}])
    assert t.parameters["lens"] == "back"
    [t] = norm([{"id": "a", "capability": "cam.default", "parameters": {"lens": None}}])
    assert t.parameters["lens"] == "back"


def test_blank_free_text_required_is_asked():
    with pytest.raises(ClarificationNeeded) as e:
        norm([{"id": "a", "capability": "web.open", "parameters": {"url": "  "}}])
    assert e.value.issues[0].options == []


def test_clarification_task_shape():
    with pytest.raises(ClarificationNeeded) as e:
        norm([{"id": "a", "capability": "camera.capture", "parameters": {}}])
    t = GoalDecomposer._clarification_task(e.value.issues)
    assert t.capability == CHAT_CAPABILITY_ID and t.type == TaskType.ONE_SHOT
    assert t.parameters["intent"] == "clarify" and t.parameters["options"] == ["front", "back"]
    assert "front" in t.parameters["question"] and "back" in t.parameters["question"]


def test_other_validation_errors_stay_fatal():
    with pytest.raises(ValueError) as e:
        norm([{"id": "a", "capability": "nope"}])
    assert not isinstance(e.value, ClarificationNeeded)


def test_capabilities_block_shows_required_enum_default():
    block = GoalDecomposer._render_capabilities(caps())
    assert "lens: string (required; one of: front, back)" in block
    assert "default='back'" in block


def test_fallback_uses_manifest_type():
    d = GoalDecomposer.__new__(GoalDecomposer)
    [t] = d._fallback_decompose("take a photo", {}, {"camera.capture": STREAM,
                                                     CHAT_CAPABILITY_ID: _CHAT_CAPABILITY_SPEC})
    assert t.type == TaskType.STREAM


# ── full decompose(): the clarification plan is what the caller receives ────────────────────
class _Span:
    def set_attribute(self, *a, **k): ...
    def set_error(self, *a, **k): ...


class _Tracer:
    def span(self, *a, **k):
        span = _Span()

        class _Ctx:
            async def __aenter__(self_): return span
            async def __aexit__(self_, *exc): return False
        return _Ctx()


@pytest.mark.asyncio
async def test_decompose_returns_clarification_plan(monkeypatch):
    import pai.planning.goal_decomposer as gd
    monkeypatch.setattr(gd, "get_tracer", lambda: _Tracer())
    seen = {}

    class LLM:
        async def generate_request(self, req, route=None):
            seen["prompt"] = req.messages[0].content
            return SimpleNamespace(
                content='[{"id":"task_1","name":"Photo","capability":"camera.capture",'
                        '"type":"stream","parameters":{}}]', finish_reason="stop")

    class Router:
        async def describe_capabilities_with_schema(self, user_id):
            return {"camera.capture": CAMERA}

    class Guard:
        def inspect_request(self, **kw):
            return SimpleNamespace(memory_block="", history_block="")

        def sanitize_response(self, raw_text, **kw):
            return SimpleNamespace(clean_text=raw_text)

    class Tracker:
        async def record(self, **kw): ...

    d = GoalDecomposer(LLM(), Router(), None, None,
                       PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True)),
                       Tracker(), Guard())
    plan = await d.decompose("take a photo", {"user_id": "u1"})
    assert [t.capability for t in plan] == [CHAT_CAPABILITY_ID]
    assert plan[0].parameters["intent"] == "clarify"
    assert plan[0].parameters["options"] == ["front", "back"]
    assert "lens: string (required; one of: front, back)" in seen["prompt"]


def test_task_manager_applies_manifest_type_and_timeout():
    from pai.tasks.manager import TaskManager
    cap = SimpleNamespace(taskType=TaskType.LONG_RUNNING, timeoutSeconds=120)
    manifest = SimpleNamespace(get_capability=lambda c: cap if c == "x.y" else None)
    res = SimpleNamespace(plugin_manifest=manifest, capability="x.y")
    assert TaskManager.manifest_execution(res) == {"type": TaskType.LONG_RUNNING, "timeout": 120.0}
    assert TaskManager.manifest_execution(SimpleNamespace(plugin_manifest=None, capability="a")) == {}
