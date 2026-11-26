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
from pai.security.remote_access import remote_access_denial
from pai.security.audit.logger import AuditLogger
from pai.common.enums.permission import (
    ConsentScope, Origin, PermissionAction, PermissionResource, PrincipalType, RiskLevel,
)
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
from .consent.binding import DEFAULT_BINDING, default_binding_for, preview_parameters, required_keys
from .consent import limits
from .consent.models import (
    ConfirmationInfo, ConsentError, ConsentGrant, GrantRequest, PreflightDecision, PreflightResult,
)
from .policies.builtin.approval_policy import ApprovalPolicy
from .audit.models import AuditEventType
from .consent.repository import InMemoryConsentRepository, SqlConsentGrantRepository
from .scopes.evaluator import ScopeEvaluator
from .scopes.filesystem import PATH_KEYS, request_paths
from .risk.classifier import RiskClassifier
from .risk.registry import RiskRegistry
from .audit.logger import AuditLogger
from .audit.repository import InMemoryAuditRepository
from .tokens.issuer import TokenIssuer
from .tokens.validator import TokenValidator


def always_approve_from_config(authz_config: Any = None) -> frozenset:
    """Actions whose approval can never be remembered, per ``security.authorization.input_grant_scope``."""
    scope = getattr(authz_config, "input_grant_scope", "once") if authz_config is not None else "once"
    return ApprovalPolicy.always_approve_for(scope)


def build_authorization_manager(authz_config: Any = None, grant_repository: Any = None) -> AuthorizationManager:
    """Wire the authorization pipeline (shared by all facades).

    ``authz_config`` is ``AuthorizationConfig``: its roles drive RBAC and its
    ``rules`` become declarative policies. ``enabled=False`` keeps only the
    builtin safety policies (no RBAC / rules).
    """
    permissions = PermissionRegistry()
    always_approve = always_approve_from_config(authz_config)
    if authz_config is not None and authz_config.enabled:
        repo = PolicyRepository.with_builtins(
            role_permissions={str(r.role.value): {str(a.value) for a in r.permissions} for r in authz_config.roles},
            default_role=authz_config.default_role.value,
            rules=[r.model_dump() for r in authz_config.rules],
            always_approve=always_approve,
        )
    else:
        repo = PolicyRepository.with_builtins(always_approve=always_approve)
    return AuthorizationManager(
        permission_validator=PermissionValidator(permissions),
        policy_evaluator=PolicyEvaluator(repo),
        combiner=PolicyCombiner(),
        consent_manager=ConsentManager(InMemoryConsentRepository(), grant_repository),
        scope_evaluator=ScopeEvaluator(),
        risk_classifier=RiskClassifier(RiskRegistry.with_defaults()),
        audit_logger=AuditLogger(InMemoryAuditRepository()),
        token_issuer=TokenIssuer(),
    )

_RISK_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def _manifest_filesystem_roots(plugin_manifest: Any) -> list[str]:
    """The plugin's own declared ``metadata.allowedRoots`` (trusted: it comes from the installed manifest).

    ``~`` is expanded by the scope evaluator. Without this nothing ever filled
    ``ScopeContext.filesystem_roots`` and every ``files.*`` call was denied with
    "No filesystem roots configured".
    """
    meta = getattr(plugin_manifest, "metadata", None) or {}
    roots = meta.get("allowedRoots") if isinstance(meta, dict) else None
    return [r for r in roots if isinstance(r, str) and r.strip()] if isinstance(roots, list) else []


