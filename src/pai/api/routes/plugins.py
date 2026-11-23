from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pai.api.dependencies import get_current_admin, get_current_user
from pai.app_context import PAIAppContext, get_app_context
from pai.plugins.manifest import PluginManifest
from pai.plugins.manifest_info import PluginInfo
from pai.plugins.store import LocalArtifactStore


router = APIRouter(prefix="/plugins", tags=["plugins"])
logger = logging.getLogger(__name__)


# ============================================================
# Request Models
# ============================================================

class PluginInstallRequest(BaseModel):
    device_id: str
    version: Optional[str] = None


class PluginConfigRequest(BaseModel):
    config: Dict[str, Any]


class PluginDeviceRequest(BaseModel):
    device_id: Optional[str] = None


# ============================================================
# Helpers
# ============================================================

def _get_user_id(user_id: Optional[str]) -> str:
    return user_id or "anonymous"


@router.get("/catalog",
            response_model=dict[str, list[PluginInfo]],
            status_code=200)
async def registry_index(
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
    ) -> Dict[str,list[PluginInfo]]:
    device_manager = context.orchestrator.device_manager
    plugin_manager = context.orchestrator.plugin_manager

    user_devices = await device_manager.list_devices(user_id=user_id)

    if not user_devices:
        raise HTTPException(404, f"No devices found for user '{user_id}'")    

    res = {}
    try:
        for device in user_devices:
            device_type = device.device_type
            device_platform = device.platform
            device_architecture = device.architecture

            # Get the plugin index for the current device
            plugins = await plugin_manager.get_plugin_index_for_device(
                device_type=device_type,
                device_platform=device_platform,
                device_architecture=device_architecture,
            )
            res[device.id] = plugins
            
    except Exception as exc:
        logger.warning("Failed to get plugin index for user '%s': %s", user_id, exc)
        raise HTTPException(500, f"Failed to get plugin index for user '{user_id}'") from exc
    
    if not res:
        raise HTTPException(404, f"No plugins found for user '{user_id}'")

    return res


