"""How broad an approval may be. Enforced server-side; the client's wishes never widen it.

    broadest scope = min( risk default , manifest maxGrantScope , "once" for always-approve actions )

    low / medium  -> ALWAYS      high -> SESSION      critical -> ONCE only

The manifest can only make this STRICTER. Always-approve actions (e.g. camera control,
see ApprovalPolicy.ALWAYS_APPROVE) can never be remembered. Keyboard/mouse control is in that set by
default and leaves it when security.authorization.input_grant_scope is "session".
"""
from __future__ import annotations

from datetime import timedelta
from typing import List, Optional

from pai.common.enums.permission import ConsentScope, RiskLevel

SESSION_TTL = timedelta(hours=8)
DEFAULT_ALWAYS_DAYS = 90
MAX_ALWAYS_DAYS = 365
MAX_ACTIVE_GRANTS_PER_USER = 200

_RISK_MAX = {
    RiskLevel.LOW: ConsentScope.ALWAYS,
    RiskLevel.MEDIUM: ConsentScope.ALWAYS,
    RiskLevel.HIGH: ConsentScope.SESSION,
    RiskLevel.CRITICAL: ConsentScope.ONCE,
}
_ORDER = [ConsentScope.ONCE, ConsentScope.SESSION, ConsentScope.ALWAYS]


def risk_of(value: object) -> RiskLevel:
    try:
        return RiskLevel(getattr(value, "value", value))
    except ValueError:
        return RiskLevel.HIGH            # a risk we do not understand is treated cautiously


def max_scope(risk: object, manifest_max: Optional[ConsentScope] = None,
              always_approve: bool = False) -> ConsentScope:
    broadest = _RISK_MAX[risk_of(risk)]
    if manifest_max is not None and manifest_max.rank < broadest.rank:
        broadest = manifest_max          # only ever stricter
    if always_approve:
        broadest = ConsentScope.ONCE
    return broadest


def allowed_scopes(risk: object, manifest_max: Optional[ConsentScope] = None,
                   always_approve: bool = False) -> List[ConsentScope]:
    limit = max_scope(risk, manifest_max, always_approve)
    return [s for s in _ORDER if s.rank <= limit.rank]
