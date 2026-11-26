"""The signed-in user's own account: profile, password, devices, plugins, recent remote activity,
and the web Settings page that edits them.

Everything here is scoped to the caller (``get_current_user``); the page itself is a static shell
(no data) and signs in through ``/auth/login`` like any client.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db
from pai.app_context import PAIAppContext, get_app_context
from pai.security.tokens.auth import ahash_password, averify_password
from pai.storage.models import UserModel
from pai.storage.repositories.plugin import UserPluginRepository
from pai.users.profile import Personalization, load_personalization

router = APIRouter(prefix="/account", tags=["account"])

_PAGE = Path(__file__).resolve().parent.parent / "user" / "settings.html"
_HELP_PAGE = _PAGE.with_name("help.html")
_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


# ============================================================
# Settings page (static shell; its API calls need a bearer token)
# ============================================================

@router.get("/help", response_class=HTMLResponse, include_in_schema=False)
async def help_page() -> HTMLResponse:
    """What PAI can do, with examples. Static, public information (no user data)."""
    if not _HELP_PAGE.is_file():
        return HTMLResponse("<h1>Help page not installed</h1>", status_code=500)
    return HTMLResponse(_HELP_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings_page() -> HTMLResponse:
    if not _PAGE.exists():
        return HTMLResponse(f"<h1>Settings page not installed</h1><p>Expected: {_PAGE}</p>", status_code=500)
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


# ============================================================
# Profile
# ============================================================

class ProfileOut(BaseModel):
    id: str
    email: str
    is_admin: bool = False
    display_name: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    created_at: Optional[str] = None


class ProfileUpdate(BaseModel):
    """Send only what changes. An empty string clears an optional field."""
    display_name: Optional[str] = Field(default=None, max_length=80)
    timezone: Optional[str] = Field(default=None, max_length=64)
    locale: Optional[str] = Field(default=None, max_length=16)

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v is not None else None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: Optional[str]) -> Optional[str]:
        if v:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise ValueError("Unknown time zone (use a name like 'Asia/Kolkata')")
        return v

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: Optional[str]) -> Optional[str]:
        if v and not _LOCALE.match(v):
            raise ValueError("Language must look like 'en' or 'en-IN'")
        return v


def _profile(user: UserModel) -> ProfileOut:
    return ProfileOut(
        id=user.id, email=user.email, is_admin=bool(user.is_admin),
        display_name=user.display_name, timezone=user.timezone, locale=user.locale,
        created_at=user.created_at.isoformat() if user.created_at else None)


async def _user(session: AsyncSession, user_id: str) -> UserModel:
    user = (await session.execute(select(UserModel).where(UserModel.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found")
    return user


@router.get("/profile", response_model=ProfileOut)
async def get_profile(user_id: str = Depends(get_current_user),
                      session: AsyncSession = Depends(get_db)) -> ProfileOut:
    return _profile(await _user(session, user_id))


@router.patch("/profile", response_model=ProfileOut)
async def update_profile(payload: ProfileUpdate, user_id: str = Depends(get_current_user),
                         session: AsyncSession = Depends(get_db),
                         context: PAIAppContext = Depends(get_app_context)) -> ProfileOut:
    user = await _user(session, user_id)
    for field in payload.model_fields_set:
        setattr(user, field, getattr(payload, field) or None)       # "" clears
    await session.flush()
    _forget(context, user_id)
    return _profile(user)


def _forget(context: PAIAppContext, user_id: str) -> None:
    """Drop the cached profile so the next chat message uses what was just saved."""
    profiles = getattr(context.orchestrator, "user_profiles", None)
    if profiles is not None:
        profiles.invalidate(user_id)


# ============================================================
# Personalization (likes, interests, style ...: used to personalise replies)
# ============================================================

@router.get("/personalization", response_model=Personalization)
async def get_personalization(user_id: str = Depends(get_current_user),
                              session: AsyncSession = Depends(get_db)) -> Personalization:
    return load_personalization((await _user(session, user_id)).personalization)


@router.put("/personalization", response_model=Personalization)
async def put_personalization(payload: Personalization, user_id: str = Depends(get_current_user),
                              session: AsyncSession = Depends(get_db),
                              context: PAIAppContext = Depends(get_app_context)) -> Personalization:
    """Replace the user's personalization. Everything is optional; an empty body clears it. The text is
    sanitised and bounded (see pai.users.profile) because it is sent to the model with every request."""
    user = await _user(session, user_id)
    user.personalization = payload.model_dump()
    await session.flush()
    _forget(context, user_id)
    return payload


@router.delete("/personalization", status_code=204)
async def clear_personalization(user_id: str = Depends(get_current_user),
                                session: AsyncSession = Depends(get_db),
                                context: PAIAppContext = Depends(get_app_context)):
    user = await _user(session, user_id)
    user.personalization = None
    await session.flush()
    _forget(context, user_id)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


@router.post("/password")
async def change_password(payload: PasswordChange, user_id: str = Depends(get_current_user),
                          session: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    user = await _user(session, user_id)
    if not await averify_password(payload.current_password, user.hashed_password):
        raise HTTPException(403, "Current password is incorrect")
    if payload.new_password == payload.current_password:
        raise HTTPException(422, "Choose a password different from the current one")
    user.hashed_password = await ahash_password(payload.new_password)
    await session.flush()
    return {"ok": True}


# ============================================================
# Devices (with the remote-access switches)
# ============================================================

@router.get("/devices")
async def my_devices(context: PAIAppContext = Depends(get_app_context),
                     user_id: str = Depends(get_current_user)) -> Dict[str, Any]:
    dm = context.orchestrator.device_manager
    out: List[Dict[str, Any]] = []
    for d in await dm.list_devices(user_id):
        out.append({
            "id": d.id, "name": d.name, "platform": str(getattr(d.platform, "value", d.platform)),
            "device_type": str(getattr(d.device_type, "value", d.device_type)),
            "architecture": str(getattr(d.architecture, "value", d.architecture)),
            "os_version": d.os_version, "app_version": d.app_version,
            "connected": bool(await dm.is_connected(d.id)),
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            "allow_remote_install": bool(d.allow_remote_install),
            "allow_remote_invoke": bool(d.allow_remote_invoke),
        })
    out.sort(key=lambda x: (not x["connected"], x["name"].lower()))
    return {"devices": out}


# ============================================================
# Plugins (which ones the user has switched on)
# ============================================================

@router.get("/plugins")
async def my_plugins(context: PAIAppContext = Depends(get_app_context),
                     user_id: str = Depends(get_current_user),
                     session: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    enabled = {r.plugin_id: bool(r.is_enabled)
               for r in await UserPluginRepository(session).list_for_user(user_id)}
    plugins = [{
        "id": p.id, "name": p.name, "description": p.description, "version": p.version,
        "platforms": sorted(str(getattr(k, "value", k)) for k in p.platforms),
        "requires_confirmation": bool(p.requiresConfirmation),
        "builtin": bool(getattr(p, "builtin", False)),
        "enabled": enabled.get(p.id, False),
    } for p in pm.get_catalog()]
    plugins.sort(key=lambda p: (p["name"].lower(), p["id"]))
    return {"plugins": plugins}


# ============================================================
# Recent remote plugin activity
# ============================================================

@router.get("/activity")
async def recent_activity(limit: int = Query(30, ge=1, le=100),
                          context: PAIAppContext = Depends(get_app_context),
                          user_id: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Recent plugin installs/uninstalls on the user's devices, newest first."""
    ops = context.orchestrator.plugin_manager.operations
    return {"operations": await ops.list(user_id=user_id, limit=limit)}
