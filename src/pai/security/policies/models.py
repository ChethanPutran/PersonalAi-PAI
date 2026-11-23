from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"
    NOT_APPLICABLE = "not_applicable"

@dataclass
class PolicyResult:
    policy_id: str
    effect: PolicyEffect
    reason: str
    obligations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class Policy:
    """
    Base security policy.

    A policy should answer:

        Is this operation allowed?

    It should NOT execute the operation.
    """
    policy_id: str
    priority: int = 100
    enabled: bool = True
    evaluator: Any = None


    @staticmethod
    def evaluate(ctx):
        raise NotImplementedError