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
    device_id: Optional[str] = None


# ============================================================
# Helpers
# ============================================================

def _get_user_id(user_id: Optional[str]) -> str:
    return user_id or "anonymous"


def _load_platform_manifest(
    version_dir: Path, platform: str,
) -> Optional[Dict[str, Any]]:
    """
    Load `<version_dir>/<platform>/manifest.json`. The only supported
    layout — no legacy top-level fallback.
    """
    if not platform:
        return None
    per_platform = version_dir / platform / "manifest.json"
    if not per_platform.exists():
        return None
    try:
        return json.loads(per_platform.read_text())
    except Exception as exc:
        logger.warning("bad platform manifest %s: %s", per_platform, exc)
        return None


def _list_platform_manifests(version_dir: Path) -> Dict[str, Path]:
    """
    Return {platform: manifest_path} for every platform subdir that
    contains manifest.json. Hidden directories and plain files are
    skipped.
    """
    out: Dict[str, Path] = {}
    if not version_dir.is_dir():
        return out
    for child in sorted(version_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        mp = child / "manifest.json"
        if mp.exists():
            out[child.name] = mp
    return out


def _build_merged_manifest(version_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Discovery/debug view: merge all per-platform manifests into one dict.

    NOT used for install — clients must fetch `?platform=<x>` to get the
    correct `type` and artifact. This exists so `GET /manifest.json`
    (with no query param) returns something inspectable.
    """
    platform_manifests: Dict[str, Dict[str, Any]] = {}
    for plat, mp in _list_platform_manifests(version_dir).items():
        try:
            platform_manifests[plat] = json.loads(mp.read_text())
        except Exception:
            continue

    if not platform_manifests:
        return None

    first = platform_manifests[sorted(platform_manifests.keys())[0]]
    merged: Dict[str, Any] = {
        "id": first.get("id"),
        "name": first.get("name"),
        "version": first.get("version"),
        "description": first.get("description", ""),
        "type": "mixed",
        "platforms": {},
        "architectures": [],
        "device_types": [],
        "capabilities": [],
        "permissions": [],
    }

    for m in platform_manifests.values():
        for k, v in (m.get("platforms") or {}).items():
            merged["platforms"][k] = v
        for field in ("architectures", "device_types", "capabilities", "permissions"):
            for v in m.get(field) or []:
                if v not in merged[field]:
                    merged[field].append(v)

    for m in platform_manifests.values():
        if "runtime" in m:
            merged["runtime"] = m["runtime"]
            break

    return merged


# ============================================================
# Device plugin registry
# ============================================================

REGISTRY_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "plugin_registry"

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
            if not version_dir.is_dir() or version_dir.name.startswith("."):
                continue

            platform_manifests = _list_platform_manifests(version_dir)
            if not platform_manifests:
                continue

            # Read one for id/version metadata
            primary: Optional[Dict[str, Any]] = None
            for mp in platform_manifests.values():
                try:
                    primary = json.loads(mp.read_text())
                    break
                except Exception:
                    continue
            if primary is None:
                continue

            version = primary.get("version") or version_dir.name
            versions[version] = {
                "platform_manifests": platform_manifests,
                "version_dir": version_dir,
                "primary_data": primary,
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


_KNOWN_ABIS = {"arm64-v8a", "armeabi-v7a", "x86_64", "x86", "arm64"}


def _enumerate_abis(version_dir: Path, platform: str) -> List[str]:
    """
    Return ABI directories present under `<version_dir>/<platform>/`.
    Empty for single-artifact platforms (Linux/macOS/Windows) or for
    Android plugins that ship one artifact (e.g. a Kotlin `.dex`).
    """
    base = version_dir / platform
    if not base.is_dir():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and d.name in _KNOWN_ABIS
    )


def _artifact_for_platform(
    version_dir: Path,
    platform: str,
    abi: Optional[str] = None,
) -> Optional[Path]:
    """
    Resolve the artifact file for a platform (+ optional ABI) using the
    per-platform manifest's declared `artifact` path. Falls back to a
    recursive scan by extension.
    """
    manifest = _load_platform_manifest(version_dir, platform)
    if manifest is None:
        return None

    spec = (manifest.get("platforms") or {}).get(platform)
    declared: Optional[str] = None
    if isinstance(spec, dict):
        declared = spec.get("artifact")

    if declared:
        parts = Path(declared).parts

        # ABI-injected path: linux/lib.so → linux/<abi>/lib.so
        if abi and len(parts) == 2:
            candidate = version_dir / parts[0] / abi / parts[1]
            if candidate.exists():
                return candidate

        # ABI-baked path: android/arm64-v8a/lib.so → replace ABI segment
        if abi and len(parts) >= 3:
            patched = list(parts)
            for i, p in enumerate(patched):
                if p in _KNOWN_ABIS:
                    patched[i] = abi
                    candidate = version_dir.joinpath(*patched)
                    if candidate.exists():
                        return candidate
                    break

        p = version_dir / declared
        if p.exists():
            return p

    # Extension fallback (native artifacts only)
    ext = {
        "linux": ".so", "android": ".so", "windows": ".dll", "macos": ".dylib",
    }.get(platform)
    if ext:
        if abi:
            base = version_dir / platform / abi
            if base.is_dir():
                for c in base.rglob(f"*{ext}"):
                    return c
        base = version_dir / platform
        if base.is_dir():
            for c in base.rglob(f"*{ext}"):
                return c
        for c in version_dir.rglob(f"*{ext}"):
            return c

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
            version_dir: Path = vinfo["version_dir"]
            platform_manifests: Dict[str, Path] = vinfo["platform_manifests"]

            artifacts: Dict[str, str] = {}
            sha: Dict[str, str] = {}
            sizes: Dict[str, int] = {}

            artifacts_by_abi: Dict[str, Dict[str, str]] = {}
            sha_by_abi: Dict[str, Dict[str, str]] = {}
            sizes_by_abi: Dict[str, Dict[str, int]] = {}

            manifest_urls: Dict[str, str] = {}
            platform_types: Dict[str, str] = {}

            for platform in sorted(platform_manifests.keys()):
                manifest_urls[platform] = (
                    f"{base}/api/v1/plugins/{plugin_id}/{version}"
                    f"/manifest.json?platform={platform}"
                )
                try:
                    pm = json.loads(platform_manifests[platform].read_text())
                    platform_types[platform] = pm.get("type", "native")
                except Exception:
                    platform_types[platform] = "native"

                abis = _enumerate_abis(version_dir, platform)

                if abis:
                    per_abi_urls: Dict[str, str] = {}
                    per_abi_sha: Dict[str, str] = {}
                    per_abi_sizes: Dict[str, int] = {}
                    for abi in abis:
                        artifact_path = _artifact_for_platform(
                            version_dir, platform, abi=abi,
                        )
                        if artifact_path is None:
                            continue
                        per_abi_urls[abi] = (
                            f"{base}/api/v1/plugins/{plugin_id}/{version}"
                            f"/artifact/{platform}/{abi}"
                        )
                        per_abi_sha[abi] = _sha256_file(artifact_path)
                        per_abi_sizes[abi] = artifact_path.stat().st_size

                    if per_abi_urls:
                        artifacts_by_abi[platform] = per_abi_urls
                        sha_by_abi[platform] = per_abi_sha
                        sizes_by_abi[platform] = per_abi_sizes

                        first_abi = next(iter(per_abi_urls))
                        artifacts[platform] = per_abi_urls[first_abi]
                        sha[platform] = per_abi_sha[first_abi]
                        sizes[platform] = per_abi_sizes[first_abi]
                else:
                    artifact_path = _artifact_for_platform(version_dir, platform)
                    if artifact_path is None:
                        continue
                    artifacts[platform] = (
                        f"{base}/api/v1/plugins/{plugin_id}/{version}"
                        f"/artifact/{platform}"
                    )
                    sha[platform] = _sha256_file(artifact_path)
                    sizes[platform] = artifact_path.stat().st_size

            versions_out[version] = {
                "manifestUrls": manifest_urls,
                "platformTypes": platform_types,
                "manifestUrl": (
                    f"{base}/api/v1/plugins/{plugin_id}/{version}/manifest.json"
                ),
                "artifacts": artifacts,
                "sha256": sha,
                "sizeBytes": sizes,
                "artifactsByAbi": artifacts_by_abi,
                "sha256ByAbi": sha_by_abi,
                "sizeBytesByAbi": sizes_by_abi,
            }

        primary = next(iter(versions.values()))["primary_data"]
        plugins_out.append({
            "id": plugin_id,
            "name": primary.get("name", plugin_id),
            "latest": latest,
            "versions": versions_out,
        })

    return {"schemaVersion": 1, "updatedAt": None, "plugins": plugins_out}


@router.get("/{plugin_id}/{version}/manifest.json")
async def registry_manifest(
    plugin_id: str,
    version: str,
    platform: Optional[str] = Query(
        None,
        description="Required for installs. Per-platform manifest only.",
    ),
) -> JSONResponse:
    version_dir = REGISTRY_ROOT / plugin_id / version
    if not version_dir.exists():
        raise HTTPException(404, f"plugin not found: {plugin_id}@{version}")

    if platform:
        manifest = _load_platform_manifest(version_dir, platform)
        if manifest is None:
            raise HTTPException(
                404,
                f"manifest not found: {plugin_id}@{version} for platform {platform}",
            )
        return JSONResponse(content=manifest)

    # No platform — merged discovery view (not for install).
    merged = _build_merged_manifest(version_dir)
    if merged is None:
        raise HTTPException(404, f"no manifests under {version_dir}")
    return JSONResponse(content=merged)


@router.get("/{plugin_id}/{version}/artifact/{platform}/{abi}")
async def registry_artifact_with_abi(
    plugin_id: str, version: str, platform: str, abi: str,
):
    version_dir = REGISTRY_ROOT / plugin_id / version
    if not version_dir.exists():
        raise HTTPException(404, f"plugin not found: {plugin_id}@{version}")

    artifact = _artifact_for_platform(version_dir, platform, abi=abi)
    if artifact is None:
        raise HTTPException(
            404, f"no artifact for {plugin_id}@{version} on {platform}/{abi}",
        )
    return FileResponse(
        path=artifact, media_type="application/octet-stream",
        filename=artifact.name,
    )


@router.get("/{plugin_id}/{version}/artifact/{platform}")
async def registry_artifact(
    plugin_id: str, version: str, platform: str,
    abi: str = Query("arm64-v8a", description="Default ABI for legacy clients"),
):
    version_dir = REGISTRY_ROOT / plugin_id / version
    if not version_dir.exists():
        raise HTTPException(404, f"plugin not found: {plugin_id}@{version}")

    artifact = _artifact_for_platform(version_dir, platform, abi=abi)
    if artifact is None:
        raise HTTPException(
            404, f"no artifact for {plugin_id}@{version} on {platform}/{abi}",
        )
    return FileResponse(
        path=artifact, media_type="application/octet-stream",
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
    Compatibility against the *per-platform* manifest:
      - an artifact exists for this platform
      - device_type matches (if the manifest declares types)
      - architecture matches (if the manifest declares architectures)
    """
    if not platform or platform not in (vinfo.get("artifacts") or {}):
        return False

    declared_types = manifest.get("device_types") or []
    if declared_types and device_type and device_type not in declared_types:
        return False

    declared_archs = manifest.get("architectures") or []
    if declared_archs and architecture and architecture not in declared_archs:
        return False

    return True


@router.get("/catalog")
async def get_catalog(
    request: Request,
    abi: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    architecture: Optional[str] = Query(None),
    device_type: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None, description="If set, resolve platform/arch/type from this device"),
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
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

    # ---- 2. Load index ----
    index = await registry_index(request)

    # ---- 3. Fetch other devices ----
    try:
        user_devices = await dm.list_devices(user_id)
    except Exception as exc:
        logger.warning("list_devices failed for %s: %s", user_id, exc)
        user_devices = []

    other_devices = [d for d in user_devices if d.id != device_id]

    # ---- 4. Batch-fetch install state ----
    install_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_device_ids = [did for did in [device_id, *(d.id for d in other_devices)] if did]
    if all_device_ids and context.database is not None:
        try:
            from sqlalchemy import select as sa_select
            from pai.storage.models import DevicePluginModel
            async with context.database.session() as session:
                result = await session.execute(
                    sa_select(DevicePluginModel).where(
                        DevicePluginModel.device_id.in_(all_device_ids)
                    )
                )
                for row in result.scalars().all():
                    install_map[(row.device_id, row.plugin_id)] = {
                        "is_installed": row.is_installed,
                        "enabled_on_device": row.enabled_on_device,
                        "installed_version": row.version,
                    }
        except Exception as exc:
            logger.warning("install-state fetch failed: %s", exc)

    # ---- 5. Classify ----
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
            "type": manifest.get("type", "native"),
            "platforms": [plat],
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
        version_dir = REGISTRY_ROOT / pid / latest

        m = _load_platform_manifest(version_dir, platform)
        if m and _plugin_compatible(m, vinfo, platform, architecture, device_type):
            this_plugins.append(_serialize(
                entry, m, vinfo, platform,
                install_map.get((device_id or "", pid)),
            ))

        for d in other_devices:
            d_plat = _enum_value(d.platform)
            d_arch = d.architecture or ""
            d_type = _enum_value(d.device_type) or ""

            dm_ = _load_platform_manifest(version_dir, d_plat)
            if dm_ and _plugin_compatible(dm_, vinfo, d_plat, d_arch, d_type):
                per_other[d.id].append(_serialize(
                    entry, dm_, vinfo, d_plat,
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

    device_platform = _enum_value(getattr(device, "platform", None))
    device_architecture = getattr(device, "architecture", None)
    device_type = _enum_value(getattr(device, "device_type", None))

    if not device_platform:
        raise HTTPException(
            400,
            f"Device '{payload.device_id}' has no platform recorded",
        )

    # ---- 2. Resolve version ----
    if payload.version:
        version = payload.version
    else:
        try:
            catalog_entry = await plugin_manager.require_plugin(plugin_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        version = catalog_entry.version

    # ---- 3. Fetch the per-platform manifest ----
    manifest = await plugin_manager.get_manifest_at_version(
        plugin_id, version, platform=device_platform,
    )
    if manifest is None:
        raise HTTPException(
            404,
            f"Plugin '{plugin_id}@{version}' has no manifest "
            f"for platform '{device_platform}'",
        )

    # ---- 4. Compatibility — against the per-platform manifest ----
    declared_types = manifest.get("device_types") or []
    declared_archs = manifest.get("architectures") or []

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
    for c in manifest.get("capabilities") or []:
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
            "name": manifest.get("name", plugin_id),
            "version": version,
            "type": manifest.get("type", "native"),
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
        plugin = await pm.require_plugin(plugin_id)
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