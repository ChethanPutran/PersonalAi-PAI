"""E2E: a DAG mixing one-shot, long-running and streaming tasks; the frontend
watches over the real task-status WebSocket route; the device answers through the
real frame dispatcher; user controls (pause/resume/cancel) reach the device."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from pai.api.routes import tasks as tasks_routes
from pai.common.enums.permission import Origin
from pai.orchestration.execution_context import ExecutionContext
from pai.tasks.models import TaskStatus, TaskType
from rig import DEVICE, device_sends, fake_device, frame_dict, rig, spec  # noqa: F401


class FrontendWS:
    """The Flutter app's side of /tasks/{session}/stream."""
    def __init__(self, bus):
        self.query_params = {"token": "t"}
        self.app = SimpleNamespace(state=SimpleNamespace(context=SimpleNamespace(
            orchestrator=SimpleNamespace(event_bus=bus), database=_FakeDB())))
        self.messages = []
        self._gone = asyncio.Event()
    async def accept(self): ...
    async def send_json(self, m): self.messages.append(m)
    async def close(self, code=1000): ...
    async def receive_text(self):
        await self._gone.wait()
        raise WebSocketDisconnect()
    def disconnect(self):
        self._gone.set()


class _FakeDB:
    def session(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): ...
    async def execute(self, stmt): return self
    def scalar_one_or_none(self): return None       # new session: allowed


@pytest.fixture
def frontend(rig, monkeypatch):
    monkeypatch.setattr(tasks_routes, "decode_access_token", lambda t: "u")
    return FrontendWS(rig.bus)


async def _connect(frontend):
    task = asyncio.create_task(tasks_routes.chat_status_stream(frontend, "s"))
    await asyncio.sleep(.1)
    return task


def _device(rig, long_s=.4, stream_frames=3):
    controls = []

    async def handler(frame):
        f = frame_dict(frame)
        if f["type"] == "control":
            controls.append((f["operation"], f["task_id"]))
            await device_sends(rig, type="control_ack", request_id=f["request_id"],
                               operation=f["operation"], status="success")
            return
        if f["type"] != "invoke":
            return
        cap, rid, tid = f["capability"], f["request_id"], f["task_id"]
        if cap == "notification.send":
            await asyncio.sleep(.05)
            await device_sends(rig, type="result", request_id=rid, result={"sent": True})
        elif cap == "monitor.watch":                       # long-running
            await asyncio.sleep(long_s)
            await device_sends(rig, type="result", request_id=rid, result={"samples": 42})
        elif cap == "camera.stream":                       # streaming
            await device_sends(rig, type="stream.start", request_id=rid, task_id=tid)
            for i in range(stream_frames):
                await asyncio.sleep(.03)
                await device_sends(rig, type="stream.frame", request_id=rid, task_id=tid,
                                   sequence=i, data=f"frame-{i}")
            await device_sends(rig, type="stream.end", request_id=rid, task_id=tid, result={"frames": stream_frames})
    return handler, controls


async def test_dag_of_oneshot_longrunning_and_streaming_with_live_frontend(rig, frontend):
    handler, _ = _device(rig)
    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, handler, stop))
    ws_task = await _connect(frontend)

    ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)
    specs = [
        spec("a"),                                                                          # one-shot
        # spec timeout (0.1s) is far shorter than the 0.4s the long task needs -> must be ignored
        spec("b", ["a"], timeout=.1, kind=TaskType.LONG_RUNNING, capability="monitor.watch"),
        spec("c", ["a"], kind=TaskType.STREAM, capability="camera.stream"),
        spec("d", ["b", "c"]),                                                              # joins both
    ]
    out = await asyncio.wait_for(rig.dag.execute(specs, ctx), 15)
    await asyncio.sleep(.3)
    frontend.disconnect(); await asyncio.wait_for(ws_task, 5)
    stop.set(); await dev

    assert out.status == "completed", [getattr(r, "error", r) for r in out.results]
    assert sorted(out.completed) == ["a", "b", "c", "d"]

    msgs = frontend.messages
    kinds = [m["event"] for m in msgs if "event" in m]
    assert msgs[0]["type"] == "connected"
    for needed in ("dag.started", "task.created", "task.started", "task.completed",
                   "dag.task_started", "dag.task_finished", "dag.completed", "task.stream"):
        assert needed in kinds, (needed, kinds)

    # streaming: start, ordered frames, end — all delivered live
    stream = [m for m in msgs if m.get("event") == "task.stream"]
    assert [m["kind"] for m in stream] == ["start", "frame", "frame", "frame", "end"]
    assert [m["sequence"] for m in stream if m["kind"] == "frame"] == [0, 1, 2]

    # dependency order as seen by the frontend: d starts only after b and c finished
    finished = {m["task_id"]: i for i, m in enumerate(msgs) if m.get("event") == "dag.task_finished"}
    started = {m["task_id"]: i for i, m in enumerate(msgs) if m.get("event") == "dag.task_started"}
    assert started["d"] > finished["b"] and started["d"] > finished["c"]
    assert started["b"] > finished["a"] and started["c"] > finished["a"]
    # b and c ran concurrently
    assert started["c"] < finished["b"]

    # nothing leaked from another session / no spurious failure
    assert all(m.get("session_id") in (None, "s") for m in msgs)
    assert "task.failed" not in kinds


