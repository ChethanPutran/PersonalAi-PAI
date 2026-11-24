"""System status, health, and admin endpoints."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_admin, get_db
from pai.app_context import PAIAppContext, get_app_context
from pai.plugins.manifest_info import PluginInfo
from pai.security.tokens.auth import ahash_password
from pai.storage.models import (
    DevicePluginModel,
    UserPluginModel,
)
from pai.storage.repositories import (
    DeviceRepository,
    UserPluginRepository,
    UserRepository,
)
from pai.tasks.store import TaskNotFoundError
from pai.common.task_controls import TaskControls
from pai.storage.repositories.task import upgrade_legacy_payload
from pai.tasks.models import Task, TaskControlView, TaskStatus
from pai.tasks.serialization import serialize_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

_STARTED_AT = time.time()
_ADMIN_DIR = Path(__file__).resolve().parent.parent / "admin"
_ADMIN_HTML = _ADMIN_DIR / "static" / "index.html"      # the HUD admin console (strict CSP, ES modules)
_ADMIN_LEGACY_HTML = _ADMIN_DIR / "admin_legacy.html"   # the previous single-file page, kept as a safety net


# ============================================================
# Health / status (public)
# ============================================================

@router.get("/health")
async def health_check():
    return {"status": "healthy", "system": "Personal AI", "version": "1.0.0"}


@router.get("/info")
async def get_system_info():
    return {
        "name": "Personal AI (PAI)",
        "version": "1.0.0",
        "description": "Context-Aware Autonomous Personal Agent System",
        "components": [
            "AI Kernel", "Event Bus", "Plugin Manager",
            "Agent Manager", "Executor Manager", "Memory Service",
            "Device Manager",
        ],
        "documentation": "/docs",
        "admin_panel": "/api/v1/system/admin",
    }


@router.get("/status")
async def get_system_status(
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    kernel = context.orchestrator
    return {
        "status": "operational",
        "uptime_seconds": int(time.time() - _STARTED_AT),
        "components": {
            "device_manager":   _device_manager_summary(kernel),
            "plugin_manager":   await _plugin_manager_summary(kernel),   # ← await
            "agent_manager":    _agent_manager_summary(kernel),
            "executor_manager": _executor_manager_summary(kernel),
            "memory_service":   _memory_service_summary(kernel),
        },
        "message": "Personal AI System is operational",
    }


# ============================================================
# Request models
# ============================================================

class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=72)
    is_admin: Optional[bool] = None


class PluginToggleRequest(BaseModel):
    enabled: bool


class ActionExecuteRequest(BaseModel):
    user_id: str
    capability: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    device_id: Optional[str] = None


# ============================================================
# Task management (admin) — unchanged
# ============================================================

serialize_task = _serialize_task = serialize_task  # moved to pai.tasks.serialization (kept importable)


@router.get("/llm/usage", dependencies=[Depends(get_current_admin)])
async def llm_usage(recent: int = 20, context: PAIAppContext = Depends(get_app_context)):
    """Tokens and cost used by LLM calls: totals overall and per provider / model / route (planner,
    chat, agent), plus the latest calls. Cost is null for a call with no known price (set
    `llm_pricing` / `cost_per_million_*` in config); `unpriced_calls` counts those."""
    tracker = _cost_tracker(context)
    out = tracker.summary(recent=max(0, min(recent, 200)))
    voice = tracker.voice_summary()
    voice["active_sessions"] = context.voice.active_sessions if getattr(context, "voice", None) is not None else 0
    out["voice"] = voice
    return out


@router.get("/llm/usage/series", dependencies=[Depends(get_current_admin)])
async def llm_usage_series(
    bucket: str = "hour",
    since: Optional[float] = None,
    until: Optional[float] = None,
    route: Optional[str] = None,
    model: Optional[str] = None,
    context: PAIAppContext = Depends(get_app_context),
):
    """Calls, tokens and cost per time bucket (minute | hour | day) for charts. `since`/`until` are epoch
    seconds (default: the last 24 buckets); `route` (planner/chat/agent) and `model` filter."""
    try:
        return _cost_tracker(context).series(bucket=bucket, since=since, until=until, route=route, model=model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/models", dependencies=[Depends(get_current_admin)])
async def models_registry(context: PAIAppContext = Depends(get_app_context)):
    """The model registry (config/models.yaml): the models each role uses now, every model that can be chosen with
    its capabilities and prices (cheapest first), and any problems (a model missing from the file, a planner/agent
    model without tool support)."""
    from pai.config.model_registry import ModelRegistry, registry_report
    registry = context.model_registry or ModelRegistry(warnings=["no model registry loaded"])
    return registry_report(registry, context.model_roles or {})


# ---- settings editor ------------------------------------------------------------------------------------------

class SettingsChange(BaseModel):
    """`set`: {"voice.airouter.voice": "nova", "llm_routes": {...}}; `reset`: dotted paths to take back to config.yaml."""
    set: Dict[str, Any] = Field(default_factory=dict, max_length=500)
    reset: List[str] = Field(default_factory=list, max_length=500)

    @field_validator("set", "reset")
    @classmethod
    def _paths(cls, v):
        import re
        for p in (v.keys() if isinstance(v, dict) else v):
            if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,200}", str(p)):
                raise ValueError(f"invalid setting path {p!r}")
        return v


_settings_lock = asyncio.Lock()


def _settings_state(context: PAIAppContext) -> Dict[str, Any]:
    from pai.config import settings_admin as S
    from pai.config.app import Config
    base = S.load_yaml_dict(context.settings_base_path)
    overrides = S.load_yaml_dict(context.settings_overrides_path)
    out = S.snapshot(Config, context.running_settings, base, overrides)
    out["overrides_file"] = context.settings_overrides_path
    return out


@router.get("/settings", dependencies=[Depends(get_current_admin)])
async def get_settings(context: PAIAppContext = Depends(get_app_context)):
    """Every setting with its schema, the value the server runs with, what config.yaml says, what the admin panel
    saved on top, and which saved values are waiting for a restart. Secrets are never returned."""
    return _settings_state(context)


@router.put("/settings")
async def update_settings(body: SettingsChange, admin_id: str = Depends(get_current_admin),
                          context: PAIAppContext = Depends(get_app_context)):
    """Validate (with the real Config model) and save changes to the overrides file. Nothing is written if any value is
    invalid. Changes apply on the next start. Secrets cannot be changed here (they live in .env)."""
    from pai.config import settings_admin as S
    from pai.config.app import Config
    async with _settings_lock:
        base = S.load_yaml_dict(context.settings_base_path)
        old = S.load_yaml_dict(context.settings_overrides_path)
        try:
            new = S.apply_changes(Config, Config.model_json_schema(), base, old, body.set, body.reset)
        except S.SettingsError as exc:
            raise HTTPException(status_code=exc.status, detail={"message": str(exc), "errors": exc.errors})
        try:
            S.write_overrides(context.settings_overrides_path, new)
        except OSError as exc:
            raise HTTPException(status_code=500, detail={"message": f"could not save settings: {exc}", "errors": []})
    changed = [{"path": p, "old": S.get_path(old, p), "new": S.get_path(new, p)}
               for p in sorted(set(S.flatten(old)) | set(S.flatten(new))) if S.get_path(old, p) != S.get_path(new, p)]
    if context.admin is not None:
        context.admin.audit(admin_id, "settings_update", set=",".join(sorted(body.set)) or "-",
                            reset=",".join(sorted(body.reset)) or "-")
    state = _settings_state(context)
    state["changed"] = changed
    return state


@router.post("/reload")
async def reload_system(admin_id: str = Depends(get_current_admin), context: PAIAppContext = Depends(get_app_context)):
    """Apply the saved settings to the running server WITHOUT a restart and without touching this admin session:
    models/routes/prices, voice, credits and history retention swap in live; anything else is listed under
    `restart_required`. A bad saved configuration changes nothing (`ok: false`)."""
    from pai.config.app import Config
    from pai.config.reload import reload_settings
    async with _settings_lock:
        report = await reload_settings(context, context.settings_loader or (lambda: Config()))
    if context.admin is not None:
        context.admin.audit(admin_id, "settings_reload", ok=report["ok"],
                            applied=",".join(a["group"] for a in report["applied"]) or "-",
                            restart_required=len(report["restart_required"]))
    report["settings"] = _settings_state(context)
    return report


class CreditsRequest(BaseModel):
    balance_usd: float = Field(ge=0, le=1e9, description="Credits you have right now, in USD")


def _cost_tracker(context: PAIAppContext):
    tracker = getattr(context.orchestrator.planner.llm, "cost_tracker", None)
    if tracker is None:
        raise HTTPException(status_code=404, detail="LLM cost tracking is disabled (llm_cost.track: false).")
    return tracker


@router.put("/llm/credits", dependencies=[Depends(get_current_admin)])
async def set_llm_credits(body: CreditsRequest, context: PAIAppContext = Depends(get_app_context)):
    """Set the credits you have right now (USD). Remaining credits = this balance minus the cost of calls
    made from now on. Replaces the configured `llm_cost.initial_credits_usd` until reset."""
    return _cost_tracker(context).set_credits(body.balance_usd)


@router.delete("/llm/credits", dependencies=[Depends(get_current_admin)])
async def reset_llm_credits(context: PAIAppContext = Depends(get_app_context)):
    """Drop the admin-set balance and go back to the configured initial credits (if any)."""
    return _cost_tracker(context).clear_credits()


@router.get("/tasks", dependencies=[Depends(get_current_admin)])
async def admin_list_tasks(
    status: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 200,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    tm = context.orchestrator.task_manager
    try:
        raw = await tm.list(
            status=TaskStatus(status) if status else None,
        )
    except Exception as exc:
        logger.warning("list_tasks failed: %s", exc)
        raise HTTPException(500, f"Failed to list tasks: {exc}")

    items = [_serialize_task(t) for t in (raw or [])]
    if user_id:
        items = [t for t in items if t.get("user_id") == user_id]

    items.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    items = items[:limit]
    return {"tasks": items, "total": len(items)}


@router.get("/tasks/{task_id}", dependencies=[Depends(get_current_admin)])
async def admin_get_task(
    task_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    try:
        task = await context.orchestrator.task_manager.get(task_id)
    except TaskNotFoundError:
        task = None
    if not task:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)


async def _task_action(
    task_id: str,
    method: str,
    context: PAIAppContext,
) -> Dict[str, Any]:
    tm = context.orchestrator.task_manager
    fn = getattr(tm, method, None)
    if fn is None:
        raise HTTPException(500, f"Task manager has no `{method}` method")
    try:
        result = fn(task_id)
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        raise HTTPException(400, f"{method} failed: {exc}")
    if not result:
        raise HTTPException(404, "Task not found")
    return _serialize_task(result)


@router.post("/tasks/{task_id}/pause", dependencies=[Depends(get_current_admin)])
async def admin_pause_task(task_id: str, context: PAIAppContext = Depends(get_app_context)):
    return await _task_action(task_id, "pause", context)


@router.post("/tasks/{task_id}/resume", dependencies=[Depends(get_current_admin)])
async def admin_resume_task(task_id: str, context: PAIAppContext = Depends(get_app_context)):
    return await _task_action(task_id, "resume", context)


@router.post("/tasks/{task_id}/cancel", dependencies=[Depends(get_current_admin)])
async def admin_cancel_task(task_id: str, context: PAIAppContext = Depends(get_app_context)):
    return await _task_action(task_id, "cancel", context)


# ============================================================
# User CRUD (admin) — via UserRepository
# ============================================================

@router.post("/users", status_code=201, dependencies=[Depends(get_current_admin)])
async def admin_create_user(
    payload: UserCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    repo = UserRepository(session)

    if await repo.get_by_email(payload.email) is not None:
        raise HTTPException(409, "Email already registered")

    hashed = await ahash_password(payload.password)
    user = await repo.create_with_credentials(
        email=payload.email,
        hashed_password=hashed,
        is_admin=payload.is_admin,
    )
    await session.commit()

    return {"id": user.id, "email": user.email, "is_admin": user.is_admin}


@router.patch("/users/{user_id}", dependencies=[Depends(get_current_admin)])
async def admin_update_user(
    user_id: str,
    payload: UserUpdateRequest,
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    repo = UserRepository(session)

    user = await repo.get(user_id)
    if user is None:
        raise HTTPException(404, "User not found")

    if payload.email is not None and payload.email != user.email:
        other = await repo.get_by_email(payload.email)
        if other is not None and other.id != user_id:
            raise HTTPException(409, "Email already in use")
        user.email = payload.email

    if payload.password is not None:
        user.hashed_password = await ahash_password(payload.password)

    if payload.is_admin is not None:
        user.is_admin = payload.is_admin

    await session.commit()

    # Invalidate per-user caches (capability map may include admin gates,
    # plugin enables, etc. — safest to always drop after any user write)
    if context.orchestrator.capability_router is not None:
        context.orchestrator.capability_router.invalidate(user_id)

    return {"id": user.id, "email": user.email, "is_admin": user.is_admin}


@router.delete("/users/{user_id}", status_code=204)
async def admin_delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
    current_admin_id: str = Depends(get_current_admin),
) -> Response:
    if user_id == current_admin_id:
        raise HTTPException(400, "You cannot delete your own account")

    user_repo = UserRepository(session)
    user = await user_repo.get(user_id)
    if user is None:
        raise HTTPException(404, "User not found")

    # Cascade 1: unregister owned devices through DeviceManager.
    # unregister() detaches the WebSocket, removes the device from the
    # registry, deletes the DB row, invalidates the router — all in one
    # traced call. Uses its own session, so it commits independently;
    # failure for one device is logged and skips to the next.
    dm = context.orchestrator.device_manager
    for device in await DeviceRepository(session).list_for_user(user_id):
        try:
            await dm.unregister(device.id)
        except Exception as exc:
            logger.warning(
                "admin_delete_user: unregister %s failed: %s",
                device.id, exc,
            )

    # Cascade 2: delete user_plugins rows (FK owner)
    up_repo = UserPluginRepository(session)
    for record in await up_repo.list_for_user(user_id):
        await up_repo.delete(user_id, record.plugin_id)

    # Finally the user
    await user_repo.delete(user_id)
    await session.commit()

    if context.orchestrator.capability_router is not None:
        context.orchestrator.capability_router.invalidate(user_id)

    return Response(status_code=204)


# ============================================================
# Device management (admin) — via DeviceManager
# ============================================================

@router.post("/devices/{device_id}/disconnect", dependencies=[Depends(get_current_admin)])
async def admin_disconnect_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    dm = context.orchestrator.device_manager
    try:
        await dm.disconnect(device_id)
    except Exception as exc:
        raise HTTPException(400, f"Disconnect failed: {exc}")
    return {"device_id": device_id, "disconnected": True}


@router.delete("/devices/{device_id}", status_code=204,
               dependencies=[Depends(get_current_admin)])
async def admin_delete_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Response:
    dm = context.orchestrator.device_manager
    removed = await dm.unregister(device_id)
    if not removed:
        raise HTTPException(404, "Device not found")
    return Response(status_code=204)


# ============================================================
# Plugin management (admin) — via PluginManager
# ============================================================

@router.post("/users/{user_id}/plugins/{plugin_id}/toggle",
             dependencies=[Depends(get_current_admin)])
async def admin_toggle_user_plugin(
    user_id: str,
    plugin_id: str,
    payload: PluginToggleRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    try:
        if payload.enabled:
            await pm.enable(user_id=user_id, plugin_id=plugin_id)
        else:
            await pm.disable(user_id=user_id, plugin_id=plugin_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    return {
        "user_id": user_id,
        "plugin_id": plugin_id,
        "is_enabled": payload.enabled,
    }


@router.delete("/users/{user_id}/plugins/{plugin_id}", status_code=204,
               dependencies=[Depends(get_current_admin)])
async def admin_delete_user_plugin(
    user_id: str,
    plugin_id: str,
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
) -> Response:
    repo = UserPluginRepository(session)
    deleted = await repo.delete(user_id, plugin_id)
    if not deleted:
        raise HTTPException(404, "Plugin record not found")
    await session.commit()

    if context.orchestrator.capability_router is not None:
        context.orchestrator.capability_router.invalidate(user_id)

    return Response(status_code=204)


# ============================================================
# Direct action execution (admin)
# ============================================================

@router.post("/actions/execute", dependencies=[Depends(get_current_admin)])
async def admin_execute_action(
    payload: ActionExecuteRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    try:
        return await context.orchestrator.execute_action(
            user_id=payload.user_id,
            capability=payload.capability,
            params=payload.parameters,
            device_id=payload.device_id,
        )
    except Exception as exc:
        raise HTTPException(400, f"Action failed: {exc}")


# ============================================================
# Admin panel HTML (public shell — data is behind /admin/data)
# ============================================================

@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(context: PAIAppContext = Depends(get_app_context)) -> HTMLResponse:
    from pai.api.admin.static_app import CSP, SECURITY_HEADERS
    if context.admin is None:
        # admin.enabled=false: the console's assets/API are not mounted, so serve the self-contained legacy page.
        return await admin_panel_legacy()
    if not _ADMIN_HTML.exists():
        return HTMLResponse(
            f"<h1>Admin panel not installed</h1>"
            f"<p>Expected file: {_ADMIN_HTML}</p>",
            status_code=500,
        )
    return HTMLResponse(
        _ADMIN_HTML.read_text(encoding="utf-8"),
        headers={"Content-Security-Policy": CSP, "Cache-Control": "no-cache", **SECURITY_HEADERS},
    )


@router.get("/admin/legacy", response_class=HTMLResponse)
async def admin_panel_legacy() -> HTMLResponse:
    """The previous single-file admin page (inline script, no CSP) — a safety net."""
    if not _ADMIN_LEGACY_HTML.exists():
        return HTMLResponse("<h1>Legacy admin page not installed</h1>", status_code=404)
    return HTMLResponse(_ADMIN_LEGACY_HTML.read_text(encoding="utf-8"))


# ============================================================
# Admin — aggregate
# ============================================================

@router.get("/admin/data", dependencies=[Depends(get_current_admin)])
async def admin_data(
    context: PAIAppContext = Depends(get_app_context),
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    kernel = context.orchestrator

    devices = await _collect_devices(kernel)

    install_rows = await session.execute(select(DevicePluginModel))
    installs = list(install_rows.scalars().all())

    users = await UserRepository(session).list_all()

    user_plugin_rows = await session.execute(select(UserPluginModel))
    user_plugins = list(user_plugin_rows.scalars().all())

    plugins = _collect_plugins(kernel, installs)
    online = sum(1 for d in devices if d["connected"])

    return {
        "summary": {
            "uptime_seconds": int(time.time() - _STARTED_AT),
            "devices_total": len(devices),
            "devices_online": online,
            "plugins_catalog": len(plugins),
            "plugin_installations": sum(p["installations"] for p in plugins),
            "users_total": len(users),
        },
        "devices": devices,
        "plugins": plugins,
        "users": _collect_users(users, user_plugins),
    }


# ============================================================
# Admin — granular reads (direct SQL is fine; read-only)
# ============================================================

@router.get("/devices", dependencies=[Depends(get_current_admin)])
async def list_devices(
    context: PAIAppContext = Depends(get_app_context),
) -> List[Dict[str, Any]]:
    return await _collect_devices(context.orchestrator)


@router.get("/devices/{device_id}", dependencies=[Depends(get_current_admin)])
async def get_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    devices = await _collect_devices(context.orchestrator)
    for d in devices:
        if d["id"] == device_id:
            return d
    raise HTTPException(404, f"Device '{device_id}' not found")


@router.get("/users", dependencies=[Depends(get_current_admin)])
async def list_users(
    session: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    users = await UserRepository(session).list_all()
    plugin_rows = await session.execute(select(UserPluginModel))
    user_plugins = list(plugin_rows.scalars().all())
    return _collect_users(users, user_plugins)


@router.get("/users/{user_id}/plugins", dependencies=[Depends(get_current_admin)])
async def user_plugins(
    user_id: str,
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    records = await UserPluginRepository(session).list_for_user(user_id)
    return {
        "user_id": user_id,
        "plugins": [
            {
                "plugin_id": r.plugin_id,
                "is_enabled": r.is_enabled,
                "metadata": r.metadata_json or {},
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ],
    }


@router.get("/plugins", dependencies=[Depends(get_current_admin)])
async def list_plugins_admin(
    context: PAIAppContext = Depends(get_app_context),
    session: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    install_rows = await session.execute(select(DevicePluginModel))
    installs = list(install_rows.scalars().all())
    return _collect_plugins(context.orchestrator, installs)


@router.get("/plugins/{plugin_id}/installations", dependencies=[Depends(get_current_admin)])
async def plugin_installations(
    plugin_id: str,
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    result = await session.execute(
        select(DevicePluginModel).where(DevicePluginModel.plugin_id == plugin_id)
    )
    records = list(result.scalars().all())
    return {
        "plugin_id": plugin_id,
        "installations": [
            {
                "device_id": r.device_id,
                "version": r.version,
                "is_installed": r.is_installed,
                "enabled_on_device": r.enabled_on_device,
                "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                "last_error": r.last_error,
            }
            for r in records
        ],
    }


# ============================================================
# Collectors
# ============================================================

def _device_manager_summary(kernel: Any) -> Dict[str, Any]:
    dm = getattr(kernel, "device_manager", None)
    if dm is None:
        return {"available": False}
    connections = getattr(dm, "_connections", {})
    return {"connected": len(connections)}


async def _plugin_manager_summary(kernel: Any) -> Dict[str, Any]:
    pm = getattr(kernel, "plugin_manager", None)
    if pm is None:
        return {"available": False}
    try:
        status = await pm.get_status()
        return {
            "initialized": status.get("initialized", False),
            "catalog_size": status.get("plugin_count", 0),
            "plugins": status.get("plugins", []),
            "registry_dir": status.get("plugin_registry_dir"),
        }
    except Exception as exc:
        logger.warning("plugin manager status failed: %s", exc)
        return {"available": False}


def _agent_manager_summary(kernel: Any) -> Dict[str, Any]:
    am = getattr(kernel, "agent_manager", None)
    if am is None:
        return {"available": False}
    agents = getattr(am, "_agents", {})
    return {"agents": len(agents)}


def _executor_manager_summary(kernel: Any) -> Dict[str, Any]:
    em = getattr(kernel, "executor_scheduler", None)
    if em is None:
        return {"available": False}
    executors = getattr(em, "_executors", {})
    return {"executors": len(executors)}


def _memory_service_summary(kernel: Any) -> Dict[str, Any]:
    mm = getattr(kernel, "memory_manager", None)
    if mm is None:
        return {"available": False}
    return {"initialized": getattr(mm, "_initialized", False)}


async def _collect_devices(kernel: Any) -> List[Dict[str, Any]]:
    dm = getattr(kernel, "device_manager", None)
    if dm is None:
        return []

    try:
        all_devices = await dm.registry.all()
    except TypeError:
        all_devices = await dm.registry.all("anonymous")
    except Exception as exc:
        logger.warning("device list failed: %s", exc)
        return []

    connections = getattr(dm, "_connections", {})

    return [
        {
            "id": getattr(d, "id", None) or getattr(d, "device_id", None) or str(d),
            "name": getattr(d, "name", None) or getattr(d, "id", ""),
            "platform": getattr(d, "platform", None),
            "architecture": getattr(d, "architecture", None),
            "device_type": getattr(d, "device_type", None),
            "status": getattr(d, "status", "unknown"),
            "connected": (getattr(d, "id", None) in connections),
            "last_seen": _iso(getattr(d, "last_seen", None)),
            "hostname": getattr(d, "hostname", None),
            "app_version": getattr(d, "app_version", None),
        }
        for d in all_devices
    ]


def _collect_plugins(kernel: Any, installs: list) -> List[Dict[str, Any]]:
    pm = getattr(kernel, "plugin_manager", None)
    catalog_info: List[PluginInfo] = []
    if pm is not None:
        try:
            catalog_info = pm.get_catalog() or []
        except Exception as exc:
            logger.warning("plugin catalog access failed: %s", exc)

    per_plugin: Dict[str, list] = {}
    for row in installs:
        per_plugin.setdefault(row.plugin_id, []).append(row)

    catalog: Dict[str, Dict[str, Any]] = {}
    for info in catalog_info:
        pid = info.id
        if not pid:
            continue
        catalog[pid] = {
            "id": pid,
            "name": info.name,
            "version": info.version,
            "description": info.description or "",
            "type": info.type or "action",
            "platforms": list(info.platforms or []),
            "architectures": list(info.architectures or []),
            "capabilities": list(info.capabilities or []),
            "package_url": info.packageUrl,
            "checksum": info.checksum,
            "size": info.size or 0,
        }

    for plugin_id in per_plugin:
        if plugin_id not in catalog:
            catalog[plugin_id] = {
                "id": plugin_id,
                "name": plugin_id,
                "version": "",
                "description": "(not in catalog)",
                "plugin_type": "unknown",
                "platforms": [],
                "architectures": [],
                "capabilities": [],
                "package_url": None,
                "checksum": None,
                "size": 0,
            }

    out: List[Dict[str, Any]] = []
    for pid, info in catalog.items():
        rows = per_plugin.get(pid, [])
        out.append({
            **info,
            "installations": sum(1 for r in rows if r.is_installed),
            "enabled_on_devices": sum(1 for r in rows if r.enabled_on_device),
            "devices": [
                {
                    "device_id": r.device_id,
                    "version": r.version,
                    "is_installed": r.is_installed,
                    "enabled_on_device": r.enabled_on_device,
                    "last_error": r.last_error,
                }
                for r in rows
            ],
        })
    return out


def _collect_users(users: list, plugin_records: list) -> List[Dict[str, Any]]:
    per_user: Dict[str, list] = {}
    for row in plugin_records:
        per_user.setdefault(row.user_id, []).append(row)

    out: List[Dict[str, Any]] = []
    for u in users:
        user_plugins = per_user.get(u.id, [])
        out.append({
            "id": u.id,
            "email": getattr(u, "email", None),
            "is_admin": getattr(u, "is_admin", False),
            "created_at": _iso(getattr(u, "created_at", None)),
            "plugins_total": len(user_plugins),
            "plugins_enabled": sum(1 for p in user_plugins if p.is_enabled),
            "plugins": [
                {
                    "plugin_id": p.plugin_id,
                    "is_enabled": p.is_enabled,
                    "metadata": p.metadata_json or {},
                }
                for p in user_plugins
            ],
        })
    return out


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)