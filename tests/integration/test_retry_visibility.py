"""A retrying task is VISIBLE: status RETRYING for the whole backoff, a `task.retrying` event with
the full task + `retry` info, and cancel / pause work during the wait."""
import asyncio
from datetime import datetime

from pai.common.enums.permission import Origin
from pai.event_bus.task_log import InMemoryTaskEventLog
from pai.orchestration.execution_context import ExecutionContext
from pai.orchestration.task_event_publisher import TaskEventPublisher
from pai.planning.dag_scheduler import DAGScheduler
from pai.tasks.models import TaskRetryPolicy, TaskStatus
from pai.tasks.serialization import serialize_task
from rig import device_sends, fake_device, frame_dict, rig, spec as _spec  # noqa: F401


def spec(i, retries=0):
    """Explicit retry budget (the planner default for TaskSpec.retry_count is not what is under test)."""
    s = _spec(i)
    s.retry_count = retries
    return s


def ctx():
    return ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)


class Device:
    def __init__(self, rig, fail_first: int):
        self.rig, self.fail_first, self.invokes, self.stop = rig, fail_first, [], asyncio.Event()
        self.task = asyncio.create_task(fake_device(rig, self.handle, self.stop))

    async def handle(self, frame):
        f = frame_dict(frame)
        if f["type"] == "control":
            await device_sends(self.rig, type="control_ack", request_id=f["request_id"],
                               operation=f["operation"], status="success")
        elif f["type"] == "invoke":
            self.invokes.append(f)
            if len(self.invokes) <= self.fail_first:
                await device_sends(self.rig, type="result", request_id=f["request_id"], success=False, error="busy")
            else:
                await device_sends(self.rig, type="result", request_id=f["request_id"], result={"ok": 1})

    async def close(self):
        self.stop.set(); await self.task


async def start(rig, tm, delay, retries=2):
    tm = await rig.make_tm(TaskRetryPolicy(default_max_retries=retries, base_delay_seconds=delay, max_delay_seconds=delay))
    dag = DAGScheduler(event_bus=rig.bus, task_manager=tm)
    run = asyncio.create_task(dag.execute([spec("a", retries)], ctx()))
    return tm, run


async def wait_status(tm, status, timeout=5):
    for _ in range(int(timeout / .02)):
        tasks = [t for t in await tm.list() if t.status == status]
        if tasks:
            return tasks[0]
        await asyncio.sleep(.02)
    raise AssertionError(f"no task reached {status}")


def names(rig):
    return [n for n, _ in rig.events]


async def test_event_order_status_and_retry_info_during_backoff(rig):
    dev = Device(rig, fail_first=1)
    tm, run = await start(rig, None, delay=.6)
    task = await wait_status(tm, TaskStatus.RETRYING)

    live = serialize_task(await tm.get(task.id))                      # what GET /tasks returns now
    assert live["status"] == "retrying"
    r = live["retry"]
    assert (r["attempt"], r["max_attempts"], r["last_error"]) == (1, 2, "busy")
    assert r["delay_seconds"] == .6
    assert datetime.fromisoformat(r["next_retry_at"]).utcoffset() is not None      # ISO-8601, UTC

    out = await asyncio.wait_for(run, 10)
    await asyncio.sleep(.2); await dev.close()
    assert out.status == "completed"
    seq = [n for n in names(rig) if n in ("task.retrying", "task.started", "task.failed", "task.completed")]
    assert seq.index("task.retrying") < len(seq) - 1 and "task.failed" not in seq
    assert seq.index("task.started") < seq.index("task.retrying") < seq.index("task.started", seq.index("task.retrying"))
    final = serialize_task(await tm.get(task.id))
    assert final["status"] == "completed" and final["retry"] is None            # retry clears


async def test_retrying_bus_payload_is_the_full_task_plus_retry(rig):
    dev = Device(rig, fail_first=1)
    tm, run = await start(rig, None, delay=.05)
    await asyncio.wait_for(run, 10); await asyncio.sleep(.1); await dev.close()
    payload = next(d for n, d in rig.events if n == "task.retrying")
    assert payload["id"] and payload["status"] == "retrying" and payload["user_id"] == "u"
    assert payload["capability"] and "controls" in payload
    assert payload["retry"]["attempt"] == 1 and payload["retry"]["max_attempts"] == 2
    assert payload["retry"]["next_retry_at"] and payload["retry"]["last_error"] == "busy"


