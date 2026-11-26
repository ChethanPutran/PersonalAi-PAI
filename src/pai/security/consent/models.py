from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from pai.common.enums.permission import ConsentScope
from pai.security.consent.binding import DEFAULT_BINDING, ParamBinding
from pai.utils.helpers import get_date


@dataclass
class ConsentRecord:
    """The pipeline's answer to "is this permission consented?" (one per permission check).

    Legacy permission-level consents are stored as these. A capability/parameter-bound GRANT
    (``ConsentGrant``) that covers the call is surfaced to the pipeline as an active record with
    ``grant_id`` set, so ApprovalPolicy / DefaultDeny work unchanged.
    """
    subject: str
    plugin_id: str
    permission: str
    granted: bool
    expires_at: datetime | None = None
    approval_required: bool = False
    created_at: datetime = field(default_factory=get_date)
    grant_id: Optional[str] = None            # set when a ConsentGrant satisfied the check

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)

    def active(self):
        return self.granted and (self.expires_at is None or datetime.now(timezone.utc) < self.expires_at)


def _aware(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)        # SQLite returns naive datetimes; we store UTC
    return v


class ParamPreview(BaseModel):
    """One parameter as shown to the person approving it (secrets already masked)."""
    name: str
    value_preview: str


class ConsentGrant(BaseModel):
    """A standing approval: "this user allowed <capability> on <device>" for a scope + binding.

    Always bound to (user_id, plugin_id, capability, device_id). SESSION grants are also bound
    to one session_id. The parameter binding decides how much of the call's parameters must
    match. ONCE approvals are never stored.
    """
    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    plugin_id: str
    capability: str
    device_id: str
    scope: ConsentScope                            # SESSION | ALWAYS
    session_id: Optional[str] = None
    binding: ParamBinding = DEFAULT_BINDING
    params_hash: str = ""                          # '' for binding 'any'
    params_preview: List[ParamPreview] = Field(default_factory=list)
    risk: str = "low"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    granted_by: str
    last_used_at: Optional[datetime] = None
    use_count: int = 0
    revoked_at: Optional[datetime] = None

    def is_active(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.revoked_at is None and _aware(self.expires_at) > now

    def covers(
        self, *, user_id: str, plugin_id: str, capability: str, device_id: str,
        session_id: Optional[str], parameters: Optional[dict], now: Optional[datetime] = None,
    ) -> bool:
        """Would this grant approve exactly this call right now? (the ONE matching rule)"""
        if not self.is_active(now):
            return False
        if (self.user_id, self.plugin_id, self.capability, self.device_id) != (
                user_id, plugin_id, capability, device_id):
            return False
        if self.scope == ConsentScope.SESSION and (not self.session_id or self.session_id != session_id):
            return False
        return self.binding.matches(self.params_hash, parameters)


class ConfirmationInfo(BaseModel):
    """What the person is asked to approve, and the choices the SERVER allows them."""
    capability: str
    plugin_id: str
    device_id: str
    risk: str
    parameters_summary: List[ParamPreview] = Field(default_factory=list)
    allowed_scopes: List[ConsentScope] = Field(default_factory=lambda: [ConsentScope.ONCE])
    can_bind_params: bool = False
    default_binding: ParamBinding = DEFAULT_BINDING
    # Parameters any remembered approval must be tied to (shell: the command). Clients must not offer 'any'.
    required_binding_keys: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class GrantRequest(BaseModel):
    """Body of POST /tasks/{id}/confirm (all optional; empty body = approve once)."""
    model_config = ConfigDict(extra="forbid")

    scope: ConsentScope = ConsentScope.ONCE
    binding: ParamBinding = DEFAULT_BINDING
    days: Optional[int] = None                     # ALWAYS only; default 90, max 365


class PreflightDecision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class PreflightResult(BaseModel):
    decision: PreflightDecision
    reason: str = ""
    confirmation: Optional[ConfirmationInfo] = None


class ConsentError(Exception):
    """A consent request the server refuses. ``status`` is the HTTP status to use."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