async def test_user_can_pause_resume_cancel_long_running_task_and_device_acks(rig, frontend):
    handler, controls = _device(rig, long_s=30)
    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, handler, stop))
    ws_task = await _connect(frontend)
    ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)

    run = asyncio.create_task(rig.dag.execute(
        [spec("b", kind=TaskType.LONG_RUNNING, capability="monitor.watch")], ctx))
    for _ in range(50):
        await asyncio.sleep(.05)
        running = await rig.tm.list(status=TaskStatus.RUNNING)
        if running: break
    task = running[0]

    paused = await rig.tm.pause(task.id)
    assert paused.status == TaskStatus.PAUSED
    resumed = await rig.tm.resume(task.id)
    assert resumed.status == TaskStatus.RUNNING
    cancelled = await rig.tm.cancel(task.id)
    assert cancelled.status == TaskStatus.CANCELLED

    out = await asyncio.wait_for(run, 10)
    await asyncio.sleep(.2)
    frontend.disconnect(); await asyncio.wait_for(ws_task, 5)
    stop.set(); await dev

    assert [op for op, _ in controls] == ["pause", "resume", "cancel"]     # device really got them
    kinds = [m.get("event") for m in frontend.messages]
    for needed in ("task.paused", "task.resumed", "task.cancelled"):
        assert needed in kinds, kinds
    assert out.status == "failed"          # a cancelled task is not a success


async def test_killing_one_dag_task_does_not_affect_independent_tasks(rig, frontend):
    """a (long, killed) | b (one-shot) -> d | c depends on a. Kill a: b and d still complete,
    c is skipped, nothing else is cancelled."""
    handler, controls = _device(rig, long_s=30)
    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, handler, stop))
    ws_task = await _connect(frontend)
    ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)

    specs = [
        spec("a", kind=TaskType.LONG_RUNNING, capability="monitor.watch"),
        spec("b"),
        spec("c", ["a"]),
        spec("d", ["b"]),
        spec("e", kind=TaskType.LONG_RUNNING, capability="monitor.watch"),     # also long; must survive
    ]
    run = asyncio.create_task(rig.dag.execute(specs, ctx))
    for _ in range(60):
        await asyncio.sleep(.05)
        tasks = await rig.tm.list(status=TaskStatus.RUNNING)
        if {t.spec_id for t in tasks} >= {"a", "e"}:
            break
    await asyncio.sleep(.3)                       # let b and d finish

    from pai.planning.dag_models import CancelOutcome
    assert await rig.dag.cancel(ctx.request_id, "a") == CancelOutcome.CANCELLED_RUNNING
    assert await rig.dag.cancel(ctx.request_id, "nope") == CancelOutcome.NOT_FOUND
    assert await rig.dag.cancel(ctx.request_id, "b") == CancelOutcome.ALREADY_FINISHED
    await asyncio.sleep(.2)

    # the other long task is still running, untouched
    still = {t.spec_id: t.status for t in await rig.tm.list()}
    assert still["e"] == TaskStatus.RUNNING and still["a"] == TaskStatus.CANCELLED
    assert still["b"] == TaskStatus.COMPLETED and still["d"] == TaskStatus.COMPLETED

    # finish e normally by killing it too -> DAG ends; b/d completed, a failed, c skipped
    assert await rig.dag.cancel(ctx.request_id, "e") == CancelOutcome.CANCELLED_RUNNING
    out = await asyncio.wait_for(run, 10)
    frontend.disconnect(); await asyncio.wait_for(ws_task, 5)
    stop.set(); await dev

    assert sorted(out.completed) == ["b", "d"]
    assert sorted(out.failed) == ["a", "e"]
    assert out.skipped == ["c"]
    cancels = [(op, tid) for op, tid in controls if op == "cancel"]
    assert len(cancels) == 2                                  # exactly a and e, never b/d


async def test_cancelling_a_pending_dag_task_skips_it_and_its_dependents_only(rig):
    handler, _ = _device(rig, long_s=.5)
    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, handler, stop))
    ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)
    specs = [
        spec("a", kind=TaskType.LONG_RUNNING, capability="monitor.watch"),
        spec("b", ["a"]),            # pending while a runs -> cancel it
        spec("c", ["b"]),            # depends on cancelled b -> skipped
        spec("x"),                   # independent -> completes
    ]
    run = asyncio.create_task(rig.dag.execute(specs, ctx))
    await asyncio.sleep(.2)
    from pai.planning.dag_models import CancelOutcome
    assert await rig.dag.cancel(ctx.request_id, "b") == CancelOutcome.CANCELLED_PENDING
    out = await asyncio.wait_for(run, 10)
    stop.set(); await dev
    assert sorted(out.completed) == ["a", "x"]
    assert sorted(out.skipped) == ["b", "c"] and out.failed == []
