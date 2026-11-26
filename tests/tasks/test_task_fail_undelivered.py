# tests/tasks/test_task_fail_undelivered.py
import pytest

from pai.planning.models import TaskSpec
from pai.tasks.models import TaskSource, TaskStatus


async def make_task(task_manager):
    """A task as the scheduler creates it (CREATED; submit()/fail_undelivered() walk it forward)."""
    spec = TaskSpec(id="t1", name="test", capability="system.notifications.show", parameters={"title": "x"})
    return await task_manager.create_task(
        spec, user_id="u1", session_id="s1", executor_id="device_executor",
        device_id="dev-1", source=TaskSource.SCHEDULED)


@pytest.mark.asyncio
async def test_fail_undelivered_from_created(task_manager):
    task = await make_task(task_manager)
    assert task.status == TaskStatus.CREATED

    failed = await task_manager.fail_undelivered(task.id, "test reason")
    assert failed.status == TaskStatus.FAILED
    assert failed.result is not None
    assert failed.result.success is False
    assert failed.result.error == "test reason"
    assert failed.result.metadata["undelivered"] is True


@pytest.mark.asyncio
async def test_fail_undelivered_is_idempotent(task_manager):
    task = await make_task(task_manager)
    await task_manager.fail_undelivered(task.id, "first")
    # Second call must not raise, must not change state.
    again = await task_manager.fail_undelivered(task.id, "second")
    assert again.status == TaskStatus.FAILED
    assert again.result.error == "first"      # original reason preserved


@pytest.mark.asyncio
async def test_fail_undelivered_respects_pause(task_manager):
    task = await make_task(task_manager)
    await task_manager.submit(task.id, wait=False)
    await task_manager.pause(task.id)

    result = await task_manager.fail_undelivered(task.id, "reason")
    assert result.status == TaskStatus.PAUSED   # untouched
