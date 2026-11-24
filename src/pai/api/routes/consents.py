"""Consent routes: list / revoke standing approvals, and the authorization preflight.

Grants are created ONLY by ``POST /tasks/{id}/confirm`` (authenticated REST). Nothing here
creates one, and nothing said in chat or by voice can.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db, user_is_admin
from pai.app_context import PAIAppContext, get_app_context
from pai.security.consent.binding import ParamBinding
from pai.security.consent.models import ConsentGrant, ParamPreview, PreflightResult

router = APIRouter(tags=["consents"])


class GrantOut(BaseModel):
    id: str
    scope: str
    plugin_id: str
    capability: str
    device_id: str
    device_name: Optional[str] = None
    binding: ParamBinding
    params_preview: Optional[List[ParamPreview]] = None
    session_id: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    last_used_at: Optional[datetime] = None
    use_count: int = 0
    risk: str = "low"

    @classmethod
    def from_grant(cls, g: ConsentGrant, device_name: Optional[str] = None) -> "GrantOut":
        return cls(
            id=g.id, scope=g.scope.value, plugin_id=g.plugin_id, capability=g.capability,
            device_id=g.device_id, device_name=device_name, binding=g.binding,
            params_preview=g.params_preview or None, session_id=g.session_id,
            created_at=g.created_at, expires_at=g.expires_at, last_used_at=g.last_used_at,
            use_count=g.use_count, risk=g.risk)


class GrantList(BaseModel):
    consents: List[GrantOut]


class RevokeResult(BaseModel):
    revoked: int


@router.get("/consents", response_model=GrantList)
async def list_consents(
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> GrantList:
    sec = context.orchestrator.security_manager
    dm = context.orchestrator.device_manager
    names: Dict[str, Optional[str]] = {}
    out: List[GrantOut] = []
    for g in await sec.consents.list_grants(user_id):
        if g.device_id not in names:
            try:
                dev = await dm.get(g.device_id)
                names[g.device_id] = getattr(dev, "name", None) if dev is not None else None
            except Exception:  # noqa: BLE001 — a name is cosmetic
                names[g.device_id] = None
        out.append(GrantOut.from_grant(g, names[g.device_id]))
    return GrantList(consents=out)


@router.delete("/consents/{grant_id}", status_code=200, response_model=RevokeResult)
async def revoke_consent(
    grant_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> RevokeResult:
    # Owner only: revoke_grant looks the id up within this user's grants (404 for others').
    if not await context.orchestrator.security_manager.revoke_grant(user_id, grant_id):
        raise HTTPException(404, "Consent not found")
    return RevokeResult(revoked=1)


@router.delete("/consents", response_model=RevokeResult)
async def revoke_consents(
    capability: Optional[str] = None,
    device_id: Optional[str] = None,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> RevokeResult:
    if not capability and not device_id:
        raise HTTPException(422, detail={
            "code": "filter_required", "message": "Give capability and/or device_id."})
    n = await context.orchestrator.security_manager.revoke_grants(
        user_id, capability=capability, device_id=device_id)
    return RevokeResult(revoked=n)


# ── preflight ─────────────────────────────────────────────────────────────────────────────

class PreflightRequest(BaseModel):
    capability: str
    device_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None


async def resolve_plugin(context: PAIAppContext, user_id: str, capability: str, device_id: str):
    """(plugin_id, manifest) that would serve ``capability`` on ``device_id``; 404/409 if none."""
    from pai.orchestration.capability_router import CapabilityNotAvailable  # local: avoids import cycles
    router_ = context.orchestrator.capability_router if hasattr(
        context.orchestrator, "capability_router") else None
    if router_ is None:
        raise HTTPException(500, "capability router unavailable")
    try:
        targets = await router_.resolve(
            capability=capability, user_id=user_id, preferred_device_id=device_id)
    except CapabilityNotAvailable as exc:
        raise HTTPException(409, detail={
            "code": "capability_unavailable", "message": str(exc.error.reason)}) from exc
    target = next((t for t in targets if t.device_id == device_id), None)
    if target is None:
        raise HTTPException(409, detail={
            "code": "capability_unavailable",
            "message": f"'{capability}' is not available on that device."})
    manifest = context.orchestrator.security_manager.plugin_manager.get_manifest(target.plugin_id)
    return target.plugin_id, manifest


@router.post("/authorize/preflight", response_model=PreflightResult)
async def preflight(
    payload: PreflightRequest,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PreflightResult:
    """Would this action be allowed, need confirmation, or be denied? The admin flag is decided
    here from the DB — never from the request."""
    dm = context.orchestrator.device_manager
    device = await dm.get(payload.device_id)
    if device is None or getattr(device, "user_id", None) not in (None, user_id):
        raise HTTPException(404, "Device not found")
    online = await dm.is_connected(payload.device_id)
    plugin_id, manifest = await resolve_plugin(context, user_id, payload.capability, payload.device_id)
    return await context.orchestrator.security_manager.preflight(
        user_id=user_id, plugin_id=plugin_id, plugin_manifest=manifest,
        capability=payload.capability, device_id=payload.device_id, parameters=payload.parameters,
        session_id=payload.session_id or f"tester:{user_id}",
        is_admin=await user_is_admin(session, user_id), device_online=online)
