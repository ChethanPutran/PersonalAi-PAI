# tests/tasks/test_task_repository.py
import pytest

from pai.common.enums.permission import Origin
from pai.storage.repositories.task import TaskRepository
from pai.tasks.models import Task, TaskAuthorization, TaskStatus, TaskType


def make(name="hello", **kw) -> Task:
    return Task(name=name, type=TaskType.ONE_SHOT, capability="notification.send", **kw)


@pytest.mark.asyncio
async def test_roundtrip(session_factory):
    repo = TaskRepository(session_factory)

    t = make("hello")
    await repo.create(t)

    loaded = await repo.require(t.id)
    assert loaded.name == "hello"
    assert loaded.status == TaskStatus.CREATED
    assert loaded.created_at == t.created_at


@pytest.mark.asyncio
async def test_list_filters(session_factory):
    repo = TaskRepository(session_factory)
    await repo.create(make("a", user_id="u1"))
    await repo.create(make("b", user_id="u2"))

    assert len(await repo.list(user_id="u1")) == 1
    assert len(await repo.list()) == 2
    assert (await repo.count()) == 2


@pytest.mark.asyncio
async def test_recover_orphans(session_factory):
    repo = TaskRepository(session_factory)
    t = make("x")
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


@pytest.mark.asyncio
async def test_new_fields_roundtrip_and_tokens_are_never_persisted(session_factory):
    repo = TaskRepository(session_factory)
    t = make(
        "auth", user_id="u1", session_id="s1", spec_id="spec-7",
        idempotency_key="req-1:spec-7",
    )
    t.recovery_count = 2
    t.authorization = TaskAuthorization(
        required=True, pending=True, tokens=["SECRET.TOKEN"],
        agent_id="agent-1", origin=Origin.USER_DIRECT, source_device_id="phone",
    )
    await repo.create(t)

    loaded = await repo.require(t.id)
    assert (loaded.spec_id, loaded.idempotency_key, loaded.recovery_count) == ("spec-7", "req-1:spec-7", 2)
    a = loaded.authorization
    assert (a.required, a.pending, a.agent_id, a.origin, a.source_device_id) == (
        True, True, "agent-1", Origin.USER_DIRECT, "phone")
    assert a.tokens == []                               # runtime-only: lost on purpose

    # and not in the stored JSON either
    from sqlalchemy import select
    from pai.storage.models import TaskModel
    async with session_factory() as s:
        row = (await s.execute(select(TaskModel).where(TaskModel.id == t.id))).scalar_one()
    assert "SECRET.TOKEN" not in str(row.payload)
