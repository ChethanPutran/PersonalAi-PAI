"""Graceful-shutdown / crash recovery against the REAL TaskRepository (sqlite), not the in-memory store."""
import asyncio

from pai.storage.repositories.task import TaskRepository
from pai.tasks.lifecycle import TaskLifecycle
from pai.tasks.models import Task, TaskAuthorization, TaskStatus, TaskType
from rig import device_sends, rig, spec  # noqa: F401
from test_retry_idempotency_recovery import Device, ctx, ok
from pai.planning.dag_scheduler import DAGScheduler


async def _wait(predicate, tries=400):      # polls every 50ms; returns as soon as true (20s ceiling for loaded machines)
    for _ in range(tries):
        await asyncio.sleep(.05)
        if await predicate():
            return True
    return False


async def test_graceful_shutdown_then_restart_recovers_from_database(rig, session_factory):
    async def hang_then_ok(n, f):
        if n == 1:
            await asyncio.sleep(30)
        else:
            await ok(rig, f)
    dev = Device(rig, hang_then_ok)

    repo1 = TaskRepository(session_factory)
    tm1 = await rig.make_tm(store_=repo1)
    run = asyncio.create_task(DAGScheduler(event_bus=rig.bus, task_manager=tm1).execute(
        [spec("a", kind=TaskType.LONG_RUNNING, capability="monitor.watch")], ctx("req-db-1")))
    assert await _wait(lambda: _has(tm1, TaskStatus.RUNNING))
    task_id = (await tm1.list(status=TaskStatus.RUNNING))[0].id
    key = (await tm1.get(task_id)).idempotency_key

    await tm1.shutdown()                       # "server stops"
    run.cancel(); await asyncio.gather(run, return_exceptions=True)

    # "new process": fresh repository object over the same database
    repo2 = TaskRepository(session_factory)
    row = await repo2.require(task_id)
    assert row.status == TaskStatus.INITIALIZED and row.recovery_count == 1   # persisted, not CANCELLED
    assert (row.spec_id, row.idempotency_key) == ("a", key)

    tm2 = await rig.make_tm(store_=repo2)      # recover_unfinished() runs on initialize
    assert await _wait(lambda: _status(tm2, task_id, TaskStatus.COMPLETED))
    await dev.close()

    assert len(dev.invokes) == 2
    assert {f["idempotency_key"] for f in dev.invokes} == {key}
    assert dev.invokes[1]["attempt"] == 1


async def test_crashed_running_task_persists_authorization_without_tokens_and_is_requeued(rig, session_factory):
    dev = Device(rig, lambda n, f: ok(rig, f))
    repo = TaskRepository(session_factory)

    t = Task(name="x", type=TaskType.ONE_SHOT, capability="notification.send", user_id="u",
             session_id="s", device_id="dev-1", executor_id="device_executor",
             spec_id="crashed", idempotency_key="req-db-2:crashed", plugin="p1")
    t.authorization = TaskAuthorization(required=False, tokens=["SECRET"], agent_id="ag")
    await repo.create(t)
    TaskLifecycle.initialize(t); TaskLifecycle.queue(t); TaskLifecycle.start(t)
    await repo.save(t)                         # process "dies" here: RUNNING in the DB

    stored = await TaskRepository(session_factory).require(t.id)
    assert stored.status == TaskStatus.RUNNING and stored.authorization.tokens == []
    assert stored.authorization.agent_id == "ag"

    tm = await rig.make_tm(store_=TaskRepository(session_factory))
    assert await _wait(lambda: _status(tm, t.id, TaskStatus.COMPLETED))
    await dev.close()
    done = await tm.get(t.id)
    assert done.recovery_count == 1 and len(dev.invokes) == 1
    assert dev.invokes[0]["idempotency_key"] == "req-db-2:crashed"


async def _has(tm, status):
    return bool(await tm.list(status=status))


async def _status(tm, task_id, status):
    return (await tm.get(task_id)).status == status
