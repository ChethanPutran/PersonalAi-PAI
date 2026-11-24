"""A failure that can never succeed (unknown capability) fails at once — no backoff-and-retry."""
import pytest

from pai.tasks.models import Task, TaskResult, TaskStatus, TaskType


def stale_task(**kw) -> Task:
    """What startup recovery finds: a persisted task whose capability no longer exists."""
    return Task(name="long", type=TaskType.LONG_RUNNING, capability="test.sleep", user_id="u",
                session_id="s", device_id="self", executor_id="builtin_executor",
                max_retries=3, status=TaskStatus.INITIALIZED, **kw)


async def test_unknown_builtin_capability_is_not_retried(task_manager):
    t = stale_task()
    await task_manager.store.create(t)
    result = await task_manager.submit(t.id, wait=True)

    after = await task_manager.get(t.id)
    assert after.status == TaskStatus.FAILED
    assert after.retry_count == 0                          # no retries were attempted (max was 3)
    assert "No built-in capability 'test.sleep'" in (after.error or "")
    assert result.retryable is False


def test_retryable_defaults_to_true_and_travels_with_the_result():
    assert TaskResult(task_id="x").retryable is True
    assert TaskResult(task_id="x", retryable=False).retryable is False
