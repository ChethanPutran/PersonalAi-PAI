from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4
import uuid
from .models import AuditEvent, AuditEventType
from loguru  import logger

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
        self,repository, logger_instance=None
    ) -> None:
        self._logger = logger_instance or logger
        self.repository = repository

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
            subject=user_id,
            action=capability,
            resource=plugin_id,
            effect=allowed,
            risk_level=None,
            reason=reason,
            policies=[],
            request_id=str(uuid.uuid4()),
            permission=None,
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            device_id=device_id,
            task_id=task_id,
            metadata=metadata or {},
        )
        # record() used to only log: execution/authorization events never reached the repository.
        if self.repository is not None:
            self.repository.append(event)

        self._logger.info(
            "AUDIT event=%s user=%s plugin=%s capability=%s "
            "decision=%s device=%s reason=%s",
            event.event_type.value,
            event.user_id,
            event.plugin_id,
            event.capability,
            event.effect,
            event.device_id,
            event.reason,
        )

        return event

    def log(self, request, decision) -> AuditEvent:
        """Record the outcome of an AuthorizationRequest (new authorization stack)."""
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=AuditEventType.AUTHORIZATION,
            subject=request.subject.key,
            action=request.action.value,
            permission=request.permission,
            resource=request.resource.uri,
            request_id=request.request_id,
            effect=decision.effect.value,
            reason=decision.reason,
            risk_level=str(decision.risk_level),
            policies=list(decision.policy_ids),
            user_id=request.user_id,
            plugin_id=request.plugin_id,
            capability=request.capability,
            device_id=request.target_device_id,
            task_id=request.task_id,
            # Redacted view only: raw parameters may contain secrets/PII.
            metadata={"obligations": list(decision.obligations), "request": request.to_audit_dict()},
        )
        if self.repository is not None:
            self.repository.append(event)
        self._logger.info(
            "AUDIT authz request={} subject={} permission={} effect={} reason={}",
            event.request_id, event.subject, event.permission, event.effect, event.reason,
        )
        return event

    def consent_event(
        self,
        event_type: AuditEventType,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        reason: str,
        device_id: str | None = None,
        task_id: str | None = None,
        allowed: bool | None = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        """Record a consent decision (grant / revoke / auto-approval / once). ``metadata`` carries
        scope, binding and grant id — never raw parameter values."""
        return self.record(
            event_type, user_id=user_id, plugin_id=plugin_id, capability=capability,
            allowed=allowed, reason=reason, device_id=device_id, task_id=task_id, metadata=metadata)

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