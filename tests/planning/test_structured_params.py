"""An object parameter must stay an object (a dict used to be printed into a repr string)."""
import pytest

from pai.executors.builtin import BuiltinExecutor
from pai.orchestration.builtin_capabilities import BuiltinCapability, BuiltinCapabilityRegistry
from pai.planning.goal_decomposer import GoalDecomposer
from pai.tasks.models import Task, TaskType
from pai.utils.structured import parse_structured

# Your real payload: the model's `parameters` after the planner's coercion.
REPR_STRING = "{'lens': 'back', 'outputPath': '/home/chethan/Pictures/PAI/photo_5s.jpg', 'format': 'jpeg'}"
SCHEMA = {"properties": {
    "title": {"type": "string"}, "parameters": {"type": "object"}, "tags": {"type": "array"},
    "count": {"type": "integer"}, "enabled": {"type": "boolean"}, "label": {"type": "string"}}}


def test_parse_structured_accepts_json_python_repr_and_leaves_the_rest():
    assert parse_structured({"a": 1}) == {"a": 1}
    assert parse_structured('{"a": 1}') == {"a": 1}
    assert parse_structured(REPR_STRING)["lens"] == "back"
    assert parse_structured("[1, 2]") == [1, 2]
    for untouched in ("plain text", "{not valid", "", 5, None, "__import__('os').system('x')", "{'a': undefined_name}"):
        assert parse_structured(untouched) == untouched         # literals only: nothing is executed


def test_planner_coercion_keeps_objects_and_arrays_structured():
    out = GoalDecomposer._coerce_params(
        {"parameters": {"lens": "back", "n": 2}, "tags": ["a", "b"]}, SCHEMA)
    assert out["parameters"] == {"lens": "back", "n": 2} and out["tags"] == ["a", "b"]   # was str(dict)!


def test_planner_coercion_repairs_stringified_objects_and_still_coerces_scalars():
    out = GoalDecomposer._coerce_params(
        {"parameters": REPR_STRING, "tags": '["x"]', "count": "3", "enabled": "yes", "label": 7}, SCHEMA)
    assert out["parameters"]["format"] == "jpeg" and out["tags"] == ["x"]
    assert out["count"] == 3 and out["enabled"] is True and out["label"] == "7"


def test_planner_coercion_never_corrupts_values_it_does_not_understand():
    out = GoalDecomposer._coerce_params({"label": {"a": 1}, "unknown": object}, SCHEMA)
    assert out["label"] == {"a": 1}                 # a string-typed field with a dict is left for validation


async def test_input_errors_are_not_retried_but_other_failures_still_are():
    reg = BuiltinCapabilityRegistry()
    async def bad_input(params, **kw): raise ValueError("bad input")
    async def flaky(params, **kw): raise RuntimeError("transient")
    for cid, fn in (("t.bad", bad_input), ("t.flaky", flaky)):
        reg.register(BuiltinCapability(id=cid, description=cid, parameters={}, handler=fn))
    ex = BuiltinExecutor(reg)
    for cid, retryable in (("t.bad", False), ("t.flaky", True)):
        task = Task(name=cid, type=TaskType.ONE_SHOT, capability=cid, user_id="u")
        result = await ex.execute_task(task)
        assert result["status"] == "failed" and result["retryable"] is retryable, cid
