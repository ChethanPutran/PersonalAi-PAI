from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from pai.security.permissions import (
    Permission,
    PermissionSet,
    SENSITIVE_PERMISSIONS,
)


class RiskLevel(str, Enum):
    """Risk classification for plugin capabilities."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuthorizationDecision(str, Enum):
    """Result of an authorization evaluation."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class AuthorizationRequest:
    """
    Everything required to make one authorization decision.
    """

    user_id: str
    plugin_id: str
    capability: str

    required_permissions: frozenset[Permission] = frozenset()

    risk: RiskLevel = RiskLevel.LOW

    device_id: str | None = None


@dataclass(frozen=True)
class AuthorizationResult:
    """
    Result of an authorization decision.
    """

    decision: AuthorizationDecision
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == AuthorizationDecision.ALLOW


class SecurityPolicy:
    """
    Base security policy.

    A policy should answer:

        Is this operation allowed?

    It should NOT execute the operation.
    """

    def evaluate(
        self,
        request: AuthorizationRequest,
        *,
        user_permissions: PermissionSet,
    ) -> AuthorizationResult:
        raise NotImplementedError


class DefaultSecurityPolicy(SecurityPolicy):
    """
    Default authorization policy for PAI.

    Rules:

    1. The user must have every permission required by the plugin.
    2. Sensitive operations must have their required permission.
    3. Critical operations are denied by default.
    """

    def evaluate(
        self,
        request: AuthorizationRequest,
        *,
        user_permissions: PermissionSet,
    ) -> AuthorizationResult:

        if request.risk == RiskLevel.CRITICAL:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason="Critical operations are denied by the default policy.",
            )

        missing_permissions = [
            permission.value
            for permission in request.required_permissions
            if not user_permissions.contains(permission)
        ]

        if missing_permissions:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason=(
                    "Missing required permissions: "
                    + ", ".join(sorted(missing_permissions))
                ),
            )

        if (
            request.risk in {RiskLevel.HIGH, RiskLevel.MEDIUM}
            and request.required_permissions & SENSITIVE_PERMISSIONS
        ):
            # At this stage explicit user permission is represented by
            # membership in user_permissions.
            return AuthorizationResult(
                decision=AuthorizationDecision.ALLOW,
                reason="Required sensitive permissions are granted.",
            )

        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason="Operation satisfies the default security policy.",
        )


class PolicyEngine:
    """
    Evaluates authorization policies.

    Multiple policies can eventually be composed here.
    """

    def __init__(
        self,
        policies: Iterable[SecurityPolicy] | None = None,
    ) -> None:
        self._policies = list(
            policies or [DefaultSecurityPolicy()]
        )

    def evaluate(
        self,
        request: AuthorizationRequest,
        *,
        user_permissions: PermissionSet,
    ) -> AuthorizationResult:
        """
        Evaluate all policies.

        Fail closed:
        if any policy denies the operation, the final result is DENY.
        """

        for policy in self._policies:
            result = policy.evaluate(
                request,
                user_permissions=user_permissions,
            )

            if not result.allowed:
                return result

        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason="All security policies allowed the operation.",
        )