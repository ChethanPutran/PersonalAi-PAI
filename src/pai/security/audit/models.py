from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Mapping

from pai.memory.models import utc_now

class AuditEventType(StrEnum):
    """Security-relevant audit event types."""

    AUTHORIZATION = "authorization"
    PLUGIN_ENABLED = "plugin_enabled"
    PLUGIN_DISABLED = "plugin_disabled"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"

    DEVICE_REMOTE_ACCESS_CHANGED = "device_remote_access_changed"
    REMOTE_INSTALL_REQUESTED = "remote_install_requested"
    REMOTE_INSTALL_DENIED = "remote_install_denied"

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


    SCHEDULE_RUN = "schedule_run"
    SCHEDULE_SKIPPED = "schedule_skipped"
    SCHEDULE_DENIED = "schedule_denied"
    SCHEDULE_FAILED = "schedule_failed"
    SCHEDULE_CONSENT_GRANTED = "schedule_consent_granted"
    SCHEDULE_CONSENT_REVOKED = "schedule_consent_revoked"

    CONSENT_GRANTED = "consent_granted"            # a session/always grant was created
    CONSENT_REVOKED = "consent_revoked"
    CONSENT_AUTO_APPROVED = "consent_auto_approved"  # a run proceeded without a prompt (grant / admin)
    CONSENT_APPROVED_ONCE = "consent_approved_once"
    PREFLIGHT_DECISION = "preflight_decision"

    SECURITY_CONFIGURATION_CHANGED = "security_configuration_changed"
    ADMIN_ACTION = "admin_action"

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