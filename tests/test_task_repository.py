# tests/test_task_repository.py
import pytest
from pai.tasks.models import Task, TaskStatus, TaskPriority
from pai.storage.repositories.task import TaskRepository


@pytest.mark.asyncio
async def test_roundtrip(session_factory):
    repo = TaskRepository(session_factory)

    t = Task(title="hello", input="x", type="demo")
    await repo.create(t)

    loaded = await repo.require(t.id)
    assert loaded.title == "hello"
    assert loaded.status == TaskStatus.CREATED
    assert loaded.created_at == t.created_at


@pytest.mark.asyncio
async def test_list_filters(session_factory):
    repo = TaskRepository(session_factory)
    await repo.create(Task(title="a", input="x", type="t", user_id="u1"))
    await repo.create(Task(title="b", input="x", type="t", user_id="u2"))

    assert len(await repo.list(user_id="u1")) == 1
    assert len(await repo.list()) == 2
    assert (await repo.count()) == 2


@pytest.mark.asyncio
async def test_recover_orphans(session_factory):
    repo = TaskRepository(session_factory)
    t = Task(title="x", input="y", type="z")
    await repo.create(t)

    # Simulate crash mid-run: manually set RUNNING
    t.mark_initialized(capability="x", device_id="d", executor_id="e")
    t.mark_queued()
    t.mark_running()
    await repo.save(t)

    recovered = await repo.recover_orphaned()
    assert recovered == 1

    reloaded = await repo.require(t.id)
    assert reloaded.status == TaskStatus.FAILED
    assert "Interrupted" in reloaded.error