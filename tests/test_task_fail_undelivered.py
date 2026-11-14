# tests/test_task_fail_undelivered.py
import pytest
from pai.tasks.models import TaskStatus


@pytest.mark.asyncio
async def test_fail_undelivered_from_initialized(task_manager):
    task = await task_manager.create_initialized(
        input="test",
        task_type="system.notifications.show",
        capability="system.notifications.show",
        device_id="dev-1",
        executor_id="device_executor",
        parameters={"title": "x"},
    )
    assert task.status == TaskStatus.INITIALIZED

    failed = await task_manager.fail_undelivered(
        task.id, "test reason",
    )
    assert failed.status == TaskStatus.FAILED
    assert failed.result is not None
    assert failed.result.success is False
    assert failed.result.error == "test reason"
    assert failed.result.metadata["undelivered"] is True


@pytest.mark.asyncio
async def test_fail_undelivered_is_idempotent(task_manager):
    task = await task_manager.create_initialized(
        input="test",
        task_type="system.notifications.show",
        capability="system.notifications.show",
        device_id="dev-1",
        executor_id="device_executor",
    )
    await task_manager.fail_undelivered(task.id, "first")
    # Second call must not raise, must not change state.
    again = await task_manager.fail_undelivered(task.id, "second")
    assert again.status == TaskStatus.FAILED
    assert again.result.error == "first"      # original reason preserved


@pytest.mark.asyncio
async def test_fail_undelivered_respects_pause(task_manager):
    task = await task_manager.create_initialized(
        input="test",
        task_type="system.notifications.show",
        capability="system.notifications.show",
        device_id="dev-1",
        executor_id="device_executor",
    )
    await task_manager.submit(task.id, wait=False)
    await task_manager.pause(task.id)

    result = await task_manager.fail_undelivered(task.id, "reason")
    assert result.status == TaskStatus.PAUSED   # untouched