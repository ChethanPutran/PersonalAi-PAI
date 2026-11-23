from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pai.memory.models import utc_now

class AuditEventType(str, Enum):
    """Security-relevant audit event types."""

    AUTHORIZATION = "authorization"
    PLUGIN_ENABLED = "plugin_enabled"
    PLUGIN_DISABLED = "plugin_disabled"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"

    AUTHENTICATION_SUCCESS = "authentication_success"
    AUTHENTICATION_FAILURE = "authentication_failure"

    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REVOKED = "permission_revoked"

    POLICY_MATCHED = "policy_matched"
    AUTHORIZATION_ALLOWED = "authorization_allowed"
    AUTHORIZATION_DENIED = "authorization_denied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"

    PLUGIN_TRUST_CHANGED = "plugin_trust_changed"
    PLUGIN_EXECUTION_BLOCKED = "plugin_execution_blocked"


    SECURITY_CONFIGURATION_CHANGED = "security_configuration_changed"

@dataclass(frozen=True)
class AuditEvent:
    """A single security audit event."""
    event_id: str
    event_type: AuditEventType
    subject: str
    action: str
    permission: str
    resource: str
    request_id: str
    effect: str
    reason: str
    risk_level: str
    policies: list[str]
    user_id: str | None
    plugin_id: str | None
    capability: str | None
    device_id: str | None = None
    task_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)