"""
System status, health, and admin endpoints.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_admin, get_db
from pai.app_context import PAIAppContext, get_app_context
from pai.storage.models import (
    DevicePluginModel,
    UserModel,
    UserPluginModel,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

_STARTED_AT = time.time()
_ADMIN_HTML = Path(__file__).resolve().parent.parent / "admin" / "panel.html"


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
            "AI Kernel",
            "Event Bus",
            "Plugin Manager",
            "Agent Manager",
            "Executor Manager",
            "Memory Service",
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
            "device_manager": _device_manager_summary(kernel),
            "plugin_manager": _plugin_manager_summary(kernel),
            "agent_manager": _agent_manager_summary(kernel),
            "executor_manager": _executor_manager_summary(kernel),
            "memory_service": _memory_service_summary(kernel),
        },
        "message": "Personal AI System is operational",
    }


# ============================================================
# Admin — HTML panel (public shell, protected data)
# ============================================================

@router.get("/admin", response_class=HTMLResponse)
async def admin_panel() -> HTMLResponse:
    """
    Serve the HTML shell.

    Public — contains no data. The shell itself asks the user to log
    in and attaches the resulting JWT to every /admin/data call.
    """
    if not _ADMIN_HTML.exists():
        return HTMLResponse(
            f"<h1>Admin panel not installed</h1><p>Expected file: {_ADMIN_HTML}</p>",
            status_code=500,
        )
    return HTMLResponse(_ADMIN_HTML.read_text(encoding="utf-8"))


# ============================================================
# Admin — aggregate (requires admin)
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

    user_rows = await session.execute(select(UserModel))
    users = list(user_rows.scalars().all())

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
# Admin — granular (all require admin)
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
    user_rows = await session.execute(select(UserModel))
    users = list(user_rows.scalars().all())

    plugin_rows = await session.execute(select(UserPluginModel))
    user_plugins = list(plugin_rows.scalars().all())

    return _collect_users(users, user_plugins)


@router.get("/users/{user_id}/plugins", dependencies=[Depends(get_current_admin)])
async def user_plugins(
    user_id: str,
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    result = await session.execute(
        select(UserPluginModel).where(UserPluginModel.user_id == user_id)
    )
    records = list(result.scalars().all())
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


def _plugin_manager_summary(kernel: Any) -> Dict[str, Any]:
    pm = getattr(kernel, "plugin_manager", None)
    if pm is None:
        return {"available": False}
    catalog = getattr(pm, "_catalog", None) or getattr(pm, "_plugins", {})
    return {
        "initialized": getattr(pm, "_initialized", False),
        "catalog_size": len(catalog),
    }


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

    devices: List[Dict[str, Any]] = []
    try:
        all_devices = await dm.registry.all()
    except TypeError:
        all_devices = await dm.registry.all("anonymous")
    except Exception as exc:
        logger.warning("device list failed: %s", exc)
        return []

    connections = getattr(dm, "_connections", {})

    for d in all_devices:
        device_id = getattr(d, "id", None) or getattr(d, "device_id", None) or str(d)
        connected = device_id in connections
        devices.append({
            "id": device_id,
            "name": getattr(d, "name", None) or device_id,
            "platform": getattr(d, "platform", None),
            "architecture": getattr(d, "architecture", None),
            "device_type": getattr(d, "device_type", None),
            "status": getattr(d, "status", "unknown"),
            "connected": connected,
            "last_seen": _iso(getattr(d, "last_seen", None)),
            "hostname": getattr(d, "hostname", None),
            "app_version": getattr(d, "app_version", None),
        })
    return devices


def _collect_plugins(kernel: Any, installs: list) -> List[Dict[str, Any]]:
    """
    Build the plugin list from the device-plugin registry on disk,
    joined with the device_plugins table.

    Server-side Python plugins (pai/plugins/) are not shown here —
    they aren't installable on devices and have nothing to do with
    the admin panel's plugin view.
    """
    from pathlib import Path
    import json

    registry_root = (
        Path(__file__).resolve().parent.parent.parent / "plugin_registry"
    )

    # Load every (plugin_id, version) from disk.
    catalog: Dict[str, Dict[str, Any]] = {}

    if registry_root.exists():
        for plugin_dir in sorted(registry_root.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue

            plugin_id = plugin_dir.name
            latest_version = None
            latest_manifest: Dict[str, Any] = {}

            for version_dir in sorted(plugin_dir.iterdir()):
                if not version_dir.is_dir():
                    continue
                manifest_path = version_dir / "manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text())
                except Exception as exc:
                    logger.warning("bad manifest %s: %s", manifest_path, exc)
                    continue

                version = manifest.get("version") or version_dir.name
                # Highest version wins.
                if latest_version is None or version > latest_version:
                    latest_version = version
                    latest_manifest = manifest

            if latest_version:
                catalog[plugin_id] = {
                    "id": plugin_id,
                    "name": latest_manifest.get("name", plugin_id),
                    "version": latest_version,
                    "description": latest_manifest.get("description", ""),
                    "platforms": list(
                        (latest_manifest.get("platforms") or {}).keys()
                    ),
                    "capabilities": [
                        c.get("id")
                        for c in latest_manifest.get("capabilities", [])
                        if isinstance(c, dict)
                    ],
                }

    # Group device_plugins rows by plugin_id.
    per_plugin: Dict[str, list] = {}
    for row in installs:
        per_plugin.setdefault(row.plugin_id, []).append(row)

    # Make sure every plugin that appears in the installs table
    # is in the response, even if the registry directory is gone.
    for plugin_id in per_plugin.keys():
        if plugin_id not in catalog:
            catalog[plugin_id] = {
                "id": plugin_id,
                "name": plugin_id,
                "version": "",
                "description": "(registry manifest missing)",
                "platforms": [],
                "capabilities": [],
            }

    out: List[Dict[str, Any]] = []
    for plugin_id, info in catalog.items():
        rows = per_plugin.get(plugin_id, [])
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