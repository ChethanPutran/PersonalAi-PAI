"""Retry with backoff, idempotency, graceful shutdown + restart recovery."""
import asyncio

from pai.common.enums.permission import Origin
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.dag_scheduler import DAGScheduler
from pai.tasks.models import TaskRetryPolicy, TaskStatus, TaskType
from rig import FakeSecurity, device_sends, fake_device, frame_dict, rig, spec  # noqa: F401


def ctx(request_id=None):
    c = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)
    if request_id:
        c.request_id = request_id
    return c


class Device:
    """Scriptable device: records invokes; `behave(n, frame)` decides each reply."""
    def __init__(self, rig, behave):
        self.rig, self.behave, self.invokes, self.stop = rig, behave, [], asyncio.Event()
        self.task = asyncio.create_task(fake_device(rig, self.handle, self.stop))
    async def handle(self, frame):
        f = frame_dict(frame)
        if f["type"] == "control":
            await device_sends(self.rig, type="control_ack", request_id=f["request_id"],
                               operation=f["operation"], status="success"); return
        if f["type"] != "invoke": return
        self.invokes.append(f)
        await self.behave(len(self.invokes), f)
    async def close(self):
        self.stop.set(); await self.task


async def ok(rig, f):  await device_sends(rig, type="result", request_id=f["request_id"], result={"ok": 1})
async def bad(rig, f): await device_sends(rig, type="result", request_id=f["request_id"], success=False, error="busy")


async def test_transient_failures_are_retried_with_same_idempotency_key(rig):
    async def behave(n, f): await (bad if n <= 2 else ok)(rig, f)
    dev = Device(rig, lambda n, f: behave(n, f))
    tm = await rig.make_tm(TaskRetryPolicy(default_max_retries=3, base_delay_seconds=0.01))
    dag = DAGScheduler(event_bus=rig.bus, task_manager=tm)
    out = await asyncio.wait_for(dag.execute([spec("a")], ctx()), 10)
    await asyncio.sleep(.2); await dev.close()

    assert out.status == "completed"
    assert len(dev.invokes) == 3
    assert len({f["idempotency_key"] for f in dev.invokes}) == 1          # device can de-dupe
    assert [f["attempt"] for f in dev.invokes] == [0, 1, 2]
    names = [n for n, _ in rig.events]
    assert names.count("task.retrying") == 2 and "task.failed" not in names   # retries aren't failures


async def test_gives_up_after_budget_and_announces_failure_once(rig):
    dev = Device(rig, lambda n, f: bad(rig, f))
    tm = await rig.make_tm(TaskRetryPolicy(default_max_retries=2, base_delay_seconds=0.01))
    out = await asyncio.wait_for(DAGScheduler(event_bus=rig.bus, task_manager=tm).execute([spec("a")], ctx()), 10)
    await asyncio.sleep(.2); await dev.close()
    assert out.status == "failed" and len(dev.invokes) == 3                  # 1 + 2 retries
    assert [n for n, _ in rig.events].count("task.failed") == 1


async def test_user_can_retry_a_failed_task(rig):
    dev = Device(rig, lambda n, f: (bad if n == 1 else ok)(rig, f))
    c = ctx()
    out = await asyncio.wait_for(rig.dag.execute([spec("a")], c), 10)        # default budget 0 -> fails
    assert out.status == "failed"
    task = (await rig.tm.list(status=TaskStatus.FAILED))[0]
    await rig.tm.retry(task.id)
    for _ in range(40):
        await asyncio.sleep(.05)
        if (await rig.tm.get(task.id)).status == TaskStatus.COMPLETED: break
    await dev.close()
    assert (await rig.tm.get(task.id)).status == TaskStatus.COMPLETED and len(dev.invokes) == 2


async def test_authorization_failures_are_never_retried(rig):
    dev = Device(rig, lambda n, f: ok(rig, f))
    tm = await rig.make_tm(TaskRetryPolicy(default_max_retries=3, base_delay_seconds=0.01),
                           security=FakeSecurity(reject_tokens=True))
    out = await asyncio.wait_for(DAGScheduler(event_bus=rig.bus, task_manager=tm).execute([spec("a")], ctx()), 10)
    await dev.close()
    assert out.status == "failed" and dev.invokes == []                      # never reached the device
    assert [n for n, _ in rig.events].count("task.retrying") == 0


