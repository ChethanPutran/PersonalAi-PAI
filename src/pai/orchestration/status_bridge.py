# pai/orchestration/status_bridge.py
"""Domain events → chat.status projection.

Every component emits domain facts (`task.started`, `plan.created`,
`device.connected`). The chat UI wants a *narrative* — "Running 2 tool
tasks", "Composing reply" — not the raw facts. This bridge is the
narrative layer: it subscribes to the facts and emits the narrative.

Adding a new stage to the UI is one `_ROUTES` entry here, not a
refactor of five components.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from loguru import logger

from pai.event_bus.bus import EventBus
from pai.event_bus.events import Event, EventType
from pydantic import BaseModel, ConfigDict

# Per-frame stream events are for the realtime SSE/WS streams, not for TasksScreen: forwarding
# them would make every device reload its task list dozens of times a second.
_NOT_FORWARDED = {EventType.TASK_STREAM, EventType.TASK_STREAM.value}


class TaskEventFrame(BaseModel):
    """The frame a device receives for a task event.

    ``type`` is ALWAYS the event name (e.g. ``task.started``). A task's own data has a ``type``
    too (one_shot / long_running / stream); merging the two dicts used to let the task's value
    overwrite the event name, so clients saw ``type: one_shot`` and ignored every task event.
    The task's own type travels as ``task_type``.
    """
    model_config = ConfigDict(extra="allow")

    type: str
    task_type: str | None = None

    @classmethod
    def from_event(cls, event: Event) -> "TaskEventFrame":
        data = dict(event.data)
        task_type = data.pop("type", None)
        name = getattr(event.event_type, "value", str(event.event_type))
        return cls(type=name, task_type=task_type if isinstance(task_type, str) else None, **data)


class StatusBridge:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        device_manager=None,        # for task.* → device WS forwarding
    ) -> None:
        self._bus = event_bus
        self._dm = device_manager
        self._subs: list[str] = []
        self._progress: Dict[str, tuple[int, int]] = {}

    async def start(self) -> None:
        # Orchestration-level events → chat.status narrative
        self._subs.append(await self._bus.subscribe(
            EventType.PLAN_CREATING, self._on_plan_creating,
        ))
        self._subs.append(await self._bus.subscribe(
            EventType.PLAN_CREATED, self._on_plan_created,
        ))
        self._subs.append(await self._bus.subscribe(
            EventType.PLAN_FAILED, self._on_plan_failed,
        ))

        # Task-level events → device WS (TasksScreen live refresh)
        self._subs.append(await self._bus.subscribe(
            "task.*", self._on_task_event,
        ))

        logger.info(
            "StatusBridge subscribed to {} event streams",
            len(self._subs),
        )

    async def stop(self) -> None:
        for sub_id in self._subs:
            try:
                await self._bus.unsubscribe(sub_id)
            except Exception:
                pass
        self._subs.clear()

    # ------------------------------------------------------------------
    # plan.* → chat.status
    # ------------------------------------------------------------------

    async def _on_plan_creating(self, event: Event) -> None:
        session_id = event.data.get("session_id")
        if not session_id:
            return
        total = int(event.data.get("task_count", 0) or 0)
        self._progress[session_id] = (0, total)
        while len(self._progress) > 1000:                  # entries are removed on completion/failure;
            self._progress.pop(next(iter(self._progress)))  # this bounds the ones that never finish
        await self._bus.publish(
            EventType.CHAT_STATUS,
            {
                "session_id": session_id,
                "request_id": event.data.get("request_id", ""),
                "stage": "planned",
                "message": f"Plan ready: {total} step(s)",
            },
            source="status_bridge",
            distributed=False,
        )

    async def _on_plan_created(self, event: Event) -> None:
        session_id = event.data.get("session_id")
        if not session_id:
            return
        self._progress.pop(session_id, None)
        await self._bus.publish(
            EventType.CHAT_STATUS,
            {
                "session_id": session_id,
                "request_id": event.data.get("request_id", ""),
                "stage": "done",
                "message": "Done",
            },
            source="status_bridge",
            distributed=False,
        )

    async def _on_plan_failed(self, event: Event) -> None:
        session_id = event.data.get("session_id")
        if not session_id:
            return
        self._progress.pop(session_id, None)
        await self._bus.publish(
            EventType.CHAT_STATUS,
            {
                "session_id": session_id,
                "request_id": event.data.get("request_id", ""),
                "stage": "failed",
                "message": "Failed",
            },
            source="status_bridge",
            distributed=False,
        )

    # ------------------------------------------------------------------
    # task.* → device WS
    # ------------------------------------------------------------------

    async def _on_task_event(self, event: Event) -> None:
        """task.* -> every connected device of the task's OWNER (TasksScreen live refresh).

        Not just the executing device: the phone that shows the Tasks screen is usually not
        the device that runs the task (a reminder on the desktop, a photo on the phone...).
        Never sent to other users' devices.
        """
        if self._dm is None or event.event_type in _NOT_FORWARDED:
            return
        frame = TaskEventFrame.from_event(event)
        for device_id in await self._target_devices(event.data):
            try:
                await self._dm.send(device_id, frame.model_dump(mode="json"))
            except Exception:
                # Device went offline between the check and the send: best-effort.
                # (TasksScreen also refreshes on a timer as a fallback.)
                pass

    async def _target_devices(self, data: Dict[str, Any]) -> list[str]:
        user_id = data.get("user_id")
        if not user_id:
            device_id = data.get("device_id")           # legacy events without an owner
            return [device_id] if device_id else []
        try:
            devices = await self._dm.list_devices(user_id)
        except Exception:
            return []
        out: list[str] = []
        for d in devices:
            dev_id = getattr(d, "id", None)
            if dev_id and await self._dm.is_connected(dev_id):
                out.append(dev_id)
        return out
