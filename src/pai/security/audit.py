from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
import logging
import uuid


logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class AuditEventType(str, Enum):
    """Security-relevant audit event types."""

    AUTHORIZATION = "authorization"
    PLUGIN_ENABLED = "plugin_enabled"
    PLUGIN_DISABLED = "plugin_disabled"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"


@dataclass(frozen=True)
class AuditEvent:
    """A single security audit event."""

    event_id: str
    event_type: AuditEventType

    user_id: str | None
    plugin_id: str | None
    capability: str | None

    allowed: bool | None = None
    reason: str | None = None

    device_id: str | None = None
    task_id: str | None = None

    timestamp: datetime = field(default_factory=utc_now)

    metadata: Mapping[str, Any] = field(default_factory=dict)


class AuditLogger:
    """
    Security audit logger.

    This is intentionally small for now.

    Later this can write to:
        - database
        - dedicated audit table
        - event stream
        - SIEM
    """

    def __init__(
        self,
        logger_instance: logging.Logger | None = None,
    ) -> None:
        self._logger = logger_instance or logger

    def record(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None = None,
        plugin_id: str | None = None,
        capability: str | None = None,
        allowed: bool | None = None,
        reason: str | None = None,
        device_id: str | None = None,
        task_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        """Create and record an audit event."""

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            allowed=allowed,
            reason=reason,
            device_id=device_id,
            task_id=task_id,
            metadata=metadata or {},
        )

        self._logger.info(
            "AUDIT event=%s user=%s plugin=%s capability=%s "
            "allowed=%s device=%s reason=%s",
            event.event_type.value,
            event.user_id,
            event.plugin_id,
            event.capability,
            event.allowed,
            event.device_id,
            event.reason,
        )

        return event

    def authorization(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        allowed: bool,
        reason: str,
        device_id: str | None = None,
    ) -> AuditEvent:
        """Record an authorization decision."""

        return self.record(
            AuditEventType.AUTHORIZATION,
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            allowed=allowed,
            reason=reason,
            device_id=device_id,
        )

    def execution_started(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        device_id: str | None = None,
        task_id: str | None = None,
    ) -> AuditEvent:
        """Record the beginning of an execution."""

        return self.record(
            AuditEventType.EXECUTION_STARTED,
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            device_id=device_id,
            task_id=task_id,
        )

    def execution_completed(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        device_id: str | None = None,
        task_id: str | None = None,
    ) -> AuditEvent:
        """Record successful execution."""

        return self.record(
            AuditEventType.EXECUTION_COMPLETED,
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            device_id=device_id,
            task_id=task_id,
        )

    def execution_failed(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        reason: str,
        device_id: str | None = None,
        task_id: str | None = None,
    ) -> AuditEvent:
        """Record failed execution."""

        return self.record(
            AuditEventType.EXECUTION_FAILED,
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            reason=reason,
            device_id=device_id,
            task_id=task_id,
        )