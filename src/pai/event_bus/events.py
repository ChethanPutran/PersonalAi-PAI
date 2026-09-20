from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4


class EventType(str, Enum):
    """Built-in PAI event types."""

    # Tasks
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"

    # Memory
    MEMORY_UPDATED = "memory.updated"
    MEMORY_CREATED = "memory.created"
    MEMORY_DELETED = "memory.deleted"

    # Plugins
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_STARTED = "plugin.started"
    PLUGIN_STOPPED = "plugin.stopped"
    PLUGIN_FAILED = "plugin.failed"

    # Agents
    AGENT_GOAL = "agent.goal"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    # Devices
    DEVICE_CONNECTED = "device.connected"
    DEVICE_DISCONNECTED = "device.disconnected"

    # Execution
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"


@dataclass(slots=True)
class Event:
    """
    Canonical event exchanged inside PAI.

    `event_id`
        Unique identifier for this event.

    `event_type`
        Event subject/type.

    `data`
        Event payload.

    `source`
        Component that generated the event.

    `correlation_id`
        Used to associate related events, especially request/response flows.

    `timestamp`
        UTC creation time.
    """

    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)

    source: Optional[str] = None
    correlation_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the event into a JSON-compatible dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "data": self.data,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Event":
        """Create an Event from a serialized dictionary."""

        timestamp = payload.get("timestamp")

        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        return cls(
            event_id=payload.get("event_id", str(uuid4())),
            event_type=str(
                payload.get("event_type")
                or payload.get("type")
            ),
            data=dict(payload.get("data") or {}),
            source=payload.get("source"),
            correlation_id=payload.get("correlation_id"),
            timestamp=timestamp,
        )

    def to_json_dict(self) -> Dict[str, Any]:
        """
        Alias intended for transport serialization.

        Kept separate from `to_dict()` so transport-specific serialization
        can evolve later without changing callers.
        """
        return self.to_dict()


@dataclass(slots=True)
class EventResponse:
    """
    Response associated with an event/request correlation ID.
    """

    correlation_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "data": self.data,
            "source": self.source,
            "success": self.success,
            "error": self.error,
        }