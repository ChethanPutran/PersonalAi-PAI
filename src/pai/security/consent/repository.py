class InMemoryConsentRepository:
    def __init__(self):
        self._records = {}
    def put(self, record):
        self._records[(record.subject, record.plugin_id, record.permission)] = record
    def get(self, subject, plugin_id, permission):
        return self._records.get((subject, plugin_id, permission))


# ---------------------------------------------------------------------------
# Capability/parameter-bound grants (see ConsentGrant). Async so the default store can be SQL.
# ---------------------------------------------------------------------------
from datetime import datetime, timezone
from typing import Callable, List, Optional, Protocol

from sqlalchemy import delete, func, select, update

from pai.security.consent.binding import ParamBinding
from pai.security.consent.models import ConsentGrant, ParamPreview, _aware


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConsentGrantRepository(Protocol):
    async def add(self, grant: ConsentGrant) -> None: ...
    async def candidates(self, user_id: str, plugin_id: str, capability: str, device_id: str) -> List[ConsentGrant]: ...
    async def get(self, grant_id: str) -> Optional[ConsentGrant]: ...
    async def list_for_user(self, user_id: str) -> List[ConsentGrant]: ...
    async def revoke(self, user_id: str, grant_id: str) -> bool: ...
    async def revoke_matching(self, user_id: str, capability: Optional[str], device_id: Optional[str]) -> int: ...
    async def record_use(self, grant_id: str) -> None: ...
    async def purge_expired(self) -> int: ...
    async def count_active(self, user_id: str) -> int: ...
    async def evict_least_recently_used(self, user_id: str, keep: int) -> int: ...


def _lru_key(g: ConsentGrant) -> datetime:
    return _aware(g.last_used_at) or _aware(g.created_at)


class InMemoryConsentGrantRepository:
    """Tests / no-database runs. Same behaviour as the SQL repository, minus durability."""

    def __init__(self) -> None:
        self._grants: dict[str, ConsentGrant] = {}

    async def add(self, grant: ConsentGrant) -> None:
        self._grants[grant.id] = grant.model_copy(deep=True)

    async def candidates(self, user_id, plugin_id, capability, device_id):
        return [g.model_copy(deep=True) for g in self._grants.values()
                if g.is_active() and (g.user_id, g.plugin_id, g.capability, g.device_id)
                == (user_id, plugin_id, capability, device_id)]

    async def get(self, grant_id):
        g = self._grants.get(grant_id)
        return g.model_copy(deep=True) if g else None

    async def list_for_user(self, user_id):
        return sorted((g.model_copy(deep=True) for g in self._grants.values()
                       if g.user_id == user_id and g.is_active()),
                      key=lambda g: _aware(g.created_at), reverse=True)

    async def revoke(self, user_id, grant_id):
        g = self._grants.get(grant_id)
        if g is None or g.user_id != user_id or g.revoked_at is not None:
            return False
        g.revoked_at = _now()
        return True

    async def revoke_matching(self, user_id, capability, device_id):
        n = 0
        for g in self._grants.values():
            if g.user_id == user_id and g.revoked_at is None and g.is_active() \
                    and (capability is None or g.capability == capability) \
                    and (device_id is None or g.device_id == device_id):
                g.revoked_at = _now()
                n += 1
        return n

    async def record_use(self, grant_id):
        g = self._grants.get(grant_id)
        if g is not None:
            g.use_count += 1
            g.last_used_at = _now()

    async def purge_expired(self):
        dead = [i for i, g in self._grants.items() if not g.is_active()]
        for i in dead:
            del self._grants[i]
        return len(dead)

    async def count_active(self, user_id):
        return sum(1 for g in self._grants.values() if g.user_id == user_id and g.is_active())

    async def evict_least_recently_used(self, user_id, keep):
        mine = sorted((g for g in self._grants.values() if g.user_id == user_id and g.is_active()),
                      key=_lru_key)
        doomed = mine[: max(0, len(mine) - keep)]
        for g in doomed:
            del self._grants[g.id]
        return len(doomed)


