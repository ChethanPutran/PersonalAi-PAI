from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger


import uuid
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from pai.event_bus.bus import EventBus
from pai.observability.tracer import get_tracer
from pai.plugins.manifest import PluginManifest
from pai.security.audit.logger import AuditLogger
from pai.common.enums.permission import Origin, PermissionAction, PermissionResource, PrincipalType
from pai.security.authorization.context import AuthorizationContext, ManifestFacts, ScopeContext
from datetime import datetime, timedelta, timezone
from pai.security.authorization.decision import AuthorizationDecision, DecisionEffect
from pai.security.authorization.enforcement import enforce
from pai.security.permissions.requirement import PermissionRequirement
from pai.security.authorization.request import Actor, AuthorizationRequest, ResourceRef
from pai.storage.repositories.plugin import UserPluginRepository
from pai.common.manager import Manager

from pai.event_bus.bus import EventBus
from pai.storage.repositories.plugin import UserPluginRepository
from .authorization.manager import AuthorizationManager
from .permissions.registry import PermissionRegistry
from .permissions.validator import PermissionValidator
from .policies.repository import PolicyRepository
from .policies.evaluator import PolicyEvaluator
from .policies.combining import PolicyCombiner
from .consent.manager import ConsentManager
from .consent.repository import InMemoryConsentRepository
from .scopes.evaluator import ScopeEvaluator
from .risk.classifier import RiskClassifier
from .risk.registry import RiskRegistry
from .audit.logger import AuditLogger
from .audit.repository import InMemoryAuditRepository
from .tokens.issuer import TokenIssuer
from .tokens.validator import TokenValidator


def build_authorization_manager(authz_config: Any = None) -> AuthorizationManager:
    """Wire the authorization pipeline (shared by all facades).

    ``authz_config`` is ``AuthorizationConfig``: its roles drive RBAC and its
    ``rules`` become declarative policies. ``enabled=False`` keeps only the
    builtin safety policies (no RBAC / rules).
    """
    permissions = PermissionRegistry()
    if authz_config is not None and authz_config.enabled:
        repo = PolicyRepository.with_builtins(
            role_permissions={str(r.role.value): {str(a.value) for a in r.permissions} for r in authz_config.roles},
            default_role=authz_config.default_role.value,
            rules=[r.model_dump() for r in authz_config.rules],
        )
    else:
        repo = PolicyRepository.with_builtins()
    return AuthorizationManager(
        permission_validator=PermissionValidator(permissions),
        policy_evaluator=PolicyEvaluator(repo),
        combiner=PolicyCombiner(),
        consent_manager=ConsentManager(InMemoryConsentRepository()),
        scope_evaluator=ScopeEvaluator(),
        risk_classifier=RiskClassifier(RiskRegistry.with_defaults()),
        audit_logger=AuditLogger(InMemoryAuditRepository()),
        token_issuer=TokenIssuer(),
    )

def _resource_id(res_type: PermissionResource, parameters: dict[str, Any]) -> str | None:
    """Instance id of the target, taken from the call parameters.

    The scope evaluators then validate it, and the issued token binds to it.
    """
    key = {PermissionResource.FILESYSTEM: "path", PermissionResource.NETWORK: "url"}.get(res_type)
    value = parameters.get(key) if key else None
    return str(value) if value else None

