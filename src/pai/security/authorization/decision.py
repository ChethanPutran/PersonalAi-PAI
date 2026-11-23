from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pai.common.enums.permission import RiskLevel

class DecisionEffect(str, Enum):
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
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.effect == DecisionEffect.ALLOW