async def test_retrying_event_reaches_the_user_stream_as_a_full_task(rig):
    log = InMemoryTaskEventLog()
    pub = TaskEventPublisher(event_bus=rig.bus, log=log)
    await pub.start()
    dev = Device(rig, fail_first=1)
    tm, run = await start(rig, None, delay=.05)
    await asyncio.wait_for(run, 10); await asyncio.sleep(.2); await dev.close()
    events = (await log.replay("u", 0)).events
    retrying = [e for e in events if e.event == "task.retrying"]
    assert len(retrying) == 1
    assert retrying[0].data["status"] == "retrying" and retrying[0].data["retry"]["attempt"] == 1
    assert retrying[0].data["can_cancel"] is True
    order = [e.event for e in events]
    assert order.index("task.retrying") < max(i for i, n in enumerate(order) if n == "task.started")
    await pub.stop()


async def test_cancel_during_backoff(rig):
    dev = Device(rig, fail_first=5)
    tm, run = await start(rig, None, delay=5)
    task = await wait_status(tm, TaskStatus.RETRYING)
    assert serialize_task(task)["can_cancel"] is True
    done = await tm.cancel(task.id)
    assert done.status == TaskStatus.CANCELLED and done.cancel_outcome is not None
    await asyncio.wait_for(run, 10); await asyncio.sleep(.1); await dev.close()
    assert len(dev.invokes) == 1                                        # no further attempt
    assert "task.cancelled" in names(rig) and "task.failed" not in names(rig)
    assert serialize_task(await tm.get(task.id))["retry"] is None


async def test_pause_during_backoff_holds_the_next_attempt_until_resume(rig):
    dev = Device(rig, fail_first=1)
    tm, run = await start(rig, None, delay=.3)
    task = await wait_status(tm, TaskStatus.RETRYING)
    paused = await tm.pause(task.id)
    assert paused.status == TaskStatus.PAUSED
    view = serialize_task(paused)
    assert view["retry"] is not None and view["retry"]["next_retry_at"] is None   # held, no clock
    await asyncio.sleep(.8)                                              # well past the backoff
    assert len(dev.invokes) == 1 and (await tm.get(task.id)).status == TaskStatus.PAUSED
    resumed = await tm.resume(task.id)
    assert resumed.status == TaskStatus.RETRYING and serialize_task(resumed)["retry"]["next_retry_at"]
    out = await asyncio.wait_for(run, 10); await dev.close()
    assert out.status == "completed" and len(dev.invokes) == 2
    assert {"task.paused", "task.resumed"} <= set(names(rig))


async def test_budget_exhausted_announces_failed_once_with_no_retry_info(rig):
    dev = Device(rig, fail_first=99)
    tm, run = await start(rig, None, delay=.02, retries=2)
    out = await asyncio.wait_for(run, 10); await asyncio.sleep(.2); await dev.close()
    assert out.status == "failed" and len(dev.invokes) == 3
    ns = names(rig)
    assert ns.count("task.failed") == 1 and ns.count("task.retrying") == 2
    assert ns.index("task.failed") > max(i for i, n in enumerate(ns) if n == "task.retrying")
    task = (await tm.list())[0]
    final = serialize_task(task)
    assert final["status"] == "failed" and final["retry"] is None and final["error"] == "busy"


async def test_user_retry_publishes_the_same_retrying_event(rig):
    dev = Device(rig, fail_first=1)
    out = await asyncio.wait_for(rig.dag.execute([spec("a")], ctx()), 10)       # budget 0: fails
    assert out.status == "failed"
    task = (await rig.tm.list(status=TaskStatus.FAILED))[0]
    await rig.tm.retry(task.id)
    for _ in range(100):
        await asyncio.sleep(.03)
        if (await rig.tm.get(task.id)).status == TaskStatus.COMPLETED:
            break
    await dev.close()
    payload = next(d for n, d in rig.events if n == "task.retrying")
    assert payload["status"] == "retrying" and payload["retry"]["attempt"] == 1 and payload["id"] == task.id
    assert (await rig.tm.get(task.id)).status == TaskStatus.COMPLETED
