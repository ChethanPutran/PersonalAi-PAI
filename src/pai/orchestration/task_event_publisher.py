"""Event bus (task.* / dag.*) -> the per-user task event log behind ``GET /tasks/events``.

Each event is appended to its OWNER's log only. An event without a ``user_id`` is dropped
(logged at debug): there is nobody it may be shown to, and guessing would leak across users.
Task payloads are serialized exactly like the REST API's task objects, so the app can replace a
list row straight from an event. High-frequency ``task.stream`` frames are not recorded.
"""
from __future__ import annotations

from typing import List

from loguru import logger
from pydantic import BaseModel

from pai.event_bus.bus import EventBus
from pai.event_bus.events import Event, EventType
from pai.event_bus.task_log import TaskEventLog
from pai.tasks.serialization import serialize_task

PATTERNS: tuple[str, ...] = ("task.*", "dag.*")
_SKIPPED = {EventType.TASK_STREAM.value, EventType.TASK_CONTROL_ACK.value}


class PublisherStats(BaseModel):
    recorded: int = 0
    dropped_no_owner: int = 0
    failed: int = 0


class TaskEventPublisher:
    def __init__(self, *, event_bus: EventBus, log: TaskEventLog) -> None:
        self._bus, self._log = event_bus, log
        self._subs: List[str] = []
        self.stats = PublisherStats()

    async def start(self) -> None:
        for pattern in PATTERNS:
            self._subs.append(await self._bus.subscribe(pattern, self._on_event))

    async def stop(self) -> None:
        for sub_id in self._subs:
            try:
                await self._bus.unsubscribe(sub_id)
            except Exception:
                pass
        self._subs.clear()

    async def _on_event(self, event: Event) -> None:
        name = getattr(event.event_type, "value", str(event.event_type))
        if name in _SKIPPED:
            return
        user_id = event.data.get("user_id")
        if not user_id:
            self.stats.dropped_no_owner += 1
            logger.debug("task event {} has no owner; not recorded", name)
            return
        try:
            data = dict(event.data)
            if name.startswith("task.") and data.get("id") and data.get("status") is not None:
                data = serialize_task(data)
            await self._log.append(str(user_id), name, data)
            self.stats.recorded += 1
        except Exception as exc:        # never break the bus for a log failure
            self.stats.failed += 1
            logger.warning("task event log append failed for {}: {}", name, exc)