class SecurityManager(Manager):
    """
    Central authorization facade.

    Holds a session factory (not a session). Every method that touches
    the DB opens a short-lived session, builds a fresh repository from
    it, queries, and closes.
    """
    name = "security_manager"

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        event_bus: EventBus,
        plugin_manager: Any = None,
        device_manager: Any = None,
        authorization_config: Any = None,
    ) -> None:
        super().__init__(session_factory=session_factory, event_bus=event_bus)
        self.authorization:AuthorizationManager = build_authorization_manager(authorization_config)
        self.audit_logger = self.authorization.audit_logger
        self.token_issuer = self.authorization.token_issuer
        self.token_validator = TokenValidator(self.token_issuer)
        self.plugin_manager=plugin_manager
        self.device_manager=device_manager


    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def is_authorized(self, user_id: str, plugin_id: str) -> bool:
        """Check if a user has enabled a plugin."""
        session = self.get_session()
        if session is None:
            raise RuntimeError("No session manager available for DB access.")
        try:
            repo = UserPluginRepository(session)
            return await repo.is_enabled(user_id, plugin_id)
        finally:
            await session.close()
    

    async def _on_initialize(self) -> None:
        if self.authorization is None:
            raise RuntimeError("SecurityManager: authorization not configured")
        if self.plugin_manager is None:
            raise RuntimeError("SecurityManager: plugin_manager not configured")
        if self.device_manager is None:
            raise RuntimeError("SecurityManager: device_manager not configured")

    async def _on_shutdown(self) -> None:
        logger.info("AuthorizationManager shutdown complete")
        
    async def get_status(self,*, user_id: Optional[str] = None) -> dict:
        return {
            "authorization": getattr(self.authorization, "get_status", lambda: {})(),
            "plugin_manager": getattr(self.plugin_manager, "get_status", lambda: {})(),
            "device_manager": getattr(self.device_manager, "get_status", lambda: {})(),
        }


    async def authorize(
        self,
        *,
        user_id: str,
        plugin_id: str,
        capability: str,
        device_id: str,
        plugin_manifest: PluginManifest,
        parameters: dict[str, Any] | None = None,
        scope: ScopeContext | None = None,
        task_id: str | None = None,
        source_device_id: str | None = None,
        agent_id: str | None = None,
        origin: Optional[Origin] = Origin.AGENT_AUTONOMOUS,
        approved: bool = False,
        remember_consent_for: timedelta | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> AuthorizationDecision:
        """Authorize a capability execution.

        Sequence: plugin enabled for user? -> manifest permissions -> one
        AuthorizationRequest per required permission through the
        AuthorizationManager (scope, risk, consent, policies, token) -> audit.

        Security contract:
        - Required permissions come ONLY from the plugin manifest (trusted).
        - Risk is classified by the static RiskRegistry, never by the caller.
        - ``context`` carries trusted server-side scope config (e.g.
            filesystem_roots); it must never be derived from LLM output.
        - Fail-closed: unknown permissions, missing manifest entries for
            plugin capabilities, or any evaluation error => DENY.
        """
        rid = str(uuid.uuid4())
        if plugin_manifest is None and self.plugin_manager is not None:
            plugin_manifest = self.plugin_manager.get_manifest(plugin_id)

        def _deny(reason: str) -> AuthorizationDecision:
            decision = AuthorizationDecision(DecisionEffect.DENY, reason, rid)
            self.audit_logger.authorization(
                user_id=user_id, plugin_id=plugin_id, capability=capability,
                allowed=False, reason=reason, device_id=device_id,
            )
            return decision

        tracer = get_tracer()
        async with tracer.span(
            "security.authorize",
            user_id=user_id,
            capability=capability,
            device_id=device_id,
            plugin=plugin_id,
        ) as span:

            async with tracer.span(
                "security.resolve_plugin",
                capability=capability,
                pre_supplied=plugin_id is not None,
            ) as res_span:
            
                # 1. User plugin state — own short-lived session.
                session = self.get_session()
                if session is None:
                    raise RuntimeError("No session manager available for DB access.")
                try:
                    plugin_enabled = await UserPluginRepository(session).is_enabled(user_id, plugin_id)
                finally:
                    await session.close()
                if not plugin_enabled:
                    res_span.set_attribute("error", "plugin not enabled for user")
                    return _deny("Plugin is not enabled for this user.")

                # 2. Required permissions from the trusted manifest.
                # Fail closed: a plugin capability without a manifest entry is denied.
                cap_spec = plugin_manifest.get_capability(capability) if plugin_manifest else None
                if cap_spec is None:
                    res_span.set_attribute("error", "capability not declared in plugin manifest")
                    return _deny(f"Capability '{capability}' is not declared in the plugin manifest.")
                resources = frozenset(cap_spec.permissions)
                # Trusted facts from the manifest; never derived from LLM output.
                facts = ManifestFacts(
                    requires_confirmation=bool(cap_spec.requireConfirmation),
                    confirmation_reason=cap_spec.confirmationReason,
                    approved=approved,
                )

            async with tracer.span(
                "security.evaluate_permissions",
                capability=capability,
                resources=list(resources),
            ) as perm_span:

                # 3. Resolve each manifest resource to a registered (permission, action).
                registry = self.authorization.permission_validator.registry
                decisions: list[AuthorizationDecision] = []
                requirements: list[PermissionRequirement] = []
                for resource in sorted(str(getattr(r, "value", r)) for r in resources):
                    declaration = next((d for d in registry.all() if d.name.split(".", 1)[0] == resource), None)
                    if declaration is None or not declaration.actions:
                        return _deny(f"No registered permission for manifest resource '{resource}'.")
                    requirements.append(PermissionRequirement(resource, declaration.actions[0], plugin_id))

                for req_ in requirements:
                    res_type = PermissionResource(req_.permission)
                    chain = [Actor(PrincipalType.USER, user_id)]
                    if agent_id:
                        chain.append(Actor(PrincipalType.AGENT, agent_id))
                    if task_id:
                        chain.append(Actor(PrincipalType.TASK, task_id))
                    request = AuthorizationRequest(
                        subject=Actor(PrincipalType.PLUGIN, plugin_id),
                        on_behalf_of=tuple(chain),
                        action=PermissionAction(req_.action),
                        resource=ResourceRef(res_type, _resource_id(res_type, parameters or {})),
                        capability=capability,
                        source_device_id=source_device_id,
                        target_device_id=device_id,
                        parameters=parameters or {},
                        context=(scope or ScopeContext()).model_dump(),
                        origin=origin,
                        session_id=session_id,
                        trace_id=trace_id,
                    )

                    auth_context = AuthorizationContext(request=request, manifest=facts)

                    decision = await self.authorization.authorize(request, auth_context)
                    if not decision.effect == DecisionEffect.ALLOW:
                        return decision  # first non-allow wins (deny / approval_required)
                    decisions.append(decision)
                    if approved and remember_consent_for:
                        # User opted to remember this approval (time-boxed).
                        self.authorization.consent_manager.grant(
                            request.principal_key, plugin_id, request.permission,
                            expires_at=datetime.now(timezone.utc) + remember_consent_for,
                        )

            if decisions:
                # One signed token per permission; the executor must see all of them.
                final = decisions[-1]
                final.tokens = [d.token for d in decisions if d.token]
                return final
        
            # Nothing privileged required (e.g. low-risk builtin-style capability).
            # The manifest's requireConfirmation still gates it.
            if facts.requires_confirmation and not approved:
                decision = AuthorizationDecision(
                    DecisionEffect.APPROVAL_REQUIRED,
                    "Capability requires user confirmation.", rid,
                    confirmation_reason=facts.confirmation_reason,
                )
                self.audit_logger.authorization(
                    user_id=user_id, plugin_id=plugin_id, capability=capability,
                    allowed=False, reason=decision.reason, device_id=device_id,
                )
                return decision
            decision = AuthorizationDecision(DecisionEffect.ALLOW, "No permissions required.", rid)
            self.audit_logger.authorization(
                user_id=user_id, plugin_id=plugin_id, capability=capability,
                allowed=True, reason=decision.reason, device_id=device_id,
            )
            return decision

    async def authorize_task(self, task: Any, *, approved: bool = False) -> AuthorizationDecision:
        """(Re-)authorize a runtime task from its own trusted fields, binding any
        issued token to ``task.id``.

        ``approved=True`` (user confirmed) only resolves APPROVAL_REQUIRED;
        scope/policy DENYs still stand.
        """
        auth = task.authorization
        return await self.authorize(
            user_id=task.user_id, plugin_id=task.plugin, capability=task.capability,
            device_id=task.device_id, plugin_manifest=None,
            parameters=task.parameters, scope=auth.scope,
            task_id=task.id, session_id=task.session_id,
            source_device_id=auth.source_device_id, agent_id=auth.agent_id, origin=auth.origin,
            approved=approved,
        )

    def verify_task_tokens(self, task: Any) -> None:
        """Raise PermissionError unless the task carries valid tokens.

        Fail-closed: no tokens, bad signature, expiry, or a token minted
        for a different plugin/capability/device/task all reject.
        """
        tokens = list(task.authorization.tokens)
        if not tokens:
            raise PermissionError("No authorization token for task")
        for token in tokens:
            try:
                self.token_validator.validate(
                    token, plugin_id=task.plugin, capability=task.capability,
                    device_id=task.device_id, task_id=task.id,
                )
            except Exception as exc:
                self.audit_logger.authorization(
                    user_id=task.user_id, plugin_id=task.plugin or "", capability=task.capability,
                    allowed=False, reason=f"token rejected: {exc}", device_id=task.device_id,
                )
                raise PermissionError(f"Invalid authorization token: {exc}") from exc

    # ------------------------------------------------------------------
    # Consent management (standing approvals)
    # ------------------------------------------------------------------

    def grant_consent(self, user_id: str, plugin_id: str, permission: str,
                      ttl: timedelta | None = None):
        expires = datetime.now(timezone.utc) + ttl if ttl else None
        return self.authorization.consent_manager.grant(f"user:{user_id}", plugin_id, permission, expires)

    def revoke_consent(self, user_id: str, plugin_id: str, permission: str):
        return self.authorization.consent_manager.revoke(f"user:{user_id}", plugin_id, permission)

    async def require_authorization(self, **kwargs: Any) -> None:
        decision = await self.authorize(**kwargs)
        enforce(decision)  # raises AuthorizationDenied (a PermissionError) unless ALLOW

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