from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pai.api.dependencies import get_current_user
from pai.app_context import PAIAppContext, get_app_context


router = APIRouter(prefix="/plugins", tags=["plugins"])
logger = logging.getLogger(__name__)


# ============================================================
# Request Models
# ============================================================

class PluginInstallRequest(BaseModel):
    device_id: str


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

REGISTRY_ROOT = Path(__file__).resolve().parent.parent.parent / "plugin_registry"


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

@router.get("/catalog")
async def get_catalog(
    platform: str,
    architecture: str,
    request: Request,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    index = await registry_index(request)

    plugins = []
    for entry in index["plugins"]:
        latest = entry["latest"]
        vinfo = entry["versions"][latest]
        if platform not in vinfo["artifacts"]:
            continue

        manifest = json.loads(
            (REGISTRY_ROOT / entry["id"] / latest / "manifest.json").read_text()
        )

        plugins.append({
            "id": entry["id"],
            "name": entry["name"],
            "version": latest,
            "description": manifest.get("description", ""),
            "platforms": list(vinfo["artifacts"].keys()),
            "architectures": [architecture],
            "capabilities": [c["id"] for c in manifest.get("capabilities", [])],
            "package_url": vinfo["artifacts"][platform],
            "checksum": vinfo["sha256"][platform],
            "size": vinfo["sizeBytes"][platform],
            "state": "available",
        })

    return {"plugins": plugins}


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

    try:
        plugin = await plugin_manager.require_plugin(plugin_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    device = await device_manager.get(payload.device_id)
    if device is None:
        raise HTTPException(404, f"Device '{payload.device_id}' not found")

    if not await device_manager.is_connected(payload.device_id):
        raise HTTPException(409, f"Device '{payload.device_id}' is not connected")

    device_platform = getattr(device, "platform", None)
    device_architecture = getattr(device, "architecture", None)

    if device_platform and device_architecture:
        if not await plugin_manager.is_compatible(
            plugin_id=plugin_id,
            platform=device_platform,
            architecture=device_architecture,
        ):
            raise HTTPException(
                400,
                f"Plugin '{plugin_id}' not compatible with "
                f"{device_platform}/{device_architecture}",
            )

    command = await plugin_manager.build_install_request(
        plugin_id=plugin_id,
        device_id=payload.device_id,
    )

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
        "version": plugin.version,
        "status": "install_requested",
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
    return context.orchestrator.plugin_manager.get_status()