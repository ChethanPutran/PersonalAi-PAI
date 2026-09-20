from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, Optional
import logging

from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])




"""
Plugin Management Routes

GET  /api/v1/plugins
GET  /api/v1/plugins/{plugin_id}
POST /api/v1/plugins/{plugin_id}/enable
POST /api/v1/plugins/{plugin_id}/disable
"""
"""API Routes - Plugin management (v1)

These routes integrate with the running kernel's PluginManager to persist
user plugin state and configuration. They are mounted at /api/v1/plugins.
"""

def _get_user_id_from_kernel(kernel: AIKernel) -> str:
    try:
        cm = getattr(kernel, "context_manager", None)
        if cm and getattr(cm, "user_id", None):
            return cm.user_id
    except Exception:
        pass
    return "anonymous"


"""
{
  "plugins": [
    {
      "id": "browser",
      "name": "Browser",
      "enabled": true,
      "capabilities": [
        "browser.navigate",
        "browser.click",
        "browser.type",
        "browser.screenshot"
      ]
    },
    {
      "id": "camera",
      "name": "Camera",
      "enabled": false,
      "capabilities": [
        "camera.capture"
      ]
    }
  ]
}
"""
@router.get("")
async def list_plugins(
    user_id: Optional[str] = Query(None, description="User ID for per-user enabled state"),
    kernel: AIKernel = Depends(get_kernel)
) -> Dict[str, Any]:
    try:
        plugins = await kernel.plugin_manager.list_plugins(user_id=user_id)
        total = len(plugins)
        enabled = sum(1 for p in plugins if p.get("is_enabled", False))
        return {"plugins": plugins, "total": total, "enabled": enabled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    user_id: Optional[str] = Query(None),
    kernel: AIKernel = Depends(get_kernel)
) -> Dict[str, Any]:
    # plugin_id might be the manifest id or the internal name
    # Try to find by id first, fallback to name
    plugin_name = None
    for name, manifest in kernel.plugin_manager._plugin_manifest.items():
        if manifest.get("id") == plugin_id:
            plugin_name = name
            break
    if not plugin_name:
        plugin_name = plugin_id  # assume it's the internal name

    info = await kernel.plugin_manager.get_plugin_info(plugin_name)
    if info is None:
        raise HTTPException(404, "Plugin not found")

    # Load user-specific config
    if user_id:
        records = await kernel.plugin_manager.get_user_plugins(user_id)
        user_record = next((r for r in records if r.get("plugin_name") == plugin_name), None)
        info["config"] = user_record.get("metadata", {}) if user_record else {}
        info["is_enabled"] = user_record.get("is_enabled", info["running"]) if user_record else info["running"]
    else:
        info["config"] = {}
        info["is_enabled"] = info["running"]

    return info

@router.post("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    user_id: Optional[str] = Query(None),
    kernel: AIKernel = Depends(get_kernel)
) -> Dict[str, Any]:
    if not user_id:
        user_id = "anonymous"
    # Resolve plugin name
    plugin_name = None
    for name, manifest in kernel.plugin_manager._plugin_manifest.items():
        if manifest.get("id") == plugin_id:
            plugin_name = name
            break
    if not plugin_name:
        plugin_name = plugin_id
    await kernel.plugin_manager.set_user_plugin_enabled(user_id, plugin_name, True)
    return {"plugin_id": plugin_id, "status": "enabled"}

@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    user_id: Optional[str] = Query(None),
    kernel: AIKernel = Depends(get_kernel)
) -> Dict[str, Any]:
    if not user_id:
        user_id = "anonymous"
    plugin_name = None
    for name, manifest in kernel.plugin_manager._plugin_manifest.items():
        if manifest.get("id") == plugin_id:
            plugin_name = name
            break
    if not plugin_name:
        plugin_name = plugin_id
    await kernel.plugin_manager.set_user_plugin_enabled(user_id, plugin_name, False)
    return {"plugin_id": plugin_id, "status": "disabled"}


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str, kernel: AIKernel = Depends(get_kernel)) -> Dict[str, Any]:
    """Return stored configuration for a plugin for the current user."""
    try:
        user_id = _get_user_id_from_kernel(kernel)
        records = await kernel.plugin_manager.get_user_plugins(user_id)
        user_record = next((r for r in records if r.get("plugin_name") == plugin_id), None)
        return {"config": user_record.get("metadata", {}) if user_record else {}}
    except Exception as e:
        logger.error(f"Error getting plugin config {plugin_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{plugin_id}/config")
async def set_plugin_config(plugin_id: str, payload: dict, kernel: AIKernel = Depends(get_kernel)) -> Dict[str, Any]:
    """Persist configuration for a plugin for the current user."""
    try:
        user_id = _get_user_id_from_kernel(kernel)
        # Persist config into user plugin metadata
        await kernel.plugin_manager.record_user_plugin(user_id=user_id, plugin_name=plugin_id, metadata=payload)
        return {"plugin_id": plugin_id, "status": "saved", "config": payload}
    except Exception as e:
        logger.error(f"Error setting plugin config {plugin_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

