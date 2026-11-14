# tests/test_builtin_routing.py
import pytest
from pai.planning.models import TaskSpec


@pytest.mark.asyncio
async def test_builtin_echo_via_taskrunner(task_runner, app_context, task_manager):
    """A built-in capability resolves, executes, and completes end-to-end."""
    spec = TaskSpec(
        id="t1",
        type="echo",
        capability="test.echo",
        parameters={"x": 1, "message": "hi"},
    )

    result = await task_runner.run(spec, app_context)
    print(f"result: {result}")

    assert result["status"] == "completed"
    assert result["capability"] == "test.echo"
    assert result["executor_id"] == "builtin_executor"
    assert result["result"].success
    assert result["result"].output == {"echoed": {"x": 1, "message": "hi"}}

    # Runtime task must have no device_id — built-ins aren't device-bound
    runtime = await task_manager.get(result["task_id"])
    assert runtime.device_id=="self"  # built-ins are never device-bound
    assert runtime.executor_id == "builtin_executor"
    assert runtime.status.value == "completed"


@pytest.mark.asyncio
async def test_builtin_skips_authorization(
    task_runner, app_context, capability_router,
):
    """Built-ins must not hit the device-authorization path."""
    spec = TaskSpec(
        id="t1", type="echo", capability="test.echo", parameters={},
    )
    await task_runner.run(spec, app_context)

    # The StubAuthorizer records every call; a built-in should be zero.
    auth = task_runner.authorizer
    assert auth.calls == [], (
        f"built-in should skip authorize(), got: {auth.calls}"
    )


@pytest.mark.asyncio
async def test_unknown_capability_rejected(task_runner, app_context):
    """Router rejects a capability that isn't built-in and isn't a plugin."""
    spec = TaskSpec(
        id="t1", type="nope", capability="does.not.exist", parameters={},
    )
    with pytest.raises(Exception):
        await task_runner.run(spec, app_context)