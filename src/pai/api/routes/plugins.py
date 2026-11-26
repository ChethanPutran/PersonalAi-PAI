from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from pai.api.dependencies import get_current_admin, get_current_user, get_source_device_id
from pai.app_context import PAIAppContext, get_app_context
from pai.plugins.manifest import PluginManifest
from pai.plugins.manifest_info import PluginInfo
from pai.plugins.store import LocalArtifactStore
from pai.plugins.updates import build_update_info, version_summary
from pai.security.audit.models import AuditEventType
from pai.security.remote_access import DEVICE_NOT_FOUND, remote_access_denial
from pai.version import MANIFEST_SCHEMA_MAX, MANIFEST_SCHEMA_MIN, MIN_SUPPORTED_APP_VERSION, RUNTIME_VERSION


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
# Authorization helpers
# ============================================================

async def _owned_device(context: PAIAppContext, user_id: str, device_id: str):
    """The device, if it is the caller's. Someone else's device is "not found" (never confirm it exists)."""
    device = await context.orchestrator.device_manager.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:
        raise HTTPException(404, f"Device '{device_id}' not found")
    return device


async def _authorize_remote(
    context: PAIAppContext, *, user_id: str, device_id: str,
    source_device_id: Optional[str], plugin_id: Optional[str] = None, action: str = "install",
) -> None:
    """Owner-only, and acting on another device needs ITS "allow remote plugin install" switch."""
    denial = await remote_access_denial(
        context.orchestrator.device_manager, user_id=user_id, target_device_id=device_id,
        source_device_id=source_device_id, kind="install")
    if denial is None:
        return
    context.orchestrator.security_manager.audit_logger.record(
        AuditEventType.REMOTE_INSTALL_DENIED, user_id=user_id, plugin_id=plugin_id,
        capability=f"plugin.{action}", device_id=device_id, allowed=False, reason=denial.message)
    raise HTTPException(404 if denial.code == DEVICE_NOT_FOUND else 403,
                        detail={"code": denial.code, "message": denial.message})


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
                app_version=getattr(device, "app_version", None) or None,
            )
            res[device.id] = plugins
            
    except Exception as exc:
        logger.warning("Failed to get plugin index for user '%s': %s", user_id, exc)
        raise HTTPException(500, f"Failed to get plugin index for user '{user_id}'") from exc
    
    if not res:
        raise HTTPException(404, f"No plugins found for user '{user_id}'")

    return res


@router.get("/versions-info")
async def versions_info() -> Dict[str, Any]:
    """The versions this backend speaks (runtime, manifest schema range, min client app)."""
    return {
        "runtime_version": RUNTIME_VERSION,
        "manifest_schema": {"min": MANIFEST_SCHEMA_MIN, "max": MANIFEST_SCHEMA_MAX},
        "min_supported_app_version": MIN_SUPPORTED_APP_VERSION,
    }


