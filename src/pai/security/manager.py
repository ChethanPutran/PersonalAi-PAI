from __future__ import annotations

from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

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

    Holds a session factory (not a session). Every method that touches
    the DB opens a short-lived session, builds a fresh repository from
    it, queries, and closes.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        policy_engine: PolicyEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        # NOTE: user_plugin_repository is no longer injected — it is
        # built per-operation from a fresh session.
        self._session_factory = session_factory
        self.policy_engine = policy_engine or PolicyEngine()
        self.audit_logger = audit_logger or AuditLogger()

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def is_authorized(self, user_id: str, plugin_id: str) -> bool:
        session = self._session_factory()
        try:
            repo = UserPluginRepository(session)
            return await repo.is_enabled(user_id, plugin_id)
        finally:
            await session.close()

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

        Sequence: user plugin enabled? → permissions → policies → audit.
        """

        # 1. Check user plugin state — in its own session.
        session = self._session_factory()
        try:
            repo = UserPluginRepository(session)
            plugin_enabled = await repo.is_enabled(user_id, plugin_id)
        finally:
            await session.close()

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

        # 2. Build authorization request.
        request = AuthorizationRequest(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            required_permissions=frozenset(required_permissions or set()),
            risk=risk,
            device_id=device_id,
        )

        # 3. Evaluate policies.
        user_permissions = PermissionSet(
            permissions=frozenset(required_permissions or set())
        )
        result = self.policy_engine.evaluate(
            request,
            user_permissions=user_permissions,
        )

        # 4. Audit.
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
        result = await self.authorize(
            user_id=user_id,
            plugin_id=plugin_id,
            capability=capability,
            required_permissions=required_permissions,
            risk=risk,
            device_id=device_id,
        )
        if not result.allowed:
            raise PermissionError(f"Operation denied: {result.reason}")

    # ------------------------------------------------------------------
    # Audit helpers (no DB access — unchanged)
    # ------------------------------------------------------------------

    def audit_execution_started(
        self, *, user_id: str, plugin_id: str, capability: str,
        device_id: str | None = None, task_id: str | None = None,
    ) -> None:
        self.audit_logger.execution_started(
            user_id=user_id, plugin_id=plugin_id, capability=capability,
            device_id=device_id, task_id=task_id,
        )

    def audit_execution_completed(
        self, *, user_id: str, plugin_id: str, capability: str,
        device_id: str | None = None, task_id: str | None = None,
    ) -> None:
        self.audit_logger.execution_completed(
            user_id=user_id, plugin_id=plugin_id, capability=capability,
            device_id=device_id, task_id=task_id,
        )

    def audit_execution_failed(
        self, *, user_id: str, plugin_id: str, capability: str,
        reason: str, device_id: str | None = None, task_id: str | None = None,
    ) -> None:
        self.audit_logger.execution_failed(
            user_id=user_id, plugin_id=plugin_id, capability=capability,
            reason=reason, device_id=device_id, task_id=task_id,
        )