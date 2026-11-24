# tests/orchestration/test_builtin_routing.py
import pytest

from pai.planning.models import TaskSpec
from pai.tasks.models import TaskStatus


@pytest.mark.asyncio
async def test_builtin_echo_via_task_manager(task_manager, app_context):
    """A built-in capability resolves, executes in-process and completes."""
    spec = TaskSpec(id="t1", name="t1", input="echo", type="one_shot",
                    capability="test.echo", parameters={"x": 1, "message": "hi"})

    result = await task_manager.run(spec, app_context)

    assert result.success, result.error
    assert result.output == {"echoed": {"x": 1, "message": "hi"}}
    assert result.executor_id == "builtin_executor"

    (runtime,) = await task_manager.list()
    assert runtime.spec_id == "t1"
    assert runtime.status == TaskStatus.COMPLETED
    assert runtime.executor_id == "builtin_executor"


@pytest.mark.asyncio
async def test_builtin_skips_authorization(task_manager, app_context, security_manager):
    """Built-ins must not hit the device/plugin authorization path."""
    spec = TaskSpec(id="t1", name="t1", input="echo", type="one_shot",
                    capability="test.echo", parameters={})
    await task_manager.run(spec, app_context)
    assert security_manager.calls == []


@pytest.mark.asyncio
async def test_unknown_capability_rejected(task_manager, app_context):
    spec = TaskSpec(id="t1", name="t1", input="x", type="one_shot",
                    capability="does.not.exist", parameters={})
    with pytest.raises(Exception):
        await task_manager.run(spec, app_context)
