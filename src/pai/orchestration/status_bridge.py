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
from pai.event_bus.events import Event


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
            "plan.created", self._on_plan_created,
        ))
        self._subs.append(await self._bus.subscribe(
            "plan.completed", self._on_plan_completed,
        ))
        self._subs.append(await self._bus.subscribe(
            "plan.failed", self._on_plan_failed,
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

    async def _on_plan_created(self, event: Event) -> None:
        session_id = event.data.get("session_id")
        if not session_id:
            return
        total = int(event.data.get("task_count", 0) or 0)
        self._progress[session_id] = (0, total)
        await self._bus.publish(
            "chat.status",
            {
                "session_id": session_id,
                "request_id": event.data.get("request_id", ""),
                "stage": "planned",
                "message": f"Plan ready: {total} step(s)",
            },
            source="status_bridge",
            distributed=False,
        )

    async def _on_plan_completed(self, event: Event) -> None:
        session_id = event.data.get("session_id")
        if not session_id:
            return
        self._progress.pop(session_id, None)
        await self._bus.publish(
            "chat.status",
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
            "chat.status",
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
        device_id = event.data.get("device_id")
        if not device_id or self._dm is None:
            return
        try:
            await self._dm.send(device_id, {
                "type": event.event_type,
                **event.data,
            })
        except Exception:
            # Device offline — best-effort. TasksScreen polls as a
            # fallback so the user still sees fresh state.
            pass