from __future__ import annotations

from pai.security.audit import AuditLogger
from pai.security.permissions import Permission, PermissionSet
from pai.security.policies import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationResult,
    PolicyEngine,
    RiskLevel,
)
from pai.storage.repositories.plugin import UserPluginRepository


class SecurityManager:
    """
    Central authorization facade.

    Responsibilities:
        1. Check user plugin state.
        2. Build an authorization request.
        3. Evaluate security policies.
        4. Audit the decision.

    It does NOT:
        - execute plugins
        - select devices
        - start/stop plugins
        - plan tasks
    """

    def __init__(
        self,
        *,
        user_plugin_repository: UserPluginRepository,
        policy_engine: PolicyEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.user_plugin_repository = user_plugin_repository

        self.policy_engine = (
            policy_engine
            or PolicyEngine()
        )

        self.audit_logger = (
            audit_logger
            or AuditLogger()
        )

    async def authorize(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        required_permissions: set[Permission] | None = None,
        risk: RiskLevel = RiskLevel.LOW,
        device_id: str | None = None,
    ) -> AuthorizationResult:
        """
        Authorize a capability execution.

        Authorization sequence:

            User
              ↓
            Plugin enabled?
              ↓
            Required permissions
              ↓
            Security policies
              ↓
            Audit
        """

        # ---------------------------------------------------------
        # 1. Check user plugin state
        # ---------------------------------------------------------

        plugin_enabled = await self.user_plugin_repository.is_enabled(
            user_id=user_id,
            plugin_id=plugin_id,
        )

        if not plugin_enabled:
            result = AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason="Plugin is not enabled for this user.",
            )

            self.audit_logger.authorization(
                user_id=user_id,
                plugin_id=plugin_id,
                capability=capability,
                allowed=False,
                reason=result.reason,
                device_id=device_id,
            )

            return result

        # ---------------------------------------------------------
        # 2. Build authorization request
        # ---------------------------------------------------------

        request = AuthorizationRequest(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            required_permissions=frozenset(
                required_permissions or set()
            ),
            risk=risk,
            device_id=device_id,
        )

        # ---------------------------------------------------------
        # 3. Evaluate policies
        # ---------------------------------------------------------

        # In the current implementation, user permissions are derived
        # from the permissions explicitly granted to the request.
        #
        # This will later be replaced by persistent user/device
        # permission grants from the security subsystem.
        user_permissions = PermissionSet(
            permissions=frozenset(
                required_permissions or set()
            )
        )

        result = self.policy_engine.evaluate(
            request,
            user_permissions=user_permissions,
        )

        # ---------------------------------------------------------
        # 4. Audit
        # ---------------------------------------------------------

        self.audit_logger.authorization(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            allowed=result.allowed,
            reason=result.reason,
            device_id=device_id,
        )

        return result

    async def require_authorization(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        required_permissions: set[Permission] | None = None,
        risk: RiskLevel = RiskLevel.LOW,
        device_id: str | None = None,
    ) -> None:
        """
        Authorize an operation or raise PermissionError.
        """

        result = await self.authorize(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            required_permissions=required_permissions,
            risk=risk,
            device_id=device_id,
        )

        if not result.allowed:
            raise PermissionError(
                f"Operation denied: {result.reason}"
            )

    def audit_execution_started(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        device_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Audit the start of an authorized execution."""

        self.audit_logger.execution_started(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            device_id=device_id,
            task_id=task_id,
        )

    def audit_execution_completed(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        device_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Audit successful execution."""

        self.audit_logger.execution_completed(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            device_id=device_id,
            task_id=task_id,
        )

    def audit_execution_failed(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        reason: str,
        device_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Audit failed execution."""

        self.audit_logger.execution_failed(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            reason=reason,
            device_id=device_id,
            task_id=task_id,
        )