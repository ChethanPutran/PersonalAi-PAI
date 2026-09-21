from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from pai.app_context import PAIAppContext, get_app_context


router = APIRouter(
    prefix="/plugins",
    tags=["plugins"],
)

logger = logging.getLogger(__name__)


# ============================================================
# Request Models
# ============================================================


class PluginInstallRequest(BaseModel):
    device_id: str


class PluginConfigRequest(BaseModel):
    config: Dict[str, Any]


# ============================================================
# Helpers
# ============================================================


def _get_user_id(
    user_id: Optional[str],
) -> str:
    """
    Resolve the user ID.

    Authentication is not fully wired yet, so anonymous is used
    as the temporary fallback.
    """

    return user_id or "anonymous"


# ============================================================
# Catalog
# ============================================================


@router.get("/catalog")
async def get_catalog(
    platform: str,
    architecture: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Return plugins compatible with a platform and architecture.

    Example:

        GET /api/v1/plugins/catalog
            ?platform=linux
            &architecture=x86_64
    """

    try:
        plugin_manager = context.orchestrator.plugin_manager

        plugins = await plugin_manager.get_compatible_plugins(
            platform=platform,
            architecture=architecture,
        )

        return {
            "plugins": [
                plugin_manager._serialize_plugin(plugin)
                for plugin in plugins
            ]
        }

    except Exception as exc:
        logger.exception(
            "Failed to load plugin catalog: %s",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to load plugin catalog",
        ) from exc


# ============================================================
# List Plugins
# ============================================================


@router.get("")
async def list_plugins(
    user_id: Optional[str] = Query(
        None,
        description="User ID for per-user plugin state",
    ),
    platform: Optional[str] = Query(None),
    architecture: Optional[str] = Query(None),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    List all plugins.

    Optionally returns plugins compatible with a specific
    platform/architecture and their user-specific enabled state.
    """

    try:
        user_id = _get_user_id(user_id)

        plugins = await context.orchestrator.plugin_manager.list_plugins(
            user_id=user_id,
            platform=platform,
            architecture=architecture,
        )

        total = len(plugins)

        enabled = sum(
            1
            for plugin in plugins
            if plugin.get("is_enabled", False)
        )

        return {
            "plugins": plugins,
            "total": total,
            "enabled": enabled,
        }

    except Exception as exc:
        logger.exception(
            "Failed to list plugins: %s",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to list plugins",
        ) from exc


# ============================================================
# Get Plugin
# ============================================================


@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Get metadata for a specific plugin.
    """

    try:
        plugin_manager = context.orchestrator.plugin_manager

        plugin = await plugin_manager.get_plugin(
            plugin_id
        )

        if plugin is None:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' not found",
            )

        return plugin_manager._serialize_plugin(
            plugin
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Failed to get plugin %s: %s",
            plugin_id,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to get plugin",
        ) from exc


# ============================================================
# Install Plugin On Device
# ============================================================


@router.post("/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    payload: PluginInstallRequest,
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Request installation of a plugin on a device.

    IMPORTANT:
    The backend does NOT install the plugin.

    It sends a plugin.install command through the active
    WebSocket connection to the target device.

    The device is responsible for:
        - downloading the package
        - validating checksum
        - installing/loading the plugin
        - reporting success/failure
    """

    plugin_manager = context.orchestrator.plugin_manager
    device_manager = context.orchestrator.device_manager

    logger.info(
        "Plugin install request: plugin=%s device=%s",
        plugin_id,
        payload.device_id,
    )

    logger.info(
        "Registered devices: %s",
        await device_manager.registry.all(),
    )

    device = await context.orchestrator.device_manager.get(
    payload.device_id,
    )

    logger.info(
        "Plugin installation device lookup: device_id={}, found={}",
        payload.device_id,
        device is not None,
    )

   

    # --------------------------------------------------------
    # 1. Verify plugin
    # --------------------------------------------------------

    try:
        plugin = await plugin_manager.require_plugin(
            plugin_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    # --------------------------------------------------------
    # 2. Verify device exists
    # --------------------------------------------------------

    device = await device_manager.get(
        payload.device_id
    )

    if device is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Device '{payload.device_id}' "
                "not found"
            ),
        )

    # --------------------------------------------------------
    # 3. Verify device connection
    # --------------------------------------------------------

    connected = await device_manager.is_connected(
        payload.device_id
    )

    if not connected:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Device '{payload.device_id}' "
                "is not connected"
            ),
        )

    # --------------------------------------------------------
    # 4. Verify platform compatibility
    # --------------------------------------------------------

    device_platform = getattr(
        device,
        "platform",
        None,
    )

    device_architecture = getattr(
        device,
        "architecture",
        None,
    )

    if (
        device_platform
        and device_architecture
        and not await plugin_manager.is_compatible(
            plugin_id=plugin_id,
            platform=device_platform,
            architecture=device_architecture,
        )
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Plugin '{plugin_id}' is not compatible "
                f"with device '{payload.device_id}' "
                f"({device_platform}/{device_architecture})"
            ),
        )

    # --------------------------------------------------------
    # 5. Build device command
    # --------------------------------------------------------

    command = await plugin_manager.build_install_request(
        plugin_id=plugin_id,
        device_id=payload.device_id,
    )

    # --------------------------------------------------------
    # 6. Send command through WebSocket
    # --------------------------------------------------------

    try:
        await device_manager.send(
            payload.device_id,
            command,
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Failed to send install command for %s: %s",
            plugin_id,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to send installation command",
        ) from exc

    # --------------------------------------------------------
    # 7. Return acknowledgement
    # --------------------------------------------------------

    return {
        "success": True,
        "plugin_id": plugin_id,
        "device_id": payload.device_id,
        "status": "install_requested",
        "message": (
            "Installation request sent to device"
        ),
    }