@router.get("/updates")
async def plugin_updates(
    device_id: Optional[str] = None,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Per device: installed plugins that have a newer version, with release notes and the
    capability diff. Devices of other users are never reported."""
    pm = context.orchestrator.plugin_manager
    devices = await context.orchestrator.device_manager.list_devices(user_id=user_id)
    if device_id is not None:
        devices = [d for d in devices if d.id == device_id]
        if not devices:
            raise HTTPException(404, f"Device '{device_id}' not found")

    out: Dict[str, Any] = {}
    for d in devices:
        app_version = getattr(d, "app_version", None) or None
        items = []
        for rec in await pm.list_device_plugins(d.id):
            if not rec["is_installed"]:
                continue
            items.append(build_update_info(pm.registry, rec["plugin_id"], rec["version"], app_version))
        out[d.id] = {
            "app_version": app_version,
            "updates": [i for i in items if i["update_available"] or i["blocked"]],
            "plugins": items,
        }
    return {"runtime_version": RUNTIME_VERSION, "devices": out}


# ============================================================
# Operations: live status of remote installs (replaces polling)
# ============================================================

@router.get("/operations")
async def list_operations(
    device_id: Optional[str] = None,
    plugin_id: Optional[str] = None,
    active: bool = Query(False, description="only operations still running"),
    limit: int = Query(50, ge=1, le=200),
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """The caller's install/uninstall operations, newest first. The same objects are pushed live
    as ``{"type": "plugin.operation", "operation": {...}}`` on the device WebSocket; this is for
    first load and reconnects."""
    ops = context.orchestrator.plugin_manager.operations
    return {"operations": await ops.list(
        user_id=user_id, device_id=device_id, plugin_id=plugin_id, active_only=active, limit=limit)}


@router.get("/operations/{operation_id}")
async def get_operation(
    operation_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    op = await context.orchestrator.plugin_manager.operations.get(operation_id, user_id=user_id)
    if op is None:
        raise HTTPException(404, "Operation not found")
    return op


@router.get("/{plugin_id}/versions")
async def list_plugin_versions(
    plugin_id: str,
    device_id: Optional[str] = None,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """All published versions of a plugin with release notes + per-version compatibility
    (against this device's app version when ``device_id`` is given)."""
    pm = context.orchestrator.plugin_manager
    app_version = None
    if device_id:
        device = await context.orchestrator.device_manager.get(device_id)
        if device is None or getattr(device, "user_id", user_id) != user_id:
            raise HTTPException(404, f"Device '{device_id}' not found")
        app_version = getattr(device, "app_version", None) or None
    versions = version_summary(pm.registry, plugin_id, app_version)
    if not versions:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return {"plugin_id": plugin_id, "runtime_version": RUNTIME_VERSION, "versions": versions}


@router.get("/{plugin_id}/manifest.json")
async def get_manifest(
    plugin_id: str,
    version: Optional[str] = None,
    context: PAIAppContext = Depends(get_app_context),
) -> PluginManifest:
    if not plugin_id:
        raise HTTPException(400, "plugin_id is required")
    
    plugin_manager = context.orchestrator.plugin_manager

    plugin_manifest = plugin_manager.get_manifest(plugin_id, version)
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
    version: Optional[str] = None,
    context: PAIAppContext = Depends(get_app_context),
) -> PluginInfo:
    pm = context.orchestrator.plugin_manager
    plugin = pm.get_plugin_version(plugin_id, version) if version else pm.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(404, f"Plugin '{plugin_id}'{f'@{version}' if version else ''} not found")
    return plugin


# ============================================================
# Install on device — per-platform aware
# ============================================================

@router.post("/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    payload: PluginInstallRequest,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
    source_device_id: Optional[str] = Depends(get_source_device_id),
) -> Dict[str, Any]:
    """Ask a device to install a plugin. Returns at once with an ``operation``: follow it live
    (``plugin.operation`` frames on the device WebSocket) or via ``GET /plugins/operations/{id}``."""
    plugin_manager = context.orchestrator.plugin_manager
    device_manager = context.orchestrator.device_manager

    # ---- 0. Authorization: the user's own device, and (unless it is asking itself) the device
    # owner's "allow remote plugin install" switch on THAT device.
    await _authorize_remote(context, user_id=user_id, device_id=payload.device_id,
                            source_device_id=source_device_id, plugin_id=plugin_id)

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
    # Default = newest version THIS device can run (runtime + its app version), not merely the
    # newest published; an explicit version is validated the same way below.
    device_app_version = getattr(device, "app_version", None) or None
    if payload.version:
        version = payload.version
    else:
        try:
            plugin_manager.require_plugin(plugin_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        runnable = [
            i for i in plugin_manager.list_plugin_versions(plugin_id)
            if plugin_manager.registry.entry_compatibility(
                plugin_manager.get_manifest(plugin_id, i.version), device_app_version).compatible
        ]
        if not runnable:
            newest = plugin_manager.get_plugin(plugin_id)
            reason = plugin_manager.registry.entry_compatibility(
                plugin_manager.get_manifest(plugin_id, newest.version), device_app_version).reason
            raise HTTPException(409, f"No version of '{plugin_id}' can run here: {reason}")
        version = runnable[0].version

    # ---- 3. Fetch the manifest of the requested version ----
    manifest = plugin_manager.get_manifest(plugin_id, version)
    if manifest is None:
        raise HTTPException(
            404,
            f"Plugin '{plugin_id}@{version}' has no manifest "
            f"for platform '{device_platform}'",
        )

    # ---- 4a. Version compatibility (runtime / app / manifest schema) ----
    compat = plugin_manager.registry.entry_compatibility(manifest, device_app_version)
    if not compat.compatible:
        raise HTTPException(409, {
            "error": "incompatible_version",
            "plugin_id": plugin_id, "version": version, **compat.to_dict(),
        })

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

    operation = await plugin_manager.operations.create(
        user_id=user_id, device_id=payload.device_id, plugin_id=plugin_id, version=version,
        kind="install", requested_by_device=source_device_id)

    command = {
        "type": "plugin.install",
        "request_id": f"install:{plugin_id}:{payload.device_id}",
        "operation_id": operation["id"],
        "requested_by_device": source_device_id,     # the device enforces its own remote-install switch
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
        await plugin_manager.operations.fail(
            operation["id"], code="device_offline",
            message="The device went offline before the request reached it.",
            hint="Open the PAI app on that device and try again.", detail=str(exc))
        raise HTTPException(409, str(exc)) from exc

    await plugin_manager.operations.mark_delivered(operation["id"])
    context.orchestrator.security_manager.audit_logger.record(
        AuditEventType.REMOTE_INSTALL_REQUESTED, user_id=user_id, plugin_id=plugin_id,
        capability="plugin.install", device_id=payload.device_id, allowed=True,
        reason="remote plugin install requested",
        metadata={"version": version, "source_device_id": source_device_id,
                  "operation_id": operation["id"]})

    return {
        "success": True,
        "plugin_id": plugin_id,
        "device_id": payload.device_id,
        "version": version,
        "status": "install_requested",        # kept for older clients; follow `operation` instead
        "operation_id": operation["id"],
        "operation": operation,
    }


class PluginUninstallRequest(BaseModel):
    device_id: str


@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(
    plugin_id: str,
    payload: PluginUninstallRequest,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
    source_device_id: Optional[str] = Depends(get_source_device_id),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    dm = context.orchestrator.device_manager

    await _authorize_remote(context, user_id=user_id, device_id=payload.device_id,
                            source_device_id=source_device_id, plugin_id=plugin_id, action="uninstall")

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

    operation = await pm.operations.create(
        user_id=user_id, device_id=payload.device_id, plugin_id=plugin_id, version=plugin.version,
        kind="uninstall", requested_by_device=source_device_id)
    command = await pm.build_uninstall_request(plugin_id, payload.device_id)
    command["operation_id"] = operation["id"]
    command["requested_by_device"] = source_device_id
    try:
        await dm.send(payload.device_id, command)
    except RuntimeError as exc:
        await pm.operations.fail(
            operation["id"], code="device_offline",
            message="The device went offline before the request reached it.",
            hint="Open the PAI app on that device and try again.", detail=str(exc))
        raise HTTPException(409, str(exc)) from exc
    await pm.operations.mark_delivered(operation["id"])

    return {
        "success": True,
        "plugin_id": plugin_id,
        "device_id": payload.device_id,
        "version": plugin.version,
        "status": "uninstall_requested",
        "operation_id": operation["id"],
        "operation": operation,
    }


# ============================================================
# Install / uninstall reports
# ============================================================

class PluginInstallReport(BaseModel):
    device_id: str
    version: str
    operation_id: Optional[str] = None
    success: bool
    artifact_sha256: Optional[str] = None
    error: Optional[str] = None


class PluginUninstallReport(BaseModel):
    device_id: str
    operation_id: Optional[str] = None
    success: bool
    error: Optional[str] = None


@router.post("/{plugin_id}/report-install")
async def report_install(
    plugin_id: str,
    payload: PluginInstallReport,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    await _owned_device(context, user_id, payload.device_id)      # only the owner's device reports

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

    op_id = payload.operation_id or await pm.operations.active_for(payload.device_id, plugin_id)
    if op_id:
        await pm.operations.progress(
            op_id, device_id=payload.device_id,
            status="succeeded" if payload.success else "failed",
            error=payload.error or "install_failed", version=payload.version)

    return {"ok": True, "plugin_id": plugin_id, "device_id": payload.device_id}


@router.post("/{plugin_id}/report-uninstall")
async def report_uninstall(
    plugin_id: str,
    payload: PluginUninstallReport,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    await _owned_device(context, user_id, payload.device_id)
    if payload.success:
        await pm.mark_uninstalled_on_device(payload.device_id, plugin_id)
    op_id = payload.operation_id or await pm.operations.active_for(payload.device_id, plugin_id)
    if op_id:
        await pm.operations.progress(
            op_id, device_id=payload.device_id,
            status="succeeded" if payload.success else "failed",
            error=payload.error or "uninstall_failed")
    return {"ok": True}


class DevicePermissionsReport(BaseModel):
    """What the user has allowed on a device, per installed plugin: {plugin_id: {permission: state}}."""
    plugins: Dict[str, Dict[str, str]] = Field(default_factory=dict)

    @field_validator("plugins")
    @classmethod
    def _bounded(cls, v: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
        if len(v) > 128 or any(len(p) > 32 for p in v.values()):
            raise ValueError("too many plugins or permissions in one report")
        return v


@router.post("/device/{device_id}/permissions")
async def report_device_permissions(
    device_id: str,
    payload: DevicePermissionsReport,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """The device tells the server which permissions the user allowed for its installed plugins.

    Advisory (the device re-checks before every invoke): the server uses it to stop offering capabilities the
    device would refuse. Only the device's OWNER may report, so nobody can hide another user's plugins.
    """
    device = await context.orchestrator.device_manager.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:        # not yours = not found
        raise HTTPException(404, f"Device '{device_id}' not found")
    result = await context.orchestrator.plugin_manager.report_device_permissions(
        device_id=device_id, plugins=payload.plugins)
    return {"ok": True, "device_id": device_id, **result}


@router.get("/device/{device_id}/installed")
async def list_installed_on_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    await _owned_device(context, user_id, device_id)
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
    source_device_id: Optional[str] = Depends(get_source_device_id),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    if payload.device_id:        # changing a device's plugins: the user's own device, remote => opt-in
        await _authorize_remote(context, user_id=user_id, device_id=payload.device_id,
                                source_device_id=source_device_id, plugin_id=plugin_id, action="enable")

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
    source_device_id: Optional[str] = Depends(get_source_device_id),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    pm = context.orchestrator.plugin_manager
    if payload.device_id:        # changing a device's plugins: the user's own device, remote => opt-in
        await _authorize_remote(context, user_id=user_id, device_id=payload.device_id,
                                source_device_id=source_device_id, plugin_id=plugin_id, action="disable")

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