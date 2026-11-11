from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pai.api.dependencies import get_current_admin, get_current_user
from pai.app_context import PAIAppContext, get_app_context


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
    """Body for enable/disable — device_id is optional but recommended."""
    device_id: Optional[str] = None


# ============================================================
# Helpers
# ============================================================

def _get_user_id(user_id: Optional[str]) -> str:
    return user_id or "anonymous"


# ============================================================
# Device plugin registry (index / manifest / artifact)
# ============================================================
#
# Declared BEFORE the /{plugin_id} catch-all so /index.json
# doesn't get swallowed by it.

REGISTRY_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "plugin_registry"

# Check if the registry root exists, and raise error it if it doesn't

if not REGISTRY_ROOT.exists():
    raise RuntimeError(f"Plugin registry root does not exist: {REGISTRY_ROOT}")

def _iter_registry() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not REGISTRY_ROOT.exists():
        return out

    for plugin_dir in sorted(REGISTRY_ROOT.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        plugin_id = plugin_dir.name
        versions: Dict[str, Any] = {}

        for version_dir in sorted(plugin_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            manifest_path = version_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception as exc:
                logger.warning("Bad manifest %s: %s", manifest_path, exc)
                continue
            version = manifest.get("version") or version_dir.name
            versions[version] = {
                "manifest": manifest,
                "version_dir": version_dir,
            }

        if versions:
            out[plugin_id] = versions

    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifact_for_platform(
    version_dir: Path,
    platform: str,
    manifest: Dict[str, Any],
) -> Optional[Path]:
    spec = (manifest.get("platforms") or {}).get(platform)
    if isinstance(spec, dict):
        rel = spec.get("artifact")
        if rel:
            p = version_dir / rel
            if p.exists():
                return p

    ext = {
        "linux": ".so",
        "android": ".so",
        "windows": ".dll",
        "macos": ".dylib",
    }.get(platform)

    if ext:
        for candidate in version_dir.rglob(f"*{ext}"):
            return candidate
    return None


@router.get("/index.json")
async def registry_index(request: Request) -> Dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    registry = _iter_registry()

    plugins_out = []
    for plugin_id, versions in registry.items():
        latest = max(versions.keys())

        versions_out: Dict[str, Any] = {}
        for version, vinfo in versions.items():
            manifest = vinfo["manifest"]
            version_dir: Path = vinfo["version_dir"]

            platforms = list((manifest.get("platforms") or {}).keys())
            artifacts: Dict[str, str] = {}
            sha: Dict[str, str] = {}
            sizes: Dict[str, int] = {}

            for platform in platforms:
                artifact_path = _artifact_for_platform(version_dir, platform, manifest)
                if artifact_path is None:
                    continue
                artifacts[platform] = (
                    f"{base}/api/v1/plugins/{plugin_id}/{version}/artifact/{platform}"
                )
                sha[platform] = _sha256_file(artifact_path)
                sizes[platform] = artifact_path.stat().st_size

            versions_out[version] = {
                "manifestUrl": f"{base}/api/v1/plugins/{plugin_id}/{version}/manifest.json",
                "artifacts": artifacts,
                "sha256": sha,
                "sizeBytes": sizes,
            }

        plugins_out.append({
            "id": plugin_id,
            "name": next(iter(versions.values()))["manifest"].get("name", plugin_id),
            "latest": latest,
            "versions": versions_out,
        })

    return {"schemaVersion": 1, "updatedAt": None, "plugins": plugins_out}


@router.get("/{plugin_id}/{version}/manifest.json")
async def registry_manifest(plugin_id: str, version: str) -> JSONResponse:
    path = REGISTRY_ROOT / plugin_id / version / "manifest.json"
    if not path.exists():
        raise HTTPException(404, f"manifest not found: {plugin_id}@{version}")
    return JSONResponse(content=json.loads(path.read_text()))


@router.get("/{plugin_id}/{version}/artifact/{platform}")
async def registry_artifact(plugin_id: str, version: str, platform: str):
    version_dir = REGISTRY_ROOT / plugin_id / version
    manifest_path = version_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, f"plugin not found: {plugin_id}@{version}")

    manifest = json.loads(manifest_path.read_text())
    artifact = _artifact_for_platform(version_dir, platform, manifest)
    if artifact is None:
        raise HTTPException(404, f"no artifact for {plugin_id}@{version} on {platform}")

    return FileResponse(
        path=artifact,
        media_type="application/octet-stream",
        filename=artifact.name,
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
# Catalog
# ============================================================

def _enum_value(v: Any) -> Optional[str]:
    """DeviceInfo fields are enums; stringify to their .value."""
    if v is None:
        return None
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def _plugin_compatible(
    manifest: Dict[str, Any],
    vinfo: Dict[str, Any],
    platform: str,
    architecture: str,
    device_type: str,
) -> bool:
    """
    A plugin is compatible iff:
      - the manifest publishes an artifact for this platform
      - the device_type is declared in the manifest and matches
      - the architecture is declared AND matches (or the manifest
        declares no architectures, meaning "any")
    """
    if not platform or platform not in (vinfo.get("artifacts") or {}):
        return False

    declared_types = manifest.get("device_types") or []
    if not device_type or device_type not in declared_types:
        return False

    declared_archs = manifest.get("architectures") or []
    if declared_archs and architecture and architecture not in declared_archs:
        return False

    return True


@router.get("/catalog")
async def get_catalog(
    request: Request,
    platform: Optional[str] = Query(None),
    architecture: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None, description="If set, resolve platform/arch/type from this device"),
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Return plugins grouped by device compatibility.

    Response shape:
        {
          "this_device": {..., "plugins": [compatible plugins]},
          "other_devices": [
            {..., "plugins": [compatible plugins on that device]},
            ...
          ]
        }

    Resolving:
      - If `device_id` is set, its platform/arch/type override the query
        params, and its install state is joined from `device_plugins`.
      - Otherwise `platform`, `architecture`, `device_type` must be
        supplied directly.
    """
    dm = context.orchestrator.device_manager

    # ---- 1. Resolve current device ----
    this_device = None
    if device_id:
        this_device = await dm.get(device_id)
        if this_device is not None:
            platform = _enum_value(this_device.platform) or platform
            architecture = this_device.architecture or architecture
            device_type = _enum_value(this_device.device_type) or device_type

    if not platform or not architecture or not device_type:
        raise HTTPException(
            400,
            "Either device_id, or (platform, architecture, device_type) "
            "must be provided",
        )

    # ---- 2. Load index + manifests ----
    index = await registry_index(request)

    manifests: Dict[str, Dict[str, Any]] = {}
    for entry in index["plugins"]:
        pid = entry["id"]
        latest = entry["latest"]
        path = REGISTRY_ROOT / pid / latest / "manifest.json"
        if path.exists():
            try:
                manifests[pid] = json.loads(path.read_text())
            except Exception as exc:
                logger.warning("bad manifest %s: %s", path, exc)

    # ---- 3. Fetch this user's other devices ----
    try:
        user_devices = await dm.list_devices(user_id)
    except Exception as exc:
        logger.warning("list_devices failed for %s: %s", user_id, exc)
        user_devices = []

    other_devices = [d for d in user_devices if d.id != device_id]

    # ---- 4. Batch-fetch install state for every relevant device ----
    install_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_device_ids = [did for did in [device_id, *(d.id for d in other_devices)] if did]

    if all_device_ids and context.database is not None:
        try:
            from sqlalchemy import select as sa_select
            from pai.storage.models import DevicePluginModel

            rows = []
            async with context.database.session() as session:
                result = await session.execute(sa_select(DevicePluginModel).where(
                                        DevicePluginModel.device_id.in_(all_device_ids)
                                    ))
                rows = list(result.scalars().all())
            for row in rows:
                install_map[(row.device_id, row.plugin_id)] = {
                    "is_installed": row.is_installed,
                    "enabled_on_device": row.enabled_on_device,
                    "installed_version": row.version,
                }
        except Exception as exc:
            logger.warning("install-state fetch failed: %s", exc)

    # ---- 5. Classify each plugin ----
    def _serialize(
        entry: Dict[str, Any],
        manifest: Dict[str, Any],
        vinfo: Dict[str, Any],
        plat: str,
        install_info: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        out = {
            "id": entry["id"],
            "name": entry["name"],
            "version": entry["latest"],
            "description": manifest.get("description", ""),
            "platforms": list(vinfo["artifacts"].keys()),
            "architectures": list(manifest.get("architectures") or []),
            "device_types": list(manifest.get("device_types") or []),
            "capabilities": manifest.get("capabilities") or [],
            "package_url": vinfo["artifacts"].get(plat),
            "checksum": vinfo["sha256"].get(plat),
            "size": vinfo["sizeBytes"].get(plat, 0),
            "state": "available",
            "is_installed": False,
            "enabled_on_device": False,
            "installed_version": None,
        }
        if install_info:
            out.update(install_info)
            if install_info.get("is_installed"):
                out["state"] = (
                    "enabled" if install_info.get("enabled_on_device")
                    else "disabled"
                )
        return out

    this_plugins: List[Dict[str, Any]] = []
    per_other: Dict[str, List[Dict[str, Any]]] = {d.id: [] for d in other_devices}

    for entry in index["plugins"]:
        pid = entry["id"]
        latest = entry["latest"]
        vinfo = entry["versions"][latest]
        manifest = manifests.get(pid)
        if not manifest:
            continue

        # This device?
        if _plugin_compatible(manifest, vinfo, platform, architecture, device_type):
            this_plugins.append(_serialize(
                entry, manifest, vinfo, platform,
                install_map.get((device_id or "", pid)),
            ))
            continue

        # Other devices?
        for d in other_devices:
            d_plat = _enum_value(d.platform)
            d_arch = d.architecture or ""
            d_type = _enum_value(d.device_type) or ""

            if _plugin_compatible(manifest, vinfo, d_plat, d_arch, d_type):
                per_other[d.id].append(_serialize(
                    entry, manifest, vinfo, d_plat,
                    install_map.get((d.id, pid)),
                ))

    return {
        "this_device": {
            "device_id": device_id,
            "name": this_device.name if this_device else None,
            "platform": platform,
            "architecture": architecture,
            "device_type": device_type,
            "plugins": this_plugins,
        },
        "other_devices": [
            {
                "device_id": d.id,
                "name": d.name,
                "platform": _enum_value(d.platform),
                "architecture": d.architecture,
                "device_type": _enum_value(d.device_type),
                "connected": await dm.is_connected(d.id),
                "plugins": per_other[d.id],
            }
            for d in other_devices
            if per_other[d.id]
        ],
    }
# ============================================================
# List plugins
# ============================================================

@router.get("")
async def list_plugins(
    user_id: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    architecture: Optional[str] = Query(None),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    user_id = _get_user_id(user_id)
    plugins = await context.orchestrator.plugin_manager.list_plugins(
        user_id=user_id,
        platform=platform,
        architecture=architecture,
        device_type=None, 
    )
    return {
        "plugins": plugins,
        "total": len(plugins),
        "enabled": sum(1 for p in plugins if p.get("is_enabled", False)),
    }


# ============================================================
# Get plugin
# ============================================================

@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    plugin = await context.orchestrator.plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return context.orchestrator.plugin_manager._serialize_plugin(plugin)


# ============================================================
# Install on device
# ============================================================

@router.post("/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    payload: PluginInstallRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    plugin_manager = context.orchestrator.plugin_manager
    device_manager = context.orchestrator.device_manager

    # ---- resolve the version ----
    if payload.version:
        # explicit version — read the manifest at that path,
        # bypassing the (potentially stale) catalog cache
        manifest = await plugin_manager.get_manifest_at_version(
            plugin_id, payload.version
        )
        if manifest is None:
            raise HTTPException(
                404,
                f"Plugin '{plugin_id}@{payload.version}' not found "
                f"in registry",
            )
        version = payload.version
    else:
        try:
            catalog_entry = await plugin_manager.require_plugin(plugin_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        version = catalog_entry.version
        manifest = await plugin_manager.get_manifest_at_version(plugin_id, version)
        if manifest is None:
            # catalog says this version exists but disk disagrees
            raise HTTPException(
                500,
                f"Plugin '{plugin_id}@{version}' is in catalog but "
                f"manifest missing on disk",
            )

    device = await device_manager.get(payload.device_id)
    if device is None:
        raise HTTPException(404, f"Device '{payload.device_id}' not found")

    if not await device_manager.is_connected(payload.device_id):
        raise HTTPException(409, f"Device '{payload.device_id}' is not connected")

    device_platform = getattr(device, "platform", None)
    device_architecture = getattr(device, "architecture", None)
    device_type = getattr(device, "device_type", None)

    if device_platform and device_architecture and device_type:
        if not await plugin_manager.is_compatible(
            plugin_id=plugin_id,
            platform=device_platform,
            architecture=device_architecture,
            device_type=device_type,
        ):
            raise HTTPException(
                400,
                f"Plugin '{plugin_id}' not compatible with "
                f"{device_platform}/{device_architecture}",
            )

        # ---- build the WS command from the *resolved* manifest ----
    # We can't use plugin_manager.build_install_request() here because
    # it reads from the catalog (`require_plugin(plugin_id)`), which only
    # holds the latest version. For an explicit `browser@1.0.0` request
    # after a bump to 1.0.1, that would push 1.0.1 over WS and ignore
    # the pin. Build from `manifest` instead — it's the manifest of the
    # version we actually resolved above.
    capabilities = [
        c.get("id") or c.get("name")
        for c in manifest.get("capabilities") or []
        if isinstance(c, dict)
    ]
    command = {
        "type": "plugin.install",
        "request_id": f"install:{plugin_id}:{payload.device_id}",
        "device_id": payload.device_id,
        "plugin": {
            "id": plugin_id,
            "name": manifest.get("name", plugin_id),
            "version": version,
            "platforms": list((manifest.get("platforms") or {}).keys()),
            "architectures": manifest.get("architectures") or [],
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
        plugin = await pm.require_plugin(plugin_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    device = await dm.get(payload.device_id)
    if device is None:
        raise HTTPException(404, f"Device '{payload.device_id}' not found")

    if not await dm.is_connected(payload.device_id):
        raise HTTPException(409, f"Device '{payload.device_id}' is not connected")

    # Disable first so the sync loop doesn't race-reinstall after the
    # agent removes the files. Without this, _sync_once sees
    # enabled_on_device=true + is_installed=false and immediately
    # installs it back.
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
# Enable — reads user from JWT, updates both user_plugins and device_plugins
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


# ============================================================
# Disable — same pattern
# ============================================================

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
# Plugin registry reload
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

# ============================================================
# Config — reads user from JWT
# ============================================================

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