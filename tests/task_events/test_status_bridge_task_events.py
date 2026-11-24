"""Task events reach the owner's devices (all of them), with the event name intact."""
from types import SimpleNamespace

import pytest

from pai.event_bus.events import Event, EventType
from pai.orchestration.status_bridge import StatusBridge, TaskEventFrame


class FakeDM:
    def __init__(self, devices_by_user, connected):
        self.devices_by_user, self.connected, self.sent = devices_by_user, set(connected), []
    async def list_devices(self, user_id):
        return [SimpleNamespace(id=i) for i in self.devices_by_user.get(user_id, [])]
    async def is_connected(self, device_id): return device_id in self.connected
    async def send(self, device_id, payload): self.sent.append((device_id, payload))


def task_event(name="task.started", **data):
    base = {"id": "t1", "type": "one_shot", "name": "Play music", "status": "running",
            "user_id": "u1", "session_id": "schedule:abc", "device_id": "desktop", "capability": "media.control"}
    return Event(event_type=name, data={**base, **data})


async def test_event_name_is_not_overwritten_by_the_task_type():
    dm = FakeDM({"u1": ["phone"]}, {"phone"})
    await StatusBridge(event_bus=None, device_manager=dm)._on_task_event(task_event())
    (dev, frame), = dm.sent
    assert frame["type"] == "task.started"                 # the bug: this used to be "one_shot"
    assert frame["task_type"] == "one_shot"
    assert frame["id"] == "t1" and frame["status"] == "running" and frame["capability"] == "media.control"


async def test_every_connected_device_of_the_owner_gets_it_not_just_the_executor():
    dm = FakeDM({"u1": ["phone", "desktop", "tablet"], "u2": ["other"]}, {"phone", "desktop", "other"})
    await StatusBridge(event_bus=None, device_manager=dm)._on_task_event(task_event())
    assert sorted(d for d, _ in dm.sent) == ["desktop", "phone"]     # tablet offline, u2's never


async def test_stream_frames_are_not_forwarded_to_devices():
    dm = FakeDM({"u1": ["phone"]}, {"phone"})
    await StatusBridge(event_bus=None, device_manager=dm)._on_task_event(task_event("task.stream"))
    assert dm.sent == []


async def test_a_failing_device_does_not_stop_the_others():
    dm = FakeDM({"u1": ["a", "b"]}, {"a", "b"})
    original = dm.send
    async def flaky(device_id, payload):
        if device_id == "a":
            raise RuntimeError("socket closed")
        await original(device_id, payload)
    dm.send = flaky
    await StatusBridge(event_bus=None, device_manager=dm)._on_task_event(task_event())
    assert [d for d, _ in dm.sent] == ["b"]


def test_frame_model_enum_event_types_and_missing_task_type():
    f = TaskEventFrame.from_event(Event(event_type=EventType.TASK_COMPLETED, data={"id": "x"}))
    assert f.type == "task.completed" and f.task_type is None
