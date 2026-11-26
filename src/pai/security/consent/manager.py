import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping, Optional

from loguru import logger

from pai.common.enums.permission import ConsentScope

from .binding import BindingMode, ParamBinding, binding_allowed, preview_parameters
from .limits import (
    DEFAULT_ALWAYS_DAYS, MAX_ACTIVE_GRANTS_PER_USER, MAX_ALWAYS_DAYS, SESSION_TTL,
)
from .models import ConsentError, ConsentGrant, ConsentRecord
from .repository import ConsentGrantRepository, InMemoryConsentGrantRepository
from .validator import ConsentValidator


class ConsentManager:
    """Permission-level consent records (legacy) + capability/parameter-bound GRANTS.

    ``find_grant`` is the ONE place that answers "does a standing approval already cover this
    call?" — the authorization pipeline (``check_with_grants``) and the task confirmation gate
    both use it, so the two can never disagree.
    """

    def __init__(self, repository, grants: Optional[ConsentGrantRepository] = None):
        self.repository = repository
        self.grants: ConsentGrantRepository = grants or InMemoryConsentGrantRepository()
        self._write_lock = asyncio.Lock()      # replace-then-add must not interleave

    # ------------------------------------------------------------------ legacy records
    def grant(self, subject, plugin_id, permission, expires_at=None):
        record = ConsentRecord(subject, plugin_id, permission, True, expires_at)
        self.repository.put(record)
        return record

    def revoke(self, subject, plugin_id, permission):
        record = ConsentRecord(subject, plugin_id, permission, False)
        self.repository.put(record)
        return record

    def check(self, request, declaration, *, manifest_requires_confirmation: bool = False):
        record = self.repository.get(request.principal_key, request.plugin_id, request.permission)
        # The manifest can only tighten: either side requiring consent is enough.
        if not (declaration.requires_consent or manifest_requires_confirmation):
            return record or ConsentRecord(request.principal_key, request.plugin_id, request.permission, True)
        # Validator also checks the record really belongs to this principal/plugin/permission.
        if ConsentValidator.validate(record, request):
            return record
        return ConsentRecord(request.principal_key, request.plugin_id, request.permission, False, approval_required=True)

    # ------------------------------------------------------------------ grants
    async def find_grant(
        self, *, user_id: Optional[str], plugin_id: Optional[str], capability: Optional[str],
        device_id: Optional[str], session_id: Optional[str], parameters: Optional[Mapping[str, Any]],
    ) -> Optional[ConsentGrant]:
        """The grant (if any) that approves exactly this call now. No side effects."""
        if not (user_id and plugin_id and capability and device_id):
            return None
        params = dict(parameters or {})
        try:
            candidates = await self.grants.candidates(user_id, plugin_id, capability, device_id)
        except Exception as exc:
            # A broken grant store must never ALLOW anything: no grant found => ask the user.
            logger.warning("consent grant lookup failed (asking for approval instead): {}", exc)
            return None
        covering = [
            g for g in candidates
            if binding_allowed(capability, g.binding)       # never trust a stored over-broad grant
            and g.covers(user_id=user_id, plugin_id=plugin_id, capability=capability,
                        device_id=device_id, session_id=session_id, parameters=params)
        ]
        # Prefer the most specific grant (a parameter-bound one over "any"), then the newest.
        covering.sort(key=lambda g: (g.binding.mode == BindingMode.ANY, -g.created_at.timestamp()))
        return covering[0] if covering else None

    async def check_with_grants(self, request, declaration, *, manifest_requires_confirmation: bool = False):
        """``check`` + standing grants: used by AuthorizationManager."""
        record = self.check(request, declaration,
                            manifest_requires_confirmation=manifest_requires_confirmation)
        if record.active() or not (declaration.requires_consent or manifest_requires_confirmation):
            return record
        grant = await self.find_grant(
            user_id=request.user_id, plugin_id=request.plugin_id, capability=request.capability,
            device_id=request.target_device_id, session_id=request.session_id,
            parameters=dict(request.parameters))
        if grant is None:
            return record
        return ConsentRecord(request.principal_key, request.plugin_id, request.permission, True,
                             expires_at=grant.expires_at, grant_id=grant.id)

    async def create_grant(
        self, *, user_id: str, plugin_id: str, capability: str, device_id: str,
        scope: ConsentScope, session_id: Optional[str], binding: ParamBinding,
        parameters: Optional[Mapping[str, Any]], risk: str, days: Optional[int],
        granted_by: str, allowed_scopes: List[ConsentScope],
    ) -> ConsentGrant:
        """Store a SESSION/ALWAYS approval. Every limit is enforced HERE, server-side."""
        if scope == ConsentScope.ONCE:
            raise ConsentError("scope_not_storable", "a one-time approval is not stored as a grant")
        if scope not in allowed_scopes:
            raise ConsentError("scope_not_allowed",
                               f"'{scope.value}' is broader than this action allows "
                               f"(allowed: {[s.value for s in allowed_scopes]})")
        params = dict(parameters or {})
        if not binding_allowed(capability, binding):
            raise ConsentError("binding_too_broad",
                               f"an approval for {capability} must be tied to the exact command, "
                               "not to every call")
        unknown = binding.unknown_keys(params)
        if unknown:
            raise ConsentError("binding_invalid", f"unknown parameter(s) to bind: {unknown}")

        now = datetime.now(timezone.utc)
        if scope == ConsentScope.SESSION:
            if not session_id:
                raise ConsentError("session_required", "a session grant needs a session id")
            expires = now + SESSION_TTL                      # `days` does not extend a session grant
        else:
            d = DEFAULT_ALWAYS_DAYS if days is None else days
            if not 1 <= d <= MAX_ALWAYS_DAYS:
                raise ConsentError("days_invalid", f"days must be between 1 and {MAX_ALWAYS_DAYS}")
            expires = now + timedelta(days=d)

        params_hash = binding.hash_of(params)
        grant = ConsentGrant(
            user_id=user_id, plugin_id=plugin_id, capability=capability, device_id=device_id,
            scope=scope, session_id=session_id if scope == ConsentScope.SESSION else None,
            binding=binding, params_hash=params_hash,
            params_preview=([] if binding.mode == BindingMode.ANY else preview_parameters(
                params, binding.keys if binding.mode == BindingMode.KEYS else None)),
            risk=risk, created_at=now, expires_at=expires, granted_by=granted_by)

        async with self._write_lock:
            # Re-approving the same thing replaces the earlier grant instead of piling up duplicates.
            for old in await self.grants.candidates(user_id, plugin_id, capability, device_id):
                if (old.scope, old.session_id, old.binding, old.params_hash) == (
                        grant.scope, grant.session_id, grant.binding, grant.params_hash):
                    await self.grants.revoke(user_id, old.id)
            # Bounded per user: drop expired, then least-recently-used, to make room.
            await self.grants.purge_expired()
            if await self.grants.count_active(user_id) >= MAX_ACTIVE_GRANTS_PER_USER:
                await self.grants.evict_least_recently_used(user_id, MAX_ACTIVE_GRANTS_PER_USER - 1)
            await self.grants.add(grant)
        return grant

    async def record_use(self, grant_id: str) -> None:
        await self.grants.record_use(grant_id)

    async def list_grants(self, user_id: str) -> List[ConsentGrant]:
        return await self.grants.list_for_user(user_id)

    async def revoke_grant(self, user_id: str, grant_id: str) -> bool:
        return await self.grants.revoke(user_id, grant_id)

    async def revoke_grants(self, user_id: str, *, capability: Optional[str] = None,
                            device_id: Optional[str] = None) -> int:
        return await self.grants.revoke_matching(user_id, capability, device_id)
