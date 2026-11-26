"""A reminder delivered by push must end COMPLETED, not "push delivered but local state update failed"."""
import pytest

from pai.planning.models import TaskSpec
from pai.tasks.models import TaskSource, TaskStatus


async def scheduler_style_task(task_manager):
    """Exactly what SchedulerService._fire_reminder hands over: created, never initialized."""
    spec = TaskSpec(id="schedule_s1", name="Call Mom", capability="system.notifications.show",
                    parameters={"title": "Call Mom"})
    return await task_manager.create_task(
        spec, user_id="u1", session_id="schedule:s1", executor_id="device_executor",
        device_id="dev-1", source=TaskSource.SCHEDULED)


@pytest.mark.asyncio
async def test_a_task_still_in_created_completes_via_push(task_manager):
    task = await scheduler_style_task(task_manager)
    assert task.status == TaskStatus.CREATED                 # the state that used to raise created -> queued

    done = await task_manager.complete_via_push(task.id, payload={"delivered_via": "push", "recipients": 1})
    assert done.status == TaskStatus.COMPLETED
    assert done.result.success is True and done.result.output == {"delivered_via": "push", "recipients": 1}
    assert (await task_manager.store.require(task.id)).status == TaskStatus.COMPLETED     # and it was saved


@pytest.mark.asyncio
async def test_complete_via_push_is_idempotent(task_manager):
    task = await scheduler_style_task(task_manager)
    await task_manager.complete_via_push(task.id, payload={"n": 1})
    again = await task_manager.complete_via_push(task.id, payload={"n": 2})
    assert again.status == TaskStatus.COMPLETED and again.result.output == {"n": 1}


@pytest.mark.asyncio
async def test_a_failed_task_is_not_resurrected(task_manager):
    task = await scheduler_style_task(task_manager)
    await task_manager.fail_undelivered(task.id, "no channel")
    after = await task_manager.complete_via_push(task.id, payload={})
    assert after.status == TaskStatus.FAILED
