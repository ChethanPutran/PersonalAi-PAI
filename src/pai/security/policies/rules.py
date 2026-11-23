"""Declarative, config-driven policies (``security.authorization.rules``)."""
from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from .models import Policy, PolicyEffect, PolicyResult

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _any_match(patterns, value) -> bool:
    return value is not None and any(fnmatchcase(str(value), str(p)) for p in patterns)


class RulePolicy(Policy):
    """One rule. Every populated field must match (AND); lists are OR with
    glob patterns. An empty field is a wildcard. Matching rules yield the
    rule's effect, otherwise NOT_APPLICABLE.

    Fields: permissions ("camera.*"), capabilities, plugins, origins, roles,
    devices, users, min_risk. Match inputs come only from the trusted
    request/ctx, never from LLM parameters.
    """

    def __init__(self, rule: dict[str, Any]):
        rid = rule.get("id") or "rule"
        super().__init__(f"rule:{rid}", priority=int(rule.get("priority", 50)), evaluator=self.evaluate)
        self.rule = rule
        self.effect = PolicyEffect(rule["effect"])
        self.reason = rule.get("reason") or f"Matched policy rule '{rid}'"
        if self.effect == PolicyEffect.NOT_APPLICABLE:
            raise ValueError(f"rule '{rid}': effect must be allow/deny/approval_required")

    def evaluate(self, ctx):
        r, req = self.rule, ctx.request
        roles = req.context.get("user_roles") or []
        checks = [
            (r.get("permissions"), lambda: _any_match(r["permissions"], req.permission)),
            (r.get("capabilities"), lambda: _any_match(r["capabilities"], req.capability)),
            (r.get("plugins"), lambda: _any_match(r["plugins"], req.plugin_id)),
            (r.get("origins"), lambda: _any_match(r["origins"], req.origin.value)),
            (r.get("devices"), lambda: _any_match(r["devices"], req.target_device_id)),
            (r.get("users"), lambda: _any_match(r["users"], req.user_id)),
            (r.get("roles"), lambda: any(_any_match(r["roles"], x) for x in roles)),
        ]
        for configured, matches in checks:
            if configured and not matches():
                return PolicyResult(self.policy_id, PolicyEffect.NOT_APPLICABLE, "Rule did not match")
        min_risk = r.get("min_risk")
        if min_risk:
            level = str(getattr(ctx.risk, "level", "low"))
            if _RISK_ORDER.get(level, 3) < _RISK_ORDER[str(min_risk)]:
                return PolicyResult(self.policy_id, PolicyEffect.NOT_APPLICABLE, "Risk below rule threshold")
        obligations = ["human_approval"] if self.effect == PolicyEffect.APPROVAL_REQUIRED else []
        return PolicyResult(self.policy_id, self.effect, self.reason, obligations)
