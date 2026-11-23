"""DAG -> TaskManager -> ExecutorScheduler -> DeviceExecutor -> DeviceManager -> device,
with the device replying through the real route dispatcher."""
import asyncio
import time

from pai.common.enums.permission import Origin
from pai.orchestration.execution_context import ExecutionContext
from rig import DEVICE, device_sends, fake_device, frame_dict, rig, spec  # noqa: F401


async def test_dag_over_device_runs_sequentially_with_realtime_events(rig):
    invoked = []

    async def device(frame):
        frame = frame_dict(frame)
        if frame["type"] == "invoke":
            invoked.append(frame["task_id"])
            await asyncio.sleep(.05)
            await device_sends(rig, type="result", request_id=frame["request_id"], result={"ok": True})

    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, device, stop))
    ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)
    out = await asyncio.wait_for(rig.dag.execute([spec("a"), spec("b", ["a"]), spec("c")], ctx), 10)
    await asyncio.sleep(.2)
    stop.set(); await dev

    assert out.status == "completed", [getattr(r, "error", r) for r in out.results]
    assert len(invoked) == 3
    names = [n for n, _ in rig.events]
    for needed in ("dag.started", "task.created", "task.queued", "task.started",
                   "task.completed", "dag.task_finished", "dag.completed"):
        assert needed in names, (needed, names)
    assert "task.failed" not in names
    sessions = [d.get("session_id") for n, d in rig.events if n.startswith(("task.", "dag."))]
    assert all(s == "s" for s in sessions), sessions


async def test_device_failure_result_fails_task_and_skips_dependent(rig):
    async def device(frame):
        frame = frame_dict(frame)
        if frame["type"] == "invoke":
            await device_sends(rig, type="result", request_id=frame["request_id"],
                               success=False, error="camera busy")

    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, device, stop))
    ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)
    out = await asyncio.wait_for(rig.dag.execute([spec("a"), spec("b", ["a"])], ctx), 10)
    stop.set(); await dev
    assert out.status == "failed" and out.failed == ["a"] and out.skipped == ["b"]