async def test_same_request_is_idempotent_no_duplicate_execution(rig):
    async def slow_ok(n, f):
        await asyncio.sleep(.2); await ok(rig, f)
    dev = Device(rig, slow_ok)
    c = ctx("req-1")
    # same request driven twice concurrently (client retry / double submit)
    r1, r2 = await asyncio.wait_for(asyncio.gather(
        rig.dag.execute([spec("a")], c), DAGScheduler(event_bus=rig.bus, task_manager=rig.tm).execute([spec("a")], c)), 10)
    # and once more after it finished
    r3 = await DAGScheduler(event_bus=rig.bus, task_manager=rig.tm).execute([spec("a")], c)
    await dev.close()
    assert len(dev.invokes) == 1
    assert r1.status == r2.status == r3.status == "completed"
    assert len(await rig.tm.list()) == 1


async def test_graceful_shutdown_keeps_tasks_recoverable_and_restart_finishes_them(rig):
    async def hang_then_ok(n, f):
        if n == 1:
            await asyncio.sleep(30)          # first attempt never answers (server stops first)
        else:
            await ok(rig, f)
    dev = Device(rig, hang_then_ok)
    tm1 = await rig.make_tm()
    run = asyncio.create_task(DAGScheduler(event_bus=rig.bus, task_manager=tm1).execute(
        [spec("a", kind=TaskType.LONG_RUNNING, capability="monitor.watch")], ctx("req-2")))
    for _ in range(40):
        await asyncio.sleep(.05)
        if await tm1.list(status=TaskStatus.RUNNING): break
    task_id = (await tm1.list(status=TaskStatus.RUNNING))[0].id
    first_key = (await tm1.get(task_id)).idempotency_key

    await tm1.shutdown()                                   # graceful stop mid-flight
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)
    saved = await rig.store.require(task_id)
    assert saved.status == TaskStatus.INITIALIZED and saved.recovery_count == 1   # NOT cancelled

    tm2 = await rig.make_tm()                              # "server restarted": recover on init
    for _ in range(60):
        await asyncio.sleep(.05)
        if (await tm2.get(task_id)).status == TaskStatus.COMPLETED: break
    await dev.close()

    done = await tm2.get(task_id)
    assert done.status == TaskStatus.COMPLETED
    assert len(dev.invokes) == 2
    assert {f["idempotency_key"] for f in dev.invokes} == {first_key}       # device can de-dupe the re-send
    assert dev.invokes[1]["attempt"] == 1
    assert "task.recovered" in [n for n, _ in rig.events]


async def test_crash_leaves_running_task_and_restart_requeues_it_but_never_reruns_finished(rig):
    dev = Device(rig, lambda n, f: ok(rig, f))
    c = ctx("req-3")
    await asyncio.wait_for(rig.dag.execute([spec("done")], c), 10)          # finished before the "crash"
    # simulate a crash mid-run: a task persisted as RUNNING with no live coroutine
    from pai.tasks.lifecycle import TaskLifecycle
    from pai.tasks.models import Task
    t = Task(name="x", type=TaskType.ONE_SHOT, capability="notification.send", user_id="u",
             session_id="s", device_id="dev-1", executor_id="device_executor", spec_id="crashed",
             idempotency_key="req-3:crashed")
    await rig.store.create(t)
    TaskLifecycle.initialize(t); TaskLifecycle.queue(t); TaskLifecycle.start(t)
    await rig.store.save(t)

    tm2 = await rig.make_tm()
    for _ in range(40):
        await asyncio.sleep(.05)
        if (await tm2.get(t.id)).status == TaskStatus.COMPLETED: break
    await dev.close()
    assert (await tm2.get(t.id)).status == TaskStatus.COMPLETED
    assert len(dev.invokes) == 2            # 'done' once (before), 'crashed' once (after) — 'done' not re-run