# ============================================================
# Enable Plugin
# ============================================================


@router.post("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    user_id: Optional[str] = Query(None),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Enable a plugin for a user.

    This changes backend authorization state.

    It does NOT install the plugin on a device.
    """

    user_id = _get_user_id(user_id)

    plugin_manager = context.orchestrator.plugin_manager

    try:
        await plugin_manager.enable(
            user_id=user_id,
            plugin_id=plugin_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Failed to enable plugin %s: %s",
            plugin_id,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to enable plugin",
        ) from exc

    return {
        "plugin_id": plugin_id,
        "user_id": user_id,
        "status": "enabled",
    }


# ============================================================
# Disable Plugin
# ============================================================


@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    user_id: Optional[str] = Query(None),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Disable a plugin for a user.

    This changes backend authorization state.

    It does NOT uninstall the plugin from a device.
    """

    user_id = _get_user_id(user_id)

    plugin_manager = context.orchestrator.plugin_manager

    try:
        await plugin_manager.disable(
            user_id=user_id,
            plugin_id=plugin_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Failed to disable plugin %s: %s",
            plugin_id,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to disable plugin",
        ) from exc

    return {
        "plugin_id": plugin_id,
        "user_id": user_id,
        "status": "disabled",
    }


# ============================================================
# Plugin Configuration
# ============================================================


@router.get("/{plugin_id}/config")
async def get_plugin_config(
    plugin_id: str,
    user_id: Optional[str] = Query(None),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Get user-specific plugin configuration.
    """

    user_id = _get_user_id(user_id)

    plugin_manager = context.orchestrator.plugin_manager

    try:
        config = await plugin_manager.get_user_plugin_config(
            user_id=user_id,
            plugin_id=plugin_id,
        )

        return {
            "plugin_id": plugin_id,
            "user_id": user_id,
            "config": config,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Failed to get config for plugin %s: %s",
            plugin_id,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to get plugin configuration",
        ) from exc


@router.post("/{plugin_id}/config")
async def set_plugin_config(
    plugin_id: str,
    payload: PluginConfigRequest,
    user_id: Optional[str] = Query(None),
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Store user-specific plugin configuration.
    """

    user_id = _get_user_id(user_id)

    plugin_manager = context.orchestrator.plugin_manager

    try:
        await plugin_manager.set_user_plugin_config(
            user_id=user_id,
            plugin_id=plugin_id,
            metadata=payload.config,
        )

        return {
            "plugin_id": plugin_id,
            "user_id": user_id,
            "status": "saved",
            "config": payload.config,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Failed to save config for plugin %s: %s",
            plugin_id,
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to save plugin configuration",
        ) from exc


# ============================================================
# Plugin Status
# ============================================================


@router.get("/system/status")
async def plugin_manager_status(
    context: PAIAppContext = Depends(get_app_context),
) -> Dict[str, Any]:
    """
    Return backend plugin manager status.
    """

    return context.orchestrator.plugin_manager.get_status()