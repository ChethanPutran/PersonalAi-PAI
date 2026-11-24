from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any

from pai.common.enums.permission import RiskLevel

class DecisionEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"

@dataclass
class AuthorizationDecision:
    effect: DecisionEffect
    reason: str
    request_id: str
    risk_level: RiskLevel = RiskLevel.LOW
    policy_ids: list[str] = field(default_factory=list)
    obligations: list[str] = field(default_factory=list)
    token: str | None = None
    tokens: list[str] = field(default_factory=list)       # one signed token per permission
    confirmation_reason: str | None = None
    # Set when a standing ConsentGrant (session / always) is what allowed this call; the task
    # manager counts one use per run from this.
    consent_grant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.effect == DecisionEffect.ALLOW