class SqlConsentGrantRepository:
    """Durable grants in the ``consent_grants`` table (survives restarts)."""

    def __init__(self, session_factory: Callable) -> None:
        self._sf = session_factory

    # ---- mapping -----------------------------------------------------
    @staticmethod
    def _to_model(g: ConsentGrant):
        from pai.storage.models import ConsentGrantModel
        return ConsentGrantModel(
            id=g.id, user_id=g.user_id, plugin_id=g.plugin_id, capability=g.capability,
            device_id=g.device_id, scope=g.scope.value, session_id=g.session_id,
            binding_mode=g.binding.mode.value, binding_keys=list(g.binding.keys),
            params_hash=g.params_hash, params_preview=[p.model_dump() for p in g.params_preview],
            risk=g.risk, created_at=g.created_at, expires_at=g.expires_at, granted_by=g.granted_by,
            last_used_at=g.last_used_at, use_count=g.use_count, revoked_at=g.revoked_at)

    @staticmethod
    def _from_row(r) -> ConsentGrant:
        return ConsentGrant(
            id=r.id, user_id=r.user_id, plugin_id=r.plugin_id, capability=r.capability,
            device_id=r.device_id, scope=r.scope, session_id=r.session_id,
            binding=ParamBinding(mode=r.binding_mode, keys=list(r.binding_keys or [])),
            params_hash=r.params_hash or "",
            params_preview=[ParamPreview(**p) for p in (r.params_preview or [])],
            risk=r.risk, created_at=_aware(r.created_at), expires_at=_aware(r.expires_at),
            granted_by=r.granted_by, last_used_at=_aware(r.last_used_at), use_count=r.use_count or 0,
            revoked_at=_aware(r.revoked_at))

    def _model(self):
        from pai.storage.models import ConsentGrantModel
        return ConsentGrantModel

    # ---- interface ---------------------------------------------------
    async def add(self, grant: ConsentGrant) -> None:
        async with self._sf() as s:
            s.add(self._to_model(grant))
            await s.commit()

    async def candidates(self, user_id, plugin_id, capability, device_id):
        M = self._model()
        async with self._sf() as s:
            rows = (await s.execute(select(M).where(
                M.user_id == user_id, M.plugin_id == plugin_id, M.capability == capability,
                M.device_id == device_id, M.revoked_at.is_(None), M.expires_at > _now()))).scalars().all()
        return [g for g in map(self._from_row, rows) if g.is_active()]

    async def get(self, grant_id):
        async with self._sf() as s:
            r = await s.get(self._model(), grant_id)
            return self._from_row(r) if r else None

    async def list_for_user(self, user_id):
        M = self._model()
        async with self._sf() as s:
            rows = (await s.execute(select(M).where(
                M.user_id == user_id, M.revoked_at.is_(None), M.expires_at > _now())
                .order_by(M.created_at.desc()))).scalars().all()
        return [g for g in map(self._from_row, rows) if g.is_active()]

    async def revoke(self, user_id, grant_id):
        M = self._model()
        async with self._sf() as s:
            res = await s.execute(update(M).where(
                M.id == grant_id, M.user_id == user_id, M.revoked_at.is_(None)).values(revoked_at=_now()))
            await s.commit()
            return (res.rowcount or 0) > 0

    async def revoke_matching(self, user_id, capability, device_id):
        M = self._model()
        cond = [M.user_id == user_id, M.revoked_at.is_(None), M.expires_at > _now()]
        if capability is not None:
            cond.append(M.capability == capability)
        if device_id is not None:
            cond.append(M.device_id == device_id)
        async with self._sf() as s:
            res = await s.execute(update(M).where(*cond).values(revoked_at=_now()))
            await s.commit()
            return res.rowcount or 0

    async def record_use(self, grant_id):
        M = self._model()
        async with self._sf() as s:                      # atomic increment: concurrent runs both count
            await s.execute(update(M).where(M.id == grant_id).values(
                use_count=M.use_count + 1, last_used_at=_now()))
            await s.commit()

    async def purge_expired(self):
        M = self._model()
        async with self._sf() as s:
            res = await s.execute(delete(M).where((M.expires_at <= _now()) | (M.revoked_at.is_not(None))))
            await s.commit()
            return res.rowcount or 0

    async def count_active(self, user_id):
        M = self._model()
        async with self._sf() as s:
            return int((await s.execute(select(func.count()).select_from(M).where(
                M.user_id == user_id, M.revoked_at.is_(None), M.expires_at > _now()))).scalar_one())

    async def evict_least_recently_used(self, user_id, keep):
        M = self._model()
        async with self._sf() as s:
            rows = (await s.execute(select(M).where(
                M.user_id == user_id, M.revoked_at.is_(None), M.expires_at > _now()))).scalars().all()
            rows = sorted(rows, key=lambda r: _aware(r.last_used_at) or _aware(r.created_at))
            doomed = rows[: max(0, len(rows) - keep)]
            for r in doomed:
                await s.delete(r)
            await s.commit()
            return len(doomed)