def _resource_id(res_type: PermissionResource, parameters: dict[str, Any]) -> str | None:
    """Instance id of the target, taken from the call parameters.

    The scope evaluators then validate it, and the issued token binds to it.
    """
    if res_type == PermissionResource.FILESYSTEM:
        paths = request_paths(parameters)
        return paths[0] if paths else None
    value = parameters.get("url") if res_type == PermissionResource.NETWORK else None
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
        # Standing approvals (session / always) are durable: SQL when there is a database.
        grants = SqlConsentGrantRepository(session_factory) if session_factory is not None else None
        self.always_approve = always_approve_from_config(authorization_config)
        self.authorization:AuthorizationManager = build_authorization_manager(authorization_config, grants)
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
        dry_run: bool = False,
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
        - ``dry_run=True`` runs the whole pipeline without audit events or tokens.
        - Fail-closed: unknown permissions, missing manifest entries for
            plugin capabilities, or any evaluation error => DENY.
        """
        rid = str(uuid.uuid4())

        def _audit(**kw: Any) -> None:
            if not dry_run:                       # a dry run is a question, not a decision
                self.audit_logger.authorization(**kw)
        if plugin_manifest is None and self.plugin_manager is not None:
            plugin_manifest = self.plugin_manager.get_manifest(plugin_id)

        def _deny(reason: str) -> AuthorizationDecision:
            decision = AuthorizationDecision(DecisionEffect.DENY, reason, rid)
            _audit(
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

                # 1b. Remote access: acting on a device from outside it needs the owner's opt-in
                # on THAT device (and the device must be the caller's). Covers every path that
                # authorizes a plugin run: chat plans, schedules, agents, the plugin tester.
                device_manager = getattr(self, "device_manager", None)
                if device_manager is not None:
                    denial = await remote_access_denial(
                        device_manager, user_id=user_id, target_device_id=device_id,
                        source_device_id=source_device_id, kind="invoke")
                    if denial is not None:
                        res_span.set_attribute("error", denial.code)
                        return _deny(denial.message)

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

                scope = scope or ScopeContext()
                required = set(getattr(cap_spec.parameters, "required", None) or [])
                if not required.intersection(PATH_KEYS) and not scope.filesystem_path_optional:
                    scope = scope.model_copy(update={"filesystem_path_optional": True})
                if not scope.filesystem_roots:
                    # Caller-supplied roots (trusted server code) win; otherwise the manifest's own.
                    manifest_roots = _manifest_filesystem_roots(plugin_manifest)
                    if manifest_roots:
                        scope = scope.model_copy(update={"filesystem_roots": manifest_roots})

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
                        context=scope.model_dump(),
                        origin=origin,
                        session_id=session_id,
                        trace_id=trace_id,
                    )

                    auth_context = AuthorizationContext(request=request, manifest=facts)

                    decision = await self.authorization.authorize(request, auth_context, dry_run=dry_run)
                    if not decision.effect == DecisionEffect.ALLOW:
                        return decision  # first non-allow wins (deny / approval_required)
                    decisions.append(decision)
                    if approved and remember_consent_for and not dry_run:
                        # User opted to remember this approval (time-boxed).
                        self.authorization.consent_manager.grant(
                            request.principal_key, plugin_id, request.permission,
                            expires_at=datetime.now(timezone.utc) + remember_consent_for,
                        )

            if decisions:
                # One signed token per permission; the executor must see all of them.
                final = decisions[-1]
                final.tokens = [d.token for d in decisions if d.token]
                final.consent_grant_id = next(
                    (d.consent_grant_id for d in decisions if d.consent_grant_id), None)
                return final
        
            # Nothing privileged required (e.g. low-risk builtin-style capability).
            # The manifest's requireConfirmation still gates it.
            grant = None
            if facts.requires_confirmation and not approved:
                # A standing grant (session / always) the user gave earlier covers this call.
                grant = await self.consents.find_grant(
                    user_id=user_id, plugin_id=plugin_id, capability=capability,
                    device_id=device_id, session_id=session_id, parameters=dict(parameters or {}))
            if facts.requires_confirmation and not approved and grant is None:
                decision = AuthorizationDecision(
                    DecisionEffect.APPROVAL_REQUIRED,
                    "Capability requires user confirmation.", rid,
                    confirmation_reason=facts.confirmation_reason,
                )
                _audit(
                    user_id=user_id, plugin_id=plugin_id, capability=capability,
                    allowed=False, reason=decision.reason, device_id=device_id,
                )
                return decision
            decision = AuthorizationDecision(
                DecisionEffect.ALLOW,
                "Covered by a standing approval." if grant is not None else "No permissions required.", rid,
                consent_grant_id=grant.id if grant is not None else None)
            _audit(
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
            # pre_approved is set only by trusted server code (a valid standing consent); it
            # is not persisted, so a recovered task must be approved again.
            approved=approved or auth.pre_approved,
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

    # ------------------------------------------------------------------
    # Confirmation scopes + standing grants (once / this session / always)
    # ------------------------------------------------------------------

    @property
    def consents(self) -> ConsentManager:
        return self.authorization.consent_manager

    def _cap_spec(self, plugin_id: Optional[str], capability: str, manifest: Any = None):
        manifest = manifest or (self.plugin_manager.get_manifest(plugin_id)
                                if (plugin_id and self.plugin_manager is not None) else None)
        return manifest, (manifest.get_capability(capability) if manifest is not None else None)

    def _always_approve(self, cap_spec: Any) -> bool:
        """Does any permission of this capability sit on ApprovalPolicy's always-approve list?"""
        if cap_spec is None:
            return False
        registry = self.authorization.permission_validator.registry
        for resource in cap_spec.permissions:
            res = str(getattr(resource, "value", resource))
            decl = next((d for d in registry.all() if d.name.split(".", 1)[0] == res), None)
            if decl is not None and decl.actions and (res, decl.actions[0]) in self.always_approve:
                return True
        return False

    async def confirmation_info_for(
        self, *, user_id: str, plugin_id: Optional[str], capability: str, device_id: str,
        parameters: Optional[dict], plugin_manifest: Any = None, reason: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ConfirmationInfo:
        """What the user is asked to approve, and the broadest approval the SERVER allows.

        Risk = max(manifest risk, static registry, the pipeline's own classification), so a
        manifest cannot under-declare it. Allowed scopes come from limits.allowed_scopes().
        """
        manifest, cap_spec = self._cap_spec(plugin_id, capability, plugin_manifest)
        risk = limits.risk_of(cap_spec.risk if cap_spec is not None else RiskLevel.LOW)
        registry_level = self.authorization.risk_classifier.registry.get(capability)
        if registry_level is not None:
            risk = max(risk, limits.risk_of(registry_level), key=_RISK_ORDER.index)
        if manifest is not None and cap_spec is not None:
            try:
                dry = await self.authorize(
                    user_id=user_id, plugin_id=plugin_id, capability=capability, device_id=device_id,
                    plugin_manifest=manifest, parameters=parameters, origin=Origin.USER_DIRECT,
                    session_id=session_id, dry_run=True, scope=ScopeContext().with_device(device_id))
                risk = max(risk, limits.risk_of(dry.risk_level), key=_RISK_ORDER.index)
            except Exception as exc:                               # risk stays at the manifest/registry level
                logger.debug("confirmation_info dry run failed: {}", exc)
        params = dict(parameters or {})
        return ConfirmationInfo(
            capability=capability, plugin_id=plugin_id or "builtin", device_id=device_id,
            risk=risk.value,
            parameters_summary=preview_parameters(params),
            allowed_scopes=limits.allowed_scopes(
                risk, getattr(cap_spec, "maxGrantScope", None), self._always_approve(cap_spec)),
            can_bind_params=bool(params), default_binding=default_binding_for(capability),
            required_binding_keys=list(required_keys(capability)),
            reason=reason or (cap_spec.confirmationReason if cap_spec is not None else None),
        )

    async def confirmation_info(self, task: Any) -> ConfirmationInfo:
        return await self.confirmation_info_for(
            user_id=task.user_id, plugin_id=task.plugin, capability=task.capability,
            device_id=task.device_id or "", parameters=task.parameters,
            reason=task.confirmation_reason or None, session_id=task.session_id)

    async def find_grant_for_task(self, task: Any) -> Optional[ConsentGrant]:
        return await self.consents.find_grant(
            user_id=task.user_id, plugin_id=task.plugin or "builtin", capability=task.capability,
            device_id=task.device_id, session_id=task.session_id, parameters=task.parameters)

    async def record_grant_use(self, grant_id: str, task: Any) -> None:
        """One use per run: bump the counters and write the audit event."""
        await self.consents.record_use(grant_id)
        self.audit_logger.consent_event(
            AuditEventType.CONSENT_AUTO_APPROVED, user_id=task.user_id, plugin_id=task.plugin or "builtin",
            capability=task.capability, device_id=task.device_id, task_id=task.id,
            reason=f"auto-approved by grant {grant_id}", metadata={"grant_id": grant_id})

    def audit_auto_approved(self, task: Any, why: str) -> None:
        """A run proceeded without a prompt for a non-grant reason (admin tester, valid schedule consent)."""
        self.audit_logger.consent_event(
            AuditEventType.CONSENT_AUTO_APPROVED, user_id=task.user_id, plugin_id=task.plugin or "builtin",
            capability=task.capability, device_id=task.device_id, task_id=task.id,
            reason=f"auto-approved: {why}", metadata={"why": why})

    async def grant_from_confirmation(
        self, task: Any, *, user_id: str, request: GrantRequest,
    ) -> Optional[ConsentGrant]:
        """The user pressed Confirm with a scope/binding. Validates everything server-side;
        returns the stored grant (None for a one-time approval)."""
        info = await self.confirmation_info(task)
        if request.scope not in info.allowed_scopes:
            raise ConsentError(
                "scope_not_allowed",
                f"'{request.scope.value}' is broader than this action allows "
                f"(allowed: {[s.value for s in info.allowed_scopes]})")
        meta = {"scope": request.scope.value, "binding": request.binding.model_dump(mode="json")}
        if request.scope == ConsentScope.ONCE:
            self.audit_logger.consent_event(
                AuditEventType.CONSENT_APPROVED_ONCE, user_id=user_id, plugin_id=info.plugin_id,
                capability=task.capability, device_id=task.device_id, task_id=task.id,
                reason="approved once", metadata=meta)
            return None
        grant = await self.consents.create_grant(
            user_id=user_id, plugin_id=info.plugin_id, capability=task.capability,
            device_id=task.device_id, scope=request.scope, session_id=task.session_id,
            binding=request.binding, parameters=task.parameters, risk=info.risk, days=request.days,
            granted_by=user_id, allowed_scopes=info.allowed_scopes)
        self.audit_logger.consent_event(
            AuditEventType.CONSENT_GRANTED, user_id=user_id, plugin_id=info.plugin_id,
            capability=task.capability, device_id=task.device_id, task_id=task.id,
            reason=f"grant {grant.id} ({grant.scope.value})",
            metadata={**meta, "grant_id": grant.id, "expires_at": grant.expires_at.isoformat(),
                      "session_id": grant.session_id})
        return grant

    async def revoke_grant(self, user_id: str, grant_id: str) -> bool:
        grant = next((g for g in await self.consents.list_grants(user_id) if g.id == grant_id), None)
        ok = await self.consents.revoke_grant(user_id, grant_id)
        if ok and grant is not None:
            self.audit_logger.consent_event(
                AuditEventType.CONSENT_REVOKED, user_id=user_id, plugin_id=grant.plugin_id,
                capability=grant.capability, device_id=grant.device_id, allowed=False,
                reason=f"grant {grant_id} revoked", metadata={"grant_id": grant_id, "scope": grant.scope.value})
        return ok

    async def revoke_grants(self, user_id: str, *, capability: Optional[str] = None,
                            device_id: Optional[str] = None) -> int:
        n = await self.consents.revoke_grants(user_id, capability=capability, device_id=device_id)
        if n:
            self.audit_logger.consent_event(
                AuditEventType.CONSENT_REVOKED, user_id=user_id, plugin_id="*", capability=capability or "*",
                device_id=device_id, allowed=False, reason=f"{n} grant(s) revoked",
                metadata={"count": n})
        return n

    async def preflight(
        self, *, user_id: str, plugin_id: str, plugin_manifest: Any, capability: str, device_id: str,
        parameters: Optional[dict], session_id: Optional[str], is_admin: bool, device_online: bool,
    ) -> PreflightResult:
        """Would this call be allowed / need confirmation / be denied? (no side effects but audit)

        Used by the app's LOCAL (on-device) tester before it runs natively. The admin flag is
        decided by the SERVER (never a client field); admin skips only the confirmation prompt.
        """
        def _done(decision: PreflightDecision, reason: str, info: Optional[ConfirmationInfo] = None):
            self.audit_logger.consent_event(
                AuditEventType.PREFLIGHT_DECISION, user_id=user_id, plugin_id=plugin_id,
                capability=capability, device_id=device_id, allowed=decision == PreflightDecision.ALLOW,
                reason=f"preflight {decision.value}: {reason}",
                metadata={"admin": is_admin, "device_online": device_online})
            return PreflightResult(decision=decision, reason=reason, confirmation=info)

        if not device_online and not is_admin:
            return _done(PreflightDecision.DENY,
                         "The device is offline, so this cannot be checked. Connect it and try again.")
        decision = await self.authorize(
            user_id=user_id, plugin_id=plugin_id, capability=capability, device_id=device_id,
            plugin_manifest=plugin_manifest, parameters=parameters, origin=Origin.USER_DIRECT,
            session_id=session_id, approved=is_admin, dry_run=True,
            # The route has verified this is the user's own, online device; without it every
            # device-scoped permission (screen, camera, input) is denied as "Device is not allowlisted".
            scope=ScopeContext().with_device(device_id))
        if decision.effect == DecisionEffect.DENY:
            return _done(PreflightDecision.DENY, decision.reason)
        if decision.effect == DecisionEffect.APPROVAL_REQUIRED:
            info = await self.confirmation_info_for(
                user_id=user_id, plugin_id=plugin_id, capability=capability, device_id=device_id,
                parameters=parameters, plugin_manifest=plugin_manifest, session_id=session_id)
            return _done(PreflightDecision.CONFIRM, decision.reason, info)
        return _done(PreflightDecision.ALLOW, decision.reason)

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