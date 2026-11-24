from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from pai.common.enums.permission import (
    Origin,
    PermissionAction,
    PermissionResource,
    PrincipalType,
)


@dataclass(frozen=True)
class Actor:
    """A principal taking part in a request: a user, agent, plugin, task, ..."""

    type: PrincipalType
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", PrincipalType(self.type))
        if not self.id or not str(self.id).strip():
            raise ValueError("Actor.id must be non-empty")

    @property
    def key(self) -> str:
        return f"{self.type.value}:{self.id}"


@dataclass(frozen=True)
class ResourceRef:
    """What is being acted on: a resource *type* plus an optional instance id.

    Examples: (FILESYSTEM, "/workspace/a.txt"), (CAMERA, "front"),
    (NETWORK, "https://api.example.com/x"), (SHELL, None).
    """

    type: PermissionResource
    id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", PermissionResource(self.type))
        if self.id is not None and not str(self.id).strip():
            raise ValueError("ResourceRef.id must be non-empty when given")

    @property
    def uri(self) -> str:
        return f"{self.type.value}:{self.id}" if self.id is not None else self.type.value

    def __str__(self) -> str:
        return self.uri


_SENSITIVE_KEY = re.compile(
    r"(token|password|passwd|secret|api[_-]?key|authorization|credential|cookie"
    r"|private[_-]?key|body|message|content|text|prompt)",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"


def redact(value: Any) -> Any:
    """Recursively redact values whose *key* looks sensitive."""
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(v) for v in value]
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AuthorizationRequest:
    """One question to the authorization engine: *may this actor do this?*

    Identity
        subject:        the immediate caller (e.g. the plugin).
        on_behalf_of:   the delegation chain behind it, e.g. (user, agent, task).
                        The user's identity is therefore never lost when a plugin
                        acts for a task acting for a user.
    Intent
        action:         what is being done (``PermissionAction``).
        resource:       what it is done to (``ResourceRef``).
        permission:     DERIVED, ``"<resource.type>.<action>"`` - cannot disagree
                        with action/resource.
    References
        capability:     the capability being invoked, if any.
        source_device_id / target_device_id: where it came from / is going.
    Data
        parameters:     operational arguments (untrusted; read-only view).
        context:        TRUSTED server-side policy context (filesystem_roots,
                        network_hosts, allowed_devices ...). Never from the LLM.
        metadata:       free-form extras for policies.
    Execution context
        origin:         user-direct / agent-autonomous / scheduled / plugin.
                        Defaults to the most restrictive (agent-autonomous).
        session_id, trace_id, requested_at, request_id: tracing & audit.

    plugin_id / task_id / user_id / agent_id are derived from the actor chain.
    All mappings are frozen so what was authorized cannot be mutated afterwards.
    """

    subject: Actor
    action: PermissionAction
    resource: ResourceRef
    on_behalf_of: tuple[Actor, ...] = ()

    capability: str | None = None
    source_device_id: str | None = None
    target_device_id: str | None = None

    parameters: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    origin: Origin = Origin.AGENT_AUTONOMOUS
    session_id: str | None = None
    trace_id: str | None = None
    requested_at: datetime = field(default_factory=_utcnow)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.subject, Actor):
            raise TypeError("subject must be an Actor")
        if not isinstance(self.resource, ResourceRef):
            raise TypeError("resource must be a ResourceRef")
        chain = tuple(self.on_behalf_of)
        if not all(isinstance(a, Actor) for a in chain):
            raise TypeError("on_behalf_of must contain Actors")
        object.__setattr__(self, "on_behalf_of", chain)
        object.__setattr__(self, "action", PermissionAction(self.action))
        object.__setattr__(self, "origin", Origin(self.origin))
        if not self.request_id:
            raise ValueError("request_id must be non-empty")
        for name in ("parameters", "context", "metadata"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    # ---- derived identity -------------------------------------------------

    @property
    def actors(self) -> tuple[Actor, ...]:
        return (self.subject, *self.on_behalf_of)

    def _first(self, kind: PrincipalType) -> str | None:
        return next((a.id for a in self.actors if a.type == kind), None)

    @property
    def user_id(self) -> str | None:
        return self._first(PrincipalType.USER)

    @property
    def plugin_id(self) -> str | None:
        return self._first(PrincipalType.PLUGIN)

    @property
    def task_id(self) -> str | None:
        return self._first(PrincipalType.TASK)

    @property
    def agent_id(self) -> str | None:
        return self._first(PrincipalType.AGENT)

    @property
    def principal_key(self) -> str:
        """The principal whose authority this is: the user if any, else the subject.

        Consent grants are keyed on this.
        """
        user = self.user_id
        return f"user:{user}" if user else self.subject.key

    # ---- derived intent ---------------------------------------------------

    @property
    def permission(self) -> str:
        return f"{self.resource.type.value}.{self.action.value}"

    @property
    def device_id(self) -> str | None:
        return self.target_device_id

    # ---- audit ------------------------------------------------------------

    def to_audit_dict(self) -> dict[str, Any]:
        """JSON-safe, redacted view for audit logs (no secrets / message bodies)."""
        return {
            "request_id": self.request_id,
            "subject": self.subject.key,
            "on_behalf_of": [a.key for a in self.on_behalf_of],
            "action": self.action.value,
            "resource": self.resource.uri,
            "permission": self.permission,
            "capability": self.capability,
            "source_device_id": self.source_device_id,
            "target_device_id": self.target_device_id,
            "origin": self.origin.value,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "requested_at": self.requested_at.isoformat(),
            "parameters": redact(self.parameters),
            "context": redact(self.context),
            "metadata": redact(self.metadata),
        }