@router.get("/{plugin_id}/manifest.json")
async def get_manifest(
    plugin_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> PluginManifest:
    if not plugin_id:
        raise HTTPException(400, "plugin_id is required")
    
    plugin_manager = context.orchestrator.plugin_manager

    plugin_manifest = plugin_manager.get_manifest(plugin_id)
    if not plugin_manifest:
        raise HTTPException(404, f"plugin manifest not found: {plugin_id}")

    return plugin_manifest


@router.get("/artifacts/{artifact_path:path}")
async def get_artifact(
    artifact_path: str,
    context: PAIAppContext = Depends(get_app_context),
):
    if not artifact_path:
        raise HTTPException(status_code=400, detail="artifact_path is required")

    if not context.orchestrator.plugin_artifact_store:
        raise HTTPException(status_code=500, detail="Plugin artifact store is not configured")

    if not isinstance(context.orchestrator.plugin_artifact_store, LocalArtifactStore):
        raise HTTPException(status_code=500, detail="Plugin artifact store is not a LocalArtifactStore")
    
    path = context.orchestrator.plugin_artifact_store.get_artifact_path(artifact_path)

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(
        path=path,
        media_type="application/gzip",
        filename=path.name,
    )

# ============================================================
# Device reconciliation
# ============================================================

class DevicePluginReport(BaseModel):
    id: str
    version: str
    enabled: bool = False
    sha256: Optional[str] = None


class DeviceReconcileRequest(BaseModel):
    plugins: list[DevicePluginReport]


@router.post("/device/{device_id}/reconcile")
async def reconcile_device(
    device_id: str,
    payload: DeviceReconcileRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    try:
        result = await pm.reconcile_device_plugins(
            device_id=device_id,
            device_plugins=[p.model_dump() for p in payload.plugins],
        )
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True, "device_id": device_id, **result}


# ============================================================
# List plugins
# ============================================================

@router.get("")
async def list_plugins(
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    user_id = _get_user_id(user_id)
    plugins = await context.orchestrator.plugin_manager.list_user_plugins(
        user_id=user_id
    )
    return {
        "plugins": plugins,
        "total": len(plugins),
        "enabled": sum(1 for p in plugins if p.is_enabled),
    }


# ============================================================
# Get plugin
# ============================================================

@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> PluginInfo:
    plugin = context.orchestrator.plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return plugin


# ============================================================
# Install on device — per-platform aware
# ============================================================

@router.post("/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    payload: PluginInstallRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    plugin_manager = context.orchestrator.plugin_manager
    device_manager = context.orchestrator.device_manager

    # ---- 1. Resolve the target device FIRST (we need its platform) ----
    device = await device_manager.get(payload.device_id)
    if device is None:
        raise HTTPException(404, f"Device '{payload.device_id}' not found")

    if not await device_manager.is_connected(payload.device_id):
        raise HTTPException(409, f"Device '{payload.device_id}' is not connected")

    device_platform = device.platform
    device_architecture = device.architecture
    device_type = device.device_type


    # ---- 2. Resolve version ----
    if payload.version:
        version = payload.version
    else:
        try:
            catalog_entry = plugin_manager.require_plugin(plugin_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        version = catalog_entry.version

    # ---- 3. Fetch the per-platform manifest ----
    manifest = plugin_manager.get_manifest(plugin_id)
    if manifest is None:
        raise HTTPException(
            404,
            f"Plugin '{plugin_id}@{version}' has no manifest "
            f"for platform '{device_platform}'",
        )

    # ---- 4. Compatibility — against the per-platform manifest ----
    declared_types = manifest.deviceTypes
    declared_archs = manifest.architectures

    if declared_types and device_type and device_type not in declared_types:
        raise HTTPException(
            400,
            f"Plugin '{plugin_id}' is not compatible with "
            f"device_type '{device_type}' (declared: {declared_types})",
        )
    if declared_archs and device_architecture \
            and device_architecture not in declared_archs:
        raise HTTPException(
            400,
            f"Plugin '{plugin_id}' is not compatible with "
            f"architecture '{device_architecture}' "
            f"(declared: {declared_archs})",
        )

    # ---- 5. Build the WS command from the per-platform manifest ----
    capabilities: List[str] = []
    for c in manifest.capabilities:
        if isinstance(c, str):
            capabilities.append(c)
        elif isinstance(c, dict):
            cid = c.get("id") or c.get("name")
            if cid:
                capabilities.append(cid)

    command = {
        "type": "plugin.install",
        "request_id": f"install:{plugin_id}:{payload.device_id}",
        # Top-level mirrors for the Dart command router.
        "plugin_id": plugin_id,
        "version": version,
        "device_id": payload.device_id,
        # Structured payload.
        "plugin": {
            "id": plugin_id,
            "name": manifest.name,
            "version": version,
            "type": manifest.type,
            "platforms": [device_platform],
            "architectures": declared_archs,
            "capabilities": capabilities,
        },
    }

    try:
        await device_manager.send(payload.device_id, command)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    await plugin_manager.record_device_error(
        device_id=payload.device_id,
        plugin_id=plugin_id,
        error="install_requested",
    )

    return {
        "success": True,
        "plugin_id": plugin_id,
        "device_id": payload.device_id,
        "version": version,
        "status": "install_requested",
    }


class PluginUninstallRequest(BaseModel):
    device_id: str


@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(
    plugin_id: str,
    payload: PluginUninstallRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    dm = context.orchestrator.device_manager

    try:
        plugin = pm.require_plugin(plugin_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    device = await dm.get(payload.device_id)
    if device is None:
        raise HTTPException(404, f"Device '{payload.device_id}' not found")

    if not await dm.is_connected(payload.device_id):
        raise HTTPException(409, f"Device '{payload.device_id}' is not connected")

    await pm.set_device_enabled(
        device_id=payload.device_id, plugin_id=plugin_id, enabled=False,
    )

    command = await pm.build_uninstall_request(plugin_id, payload.device_id)
    try:
        await dm.send(payload.device_id, command)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    return {
        "success": True,
        "plugin_id": plugin_id,
        "device_id": payload.device_id,
        "version": plugin.version,
        "status": "uninstall_requested",
    }


# ============================================================
# Install / uninstall reports
# ============================================================

class PluginInstallReport(BaseModel):
    device_id: str
    version: str
    success: bool
    artifact_sha256: Optional[str] = None
    error: Optional[str] = None


class PluginUninstallReport(BaseModel):
    device_id: str
    success: bool
    error: Optional[str] = None


@router.post("/{plugin_id}/report-install")
async def report_install(
    plugin_id: str,
    payload: PluginInstallReport,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager

    if payload.success:
        await pm.mark_installed_on_device(
            device_id=payload.device_id,
            plugin_id=plugin_id,
            version=payload.version,
            artifact_sha256=payload.artifact_sha256,
        )
    else:
        await pm.record_device_error(
            device_id=payload.device_id,
            plugin_id=plugin_id,
            error=payload.error or "install_failed",
        )

    return {"ok": True, "plugin_id": plugin_id, "device_id": payload.device_id}


@router.post("/{plugin_id}/report-uninstall")
async def report_uninstall(
    plugin_id: str,
    payload: PluginUninstallReport,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    if payload.success:
        await pm.mark_uninstalled_on_device(payload.device_id, plugin_id)
    return {"ok": True}


@router.get("/device/{device_id}/installed")
async def list_installed_on_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    return {"device_id": device_id, "plugins": await pm.list_device_plugins(device_id)}


# ============================================================
# Enable / disable
# ============================================================

@router.post("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    payload: PluginDeviceRequest = PluginDeviceRequest(),
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager

    try:
        await pm.enable(user_id=user_id, plugin_id=plugin_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    if payload.device_id:
        try:
            await pm.set_device_enabled(
                device_id=payload.device_id,
                plugin_id=plugin_id,
                enabled=True,
            )
        except Exception as exc:
            logger.warning("failed to set device enabled flag: %s", exc)

    return {"plugin_id": plugin_id, "user_id": user_id, "status": "enabled"}


@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    payload: PluginDeviceRequest = PluginDeviceRequest(),
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager

    try:
        await pm.disable(user_id=user_id, plugin_id=plugin_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    if payload.device_id:
        try:
            await pm.set_device_enabled(
                device_id=payload.device_id,
                plugin_id=plugin_id,
                enabled=False,
            )
        except Exception as exc:
            logger.warning("failed to clear device enabled flag: %s", exc)

    return {"plugin_id": plugin_id, "user_id": user_id, "status": "disabled"}


# ============================================================
# Reload / config / status
# ============================================================

@router.post("/system/reload")
async def reload_plugins(
    context: PAIAppContext = Depends(get_app_context),
    _admin: str = Depends(get_current_admin),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    pm._catalog.clear()
    await pm._on_initialize()
    return {"reloaded": True, "plugin_count": len(pm._catalog)}


@router.get("/{plugin_id}/config")
async def get_plugin_config(
    plugin_id: str,
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    try:
        config = await context.orchestrator.plugin_manager.get_user_plugin_config(
            user_id=user_id, plugin_id=plugin_id,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"plugin_id": plugin_id, "user_id": user_id, "config": config}


@router.post("/{plugin_id}/config")
async def set_plugin_config(
    plugin_id: str,
    payload: PluginConfigRequest,
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    try:
        await context.orchestrator.plugin_manager.set_user_plugin_config(
            user_id=user_id, plugin_id=plugin_id, metadata=payload.config,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "plugin_id": plugin_id,
        "user_id": user_id,
        "status": "saved",
        "config": payload.config,
    }


@router.get("/system/status")
async def plugin_manager_status(
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    return await context.orchestrator.plugin_manager.get_status()