"""Device-free capabilities for exercising the whole task pipeline from the admin panel.

They go through the NORMAL path (planner/DAG -> TaskManager -> ExecutorScheduler ->
BuiltinExecutor), so every task is a real task row with real events, and kill works for
real: a killed task is cancelled while it is awaiting inside these handlers.

Registered only when ``admin.test_capabilities`` is enabled (see AdminConfig).
All inputs are clamped: even an admin cannot park a task for a day.
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from pai.common.task_controls import CancelMode, TaskControls
from pai.devices.models import DeviceFrame
from pai.common.enums.task import TaskType
from pai.orchestration.builtin_capabilities import BuiltinCapability

MAX_SLEEP_SECONDS = 600.0
# These really check the pause gate, so the demo can show a REAL pause/resume.
PAUSABLE = TaskControls(pause=True, cancel=CancelMode.COOPERATIVE)
MAX_STREAM_FRAMES = 200


class SleepParams(BaseModel):
    seconds: float = Field(default=5.0, ge=0.0, le=MAX_SLEEP_SECONDS)
    label: str = Field(default="", max_length=80)


class EchoParams(BaseModel):
    message: str = Field(default="hello", max_length=2000)


class FailParams(BaseModel):
    after_seconds: float = Field(default=1.0, ge=0.0, le=MAX_SLEEP_SECONDS)
    message: str = Field(default="admin.test.fail: failing on purpose", max_length=500)


class StreamParams(BaseModel):
    frames: int = Field(default=10, ge=1, le=MAX_STREAM_FRAMES)
    interval_ms: int = Field(default=300, ge=10, le=10_000)


class SleepResult(BaseModel):
    slept_seconds: float
    label: str = ""


class EchoResult(BaseModel):
    echo: str


class StreamResult(BaseModel):
    frames: int


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def build_admin_test_capabilities(task_manager: Any) -> List[BuiltinCapability]:
    async def _sleep(params: dict, *, user_id: Optional[str] = None, pause: Any = None, **_: Any) -> dict:
        p = SleepParams.model_validate(params or {})
        remaining = p.seconds
        while remaining > 0:
            if pause is not None:
                await pause.checkpoint()               # holds here while the user has it paused
            step = min(0.2, remaining)
            await asyncio.sleep(step)                  # cancellable: this is what "kill" interrupts
            remaining -= step                          # paused time is not counted
        return SleepResult(slept_seconds=p.seconds, label=p.label).model_dump()

    async def _echo(params: dict, *, user_id: Optional[str] = None, **_: Any) -> dict:
        return EchoResult(echo=EchoParams.model_validate(params or {}).message).model_dump()

    async def _fail(params: dict, *, user_id: Optional[str] = None, **_: Any) -> dict:
        p = FailParams.model_validate(params or {})
        await asyncio.sleep(p.after_seconds)
        raise RuntimeError(p.message)

    async def _stream(params: dict, *, user_id: Optional[str] = None, task: Any = None,
                      pause: Any = None, **_: Any) -> dict:
        p = StreamParams.model_validate(params or {})
        task_id = getattr(task, "id", None)
        if task_id:
            await task_manager.on_stream_frame(DeviceFrame(type="stream.start", task_id=task_id))
        for i in range(p.frames):
            if pause is not None:
                await pause.checkpoint()               # paused: no frames are produced
            await asyncio.sleep(p.interval_ms / 1000)
            if task_id:
                await task_manager.on_stream_frame(DeviceFrame(
                    type="stream.frame", task_id=task_id, sequence=i, data=f"frame-{i}"))
        if task_id:
            await task_manager.on_stream_frame(DeviceFrame(type="stream.end", task_id=task_id))
        return StreamResult(frames=p.frames).model_dump()

    return [
        BuiltinCapability("admin.test.sleep", "TEST: sleep for N seconds (cancellable — use it to try kill).",
                          _schema(SleepParams), _sleep, PAUSABLE,
                          task_type=TaskType.LONG_RUNNING),       # runs for N seconds: no deadline
        BuiltinCapability("admin.test.echo", "TEST: return the given message immediately.",
                          _schema(EchoParams), _echo),
        BuiltinCapability("admin.test.fail", "TEST: fail with an error after N seconds.",
                          _schema(FailParams), _fail),
        BuiltinCapability("admin.test.stream", "TEST: emit N stream frames (task.stream events), then finish.",
                          _schema(StreamParams), _stream, PAUSABLE,
                          task_type=TaskType.STREAM),
    ]
