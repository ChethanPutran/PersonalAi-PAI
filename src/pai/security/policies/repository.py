from .models import Policy
from .builtin.default_deny import DefaultDenyPolicy
from .builtin.system_restrictions import SystemRestrictionsPolicy
from .builtin.risk_policy import RiskPolicy
from .builtin.approval_policy import ApprovalPolicy
from .builtin.origin_policy import OriginPolicy
from .builtin.role_policy import RolePolicy
from .rules import RulePolicy

class PolicyRepository:
    def __init__(self, policies=None):
        self._policies = list(policies or [])
    def add(self, policy):
        self._policies.append(policy)
    def all(self):
        return sorted((p for p in self._policies if p.enabled), key=lambda p: p.priority)
    @classmethod
    def with_builtins(cls, *, role_permissions=None, default_role="user", rules=None, origin_policy=True):
        """Builtins + optional RBAC (``role_permissions``) + declarative ``rules`` (dicts)."""
        policies = [SystemRestrictionsPolicy(), RiskPolicy(), ApprovalPolicy(), DefaultDenyPolicy()]
        if origin_policy:
            policies.append(OriginPolicy())
        if role_permissions is not None:
            policies.append(RolePolicy(role_permissions, default_role))
        policies.extend(RulePolicy(r) for r in (rules or []))
        return cls(policies)