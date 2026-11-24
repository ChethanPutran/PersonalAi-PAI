"""Capability-driven pause/cancel: manifest `controls` decide what is offered, a pause after
dispatch needs the device's ack, refusals are honest 409s, cancel records its outcome."""
import asyncio

import pytest
from fastapi import HTTPException

from pai.api.routes import tasks as tasks_routes
from pai.common.enums.permission import Origin
from pai.common.task_controls import CancelMode, CancelOutcomeKind, TaskControls
from pai.orchestration.execution_context import ExecutionContext
from pai.tasks.lifecycle import InvalidTaskTransition, TaskControlError
from pai.tasks.models import TaskStatus, TaskType
from pai.tasks.serialization import serialize_task
from rig import FakeResolver, device_sends, fake_device, frame_dict, rig, spec  # noqa: F401

CTX = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)


def device(rig, ack_controls=True, hold=30):
    seen = []

    async def handler(frame):
        f = frame_dict(frame)
        if f["type"] == "control":
            seen.append(f["operation"])
            if ack_controls:
                await device_sends(rig, type="control_ack", request_id=f["request_id"],
                                   operation=f["operation"], status="success")
        elif f["type"] == "invoke":
            await asyncio.sleep(hold)
            await device_sends(rig, type="result", request_id=f["request_id"], result={})
    return handler, seen


async def start(rig, capability, kind=TaskType.LONG_RUNNING):
    run = asyncio.create_task(rig.dag.execute([spec("t", kind=kind, capability=capability)], CTX))
    for _ in range(100):
        await asyncio.sleep(.03)
        running = await rig.tm.list(status=TaskStatus.RUNNING)
        if running and running[0].dispatched_at:
            return run, running[0]
    raise AssertionError("task never dispatched")


@pytest.fixture
async def dev(rig):
    stop, tasks = asyncio.Event(), []

    def launch(**kw):
        h, seen = device(rig, **kw)
        tasks.append(asyncio.create_task(fake_device(rig, h, stop)))
        return seen
    yield launch
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)


async def finish(run):
    try:
        await asyncio.wait_for(run, 10)
    except Exception:
        pass


async def test_pausable_capability_pauses_with_device_ack_and_serializes_controls(rig, dev):
    seen = dev()
    run, task = await start(rig, "monitor.watch")
    live = serialize_task(await rig.tm.store.require(task.id))
    assert live["can_pause"] is True and live["can_cancel"] is True
    assert live["controls"] == {"pause": True, "cancel": "cooperative"}

    paused = await rig.tm.pause(task.id)
    assert paused.status == TaskStatus.PAUSED and seen == ["pause"]
    view = serialize_task(paused)
    assert view["can_pause"] is False and view["can_resume"] is True

    resumed = await rig.tm.resume(task.id)
    assert resumed.status == TaskStatus.RUNNING and seen == ["pause", "resume"]
    await rig.tm.cancel(task.id)
    await finish(run)


async def test_capability_without_pause_is_refused_with_control_not_supported(rig, dev):
    dev()
    run, task = await start(rig, "notification.send")              # default controls: pause=False
    live = serialize_task(await rig.tm.store.require(task.id))
    assert live["can_pause"] is False and live["control_blocked_reason"]
    with pytest.raises(TaskControlError) as e:
        await rig.tm.pause(task.id)
    assert e.value.code == "control_not_supported"
    assert (await rig.tm.store.require(task.id)).status == TaskStatus.RUNNING     # state unchanged
    await rig.tm.cancel(task.id)
    await finish(run)


async def test_unacknowledged_pause_leaves_the_task_running(rig, dev):
    dev(ack_controls=False)
    rig.em.get("device_executor").control_ack_timeout = .3
    run, task = await start(rig, "monitor.watch")
    with pytest.raises(TaskControlError) as e:
        await rig.tm.pause(task.id)
    assert e.value.code == "control_not_acknowledged"
    assert (await rig.tm.store.require(task.id)).status == TaskStatus.RUNNING
    rig.tm.cancel_ack_timeout = .3
    await rig.tm.cancel(task.id)
    await finish(run)


async def test_cancel_acked_by_the_device_records_acked(rig, dev):
    dev()
    run, task = await start(rig, "monitor.watch")
    done = await rig.tm.cancel(task.id)
    assert done.status == TaskStatus.CANCELLED and done.cancel_outcome == CancelOutcomeKind.ACKED
    await finish(run)


async def test_cancel_not_confirmed_by_the_device_records_abandoned(rig, dev):
    dev(ack_controls=False)
    rig.tm.cancel_ack_timeout = .3
    rig.em.get("device_executor").cancel_ack_timeout = .3
    run, task = await start(rig, "camera.stream", kind=TaskType.STREAM)
    done = await rig.tm.cancel(task.id)
    assert done.status == TaskStatus.CANCELLED and done.cancel_outcome == CancelOutcomeKind.ABANDONED
    await finish(run)


async def test_cancel_is_idempotent_and_resume_of_running_is_invalid_state(rig, dev):
    dev()
    run, task = await start(rig, "monitor.watch")
    with pytest.raises(TaskControlError) as e:
        await rig.tm.resume(task.id)
    assert e.value.code == "invalid_state"
    await rig.tm.cancel(task.id)
    again = await rig.tm.cancel(task.id)
    assert again.status == TaskStatus.CANCELLED
    await finish(run)


async def test_route_maps_refusals_to_409_with_code(rig, dev):
    dev()
    run, task = await start(rig, "notification.send")
    orch = type("O", (), {"task_manager": rig.tm})()
    with pytest.raises(HTTPException) as e:
        await tasks_routes.pause_task(task.id, user_id=task.user_id, orchestrator=orch)
    assert e.value.status_code == 409 and e.value.detail["code"] == "control_not_supported"
    await rig.tm.cancel(task.id)
    await finish(run)
