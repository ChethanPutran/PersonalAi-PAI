"""Wire-protocol enums shared by the backend and (mirrored) the Flutter app.

Every string that crosses the WebSocket between backend and device
should come from one of these enums. They are the single source of
truth for field values — a typo in a `"pause"` string is now a
compile-time error on both sides.
"""
from __future__ import annotations

from enum import Enum


class DeviceMessageType(str, Enum):
    """The `type` field on any frame in either direction."""

    # ── Lifecycle / handshake ──
    CONNECTED = "connected"
    PING = "ping"
    PONG = "pong"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    ERROR = "error"

    # ── Plugin sync ──
    SYNC_PLUGINS_REQUEST = "sync_plugins_request"
    SYNC_PLUGINS_RESPONSE = "sync_plugins_response"

    # ── Capability invocation ──
    INVOKE = "invoke"                  # backend → device
    RESULT = "result"                  # device  → backend
    CONTROL = "control"                # backend → device (pause/resume/cancel)
    CONTROL_ACK = "control_ack"        # device  → backend

    # ── Events (device → backend) ──
    EVENT = "event"

    # ── Task / plan events (backend → device) ──
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_CANCELLED = "task.cancelled"
    PLAN_CREATED = "plan.created"
    PLAN_COMPLETED = "plan.completed"
    PLAN_FAILED = "plan.failed"


class DeviceControlOp(str, Enum):
    """The `op` field on a CONTROL frame — what the backend wants done."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


# Convenience sets used by the WS route to dispatch without listing
# every member inline.
TASK_EVENT_TYPES: frozenset[str] = frozenset({
    DeviceMessageType.TASK_CREATED.value,
    DeviceMessageType.TASK_UPDATED.value,
    DeviceMessageType.TASK_STARTED.value,
    DeviceMessageType.TASK_COMPLETED.value,
    DeviceMessageType.TASK_FAILED.value,
    DeviceMessageType.TASK_PAUSED.value,
    DeviceMessageType.TASK_RESUMED.value,
    DeviceMessageType.TASK_CANCELLED.value,
    DeviceMessageType.PLAN_CREATED.value,
    DeviceMessageType.PLAN_COMPLETED.value,
    DeviceMessageType.PLAN_FAILED.value,
})