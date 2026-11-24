"""Standing consent for scheduled actions.

A schedule that needs approval (see ScheduleValidator.preflight) may run unattended ONLY while
it holds a valid standing consent. A consent is:

  * per schedule and BOUND to exactly what the user approved — the sha256 of
    (user, capability, canonical parameters, target device). Editing any of those makes the
    hash differ, so the consent silently stops being valid (and is cleared on the edit);
  * time-limited (default 30 days, at most 90) and revocable;
  * granted only through the authenticated REST endpoint. Nothing a user types or says to
    the assistant can reach this module's ``grant``.

This module is pure (no I/O) so it can be tested exhaustively.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping, Optional

from pydantic import BaseModel

# One definition of "same parameters", shared with interactive consent grants.
from pai.security.consent.binding import canonical_parameters  # noqa: F401  (re-exported)

DEFAULT_CONSENT_DAYS = 30
MAX_CONSENT_DAYS = 90


class ConsentReason(StrEnum):
    VALID = "valid"
    NEVER_GRANTED = "never_granted"
    EXPIRED = "expired"
    BINDING_CHANGED = "binding_changed"      # the schedule was edited after approval


class ConsentState(BaseModel):
    """Public view of a schedule's consent (the REST ``consent`` object)."""
    active: bool = False
    granted_at: Optional[str] = None
    expires_at: Optional[str] = None
    reason: ConsentReason = ConsentReason.NEVER_GRANTED


def binding_hash(
    *, user_id: str, capability: str, parameters: Mapping[str, Any] | None,
    target_device_id: Optional[str],
) -> str:
    payload = json.dumps(
        {"u": user_id, "c": capability, "p": canonical_parameters(parameters),
         "d": target_device_id or ""},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aware(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)      # SQLite returns naive; we stored UTC
    return v


def row_binding(row: Any) -> str:
    return binding_hash(user_id=row.user_id, capability=row.capability,
                        parameters=row.parameters, target_device_id=row.target_device_id)


def evaluate(row: Any, now: Optional[datetime] = None) -> ConsentState:
    """Is this schedule's consent valid right now, and if not, why?"""
    now = now or datetime.now(timezone.utc)
    granted, expires = _aware(row.consent_granted_at), _aware(row.consent_expires_at)
    iso = lambda d: d.isoformat() if d else None          # noqa: E731
    if not row.consent_binding_hash or granted is None or expires is None:
        return ConsentState(active=False, reason=ConsentReason.NEVER_GRANTED)
    shown = dict(granted_at=iso(granted), expires_at=iso(expires))
    if row.consent_binding_hash != row_binding(row):
        return ConsentState(active=False, reason=ConsentReason.BINDING_CHANGED, **shown)
    if expires <= now:
        return ConsentState(active=False, reason=ConsentReason.EXPIRED, **shown)
    return ConsentState(active=True, reason=ConsentReason.VALID, **shown)


def grant(row: Any, *, by_user_id: str, days: int = DEFAULT_CONSENT_DAYS,
          now: Optional[datetime] = None) -> None:
    if not 1 <= days <= MAX_CONSENT_DAYS:
        raise ValueError(f"consent days must be between 1 and {MAX_CONSENT_DAYS}")
    now = now or datetime.now(timezone.utc)
    row.consent_granted_at = now
    row.consent_expires_at = now + timedelta(days=days)
    row.consent_granted_by = by_user_id
    row.consent_binding_hash = row_binding(row)


def revoke(row: Any) -> None:
    row.consent_granted_at = row.consent_expires_at = None
    row.consent_granted_by = row.consent_binding_hash = None


def revoke_if_binding_changed(row: Any) -> bool:
    """Call after editing a schedule: drops the consent if it no longer matches. True if dropped."""
    if row.consent_binding_hash and row.consent_binding_hash != row_binding(row):
        revoke(row)
        return True
    return False
