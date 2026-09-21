from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from pai.plugins.catalog.catalog import PLUGIN_CATALOG
from pai.plugins.models import PluginInfo
from pai.plugins.registry import PluginRegistry


class PluginManager:
    """
    Backend-side plugin manager.

    Responsibilities:
    - Discover and expose the server's plugin catalog
    - Read plugin metadata
    - Track per-user enabled/disabled state
    - Validate plugin/capability existence
    - Provide plugin metadata to the orchestrator
    - NOT execute device plugins

    Actual plugin installation and execution happen on the target
    device through the device connection / WebSocket layer.
    """

    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        user_plugin_repository=None,
    ):
        self.registry = registry
        self.user_plugin_repository = user_plugin_repository

        self._initialized = False
        self._catalog: Dict[str, PluginInfo] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Initialize the backend plugin catalog.

        The backend does not instantiate or start device plugins.
        """

        if self._initialized:
            return

        logger.info("Initializing backend plugin manager")

        if self.registry is not None:
            try:
                await self.registry.discover()

                for manifest in self.registry.get_all_manifests():
                    plugin_id = getattr(manifest, "id", None)

                    if plugin_id:
                        self._catalog[plugin_id] = manifest

            except Exception as exc:
                logger.warning(
                    "Plugin registry discovery failed: {}",
                    exc,
                )

        # Also load static catalog entries.
        for plugin in PLUGIN_CATALOG:
            self._catalog[plugin.id] = plugin

        self._initialized = True

        logger.info(
            "Backend plugin manager initialized with {} plugins",
            len(self._catalog),
        )

    async def shutdown(self) -> None:
        """
        Shutdown the backend plugin manager.

        There are no device plugin instances to stop here.
        """

        self._catalog.clear()
        self._initialized = False

        logger.info("Backend plugin manager shut down")

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def get_catalog(self) -> List[PluginInfo]:
        """Return all known plugin definitions."""

        return list(self._catalog.values())

    async def list_plugins(
        self,
        user_id: Optional[str] = None,
        platform: Optional[str] = None,
        architecture: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return plugin metadata together with per-user state.

        Example:

        [
            {
                "id": "camera",
                "name": "Camera",
                "version": "1.0.0",
                "is_enabled": True,
                ...
            }
        ]
        """

        plugins = self.get_catalog()

        if platform:
            plugins = [
                plugin
                for plugin in plugins
                if platform in plugin.platforms
            ]

        if architecture:
            plugins = [
                plugin
                for plugin in plugins
                if architecture in plugin.architectures
            ]

        result = []

        for plugin in plugins:
            is_enabled = False

            if user_id and self.user_plugin_repository:
                try:
                    is_enabled = await self.user_plugin_repository.is_enabled(
                        user_id=user_id,
                        plugin_id=plugin.id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to read state for plugin {}: {}",
                        plugin.id,
                        exc,
                    )

            result.append(
                self._serialize_plugin(
                    plugin,
                    is_enabled=is_enabled,
                )
            )

        return result

    async def get_plugin(
        self,
        plugin_id: str,
    ) -> Optional[PluginInfo]:
        """Return plugin metadata by ID."""

        if not self._initialized:
            await self.initialize()

        return self._catalog.get(plugin_id)

    async def require_plugin(
        self,
        plugin_id: str,
    ) -> PluginInfo:
        """Return a plugin or raise ValueError."""

        plugin = await self.get_plugin(plugin_id)

        if plugin is None:
            raise ValueError(
                f"Plugin '{plugin_id}' not found"
            )

        return plugin

    # ------------------------------------------------------------------
    # Platform filtering
    # ------------------------------------------------------------------

    async def get_compatible_plugins(
        self,
        platform: str,
        architecture: str,
    ) -> List[PluginInfo]:
        """
        Return plugins compatible with a device platform
        and architecture.
        """

        if not self._initialized:
            await self.initialize()

        return [
            plugin
            for plugin in self._catalog.values()
            if platform in plugin.platforms
            and architecture in plugin.architectures
        ]

    async def is_compatible(
        self,
        plugin_id: str,
        platform: str,
        architecture: str,
    ) -> bool:
        """Check whether a plugin supports a target device."""

        plugin = await self.get_plugin(plugin_id)

        if plugin is None:
            return False

        return (
            platform in plugin.platforms
            and architecture in plugin.architectures
        )

    # ------------------------------------------------------------------
    # Capability handling
    # ------------------------------------------------------------------

    async def supports_capability(
        self,
        plugin_id: str,
        capability: str,
    ) -> bool:
        """
        Check whether a plugin provides a capability.

        Example:
            browser -> browser.open
        """

        plugin = await self.get_plugin(plugin_id)

        if plugin is None:
            return False

        return capability in plugin.capabilities

    async def get_plugin_for_capability(
        self,
        capability: str,
    ) -> Optional[PluginInfo]:
        """
        Find the first plugin providing a capability.

        The orchestrator can use this during capability resolution.
        """

        if not self._initialized:
            await self.initialize()

        for plugin in self._catalog.values():
            if capability in plugin.capabilities:
                return plugin

        return None

    async def validate_capability(
        self,
        plugin_id: str,
        capability: str,
    ) -> None:
        """Validate that a plugin provides a capability."""

        plugin = await self.require_plugin(plugin_id)

        if capability not in plugin.capabilities:
            raise ValueError(
                f"Plugin '{plugin_id}' does not support "
                f"capability '{capability}'"
            )

    # ------------------------------------------------------------------
    # User plugin state
    # ------------------------------------------------------------------

    async def is_enabled(
        self,
        user_id: str,
        plugin_id: str,
    ) -> bool:
        """
        Return whether the user has enabled a plugin.
        """

        if self.user_plugin_repository is None:
            return False

        await self.require_plugin(plugin_id)

        return await self.user_plugin_repository.is_enabled(
            user_id=user_id,
            plugin_id=plugin_id,
        )

    async def enable(
        self,
        user_id: str,
        plugin_id: str,
    ) -> Any:
        """
        Enable a plugin for a user.

        This only changes backend authorization state.
        It does not install the plugin on a device.
        """

        await self.require_plugin(plugin_id)

        if self.user_plugin_repository is None:
            raise RuntimeError(
                "UserPluginRepository is not configured"
            )

        record = await self.user_plugin_repository.set_enabled(
            user_id=user_id,
            plugin_id=plugin_id,
            enabled=True,
        )

        logger.info(
            "Plugin '{}' enabled for user '{}'",
            plugin_id,
            user_id,
        )

        return record

    async def disable(
        self,
        user_id: str,
        plugin_id: str,
    ) -> Any:
        """
        Disable a plugin for a user.

        This prevents authorization for future executions.
        It does not uninstall the device plugin.
        """

        await self.require_plugin(plugin_id)

        if self.user_plugin_repository is None:
            raise RuntimeError(
                "UserPluginRepository is not configured"
            )

        record = await self.user_plugin_repository.set_enabled(
            user_id=user_id,
            plugin_id=plugin_id,
            enabled=False,
        )

        logger.info(
            "Plugin '{}' disabled for user '{}'",
            plugin_id,
            user_id,
        )

        return record

    async def get_user_plugins(
        self,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Return all plugin states for a user.
        """

        if self.user_plugin_repository is None:
            return []

        records = await self.user_plugin_repository.list_for_user(
            user_id=user_id,
        )

        result = []

        for record in records:
            plugin = await self.get_plugin(record.plugin_id)

            result.append(
                {
                    "plugin_id": record.plugin_id,
                    "plugin_name": (
                        plugin.name
                        if plugin is not None
                        else record.plugin_id
                    ),
                    "is_enabled": record.is_enabled,
                    "metadata": getattr(
                        record,
                        "metadata",
                        {},
                    ) or {},
                }
            )

        return result

    async def get_enabled_plugins(
        self,
        user_id: str,
    ) -> List[PluginInfo]:
        """
        Return the actual plugin definitions enabled by a user.
        """

        if self.user_plugin_repository is None:
            return []

        records = await self.user_plugin_repository.list_enabled(
            user_id=user_id,
        )

        plugins = []

        for record in records:
            plugin = await self.get_plugin(record.plugin_id)

            if plugin is not None:
                plugins.append(plugin)

        return plugins

    async def set_user_plugin_config(
        self,
        user_id: str,
        plugin_id: str,
        metadata: Dict[str, Any],
    ) -> Any:
        """
        Store user-specific plugin configuration.

        Requires the repository to support metadata persistence.
        """

        await self.require_plugin(plugin_id)

        if self.user_plugin_repository is None:
            raise RuntimeError(
                "UserPluginRepository is not configured"
            )

        record = await self.user_plugin_repository.get(
            user_id=user_id,
            plugin_id=plugin_id,
        )

        if record is None:
            record = await self.user_plugin_repository.set_enabled(
                user_id=user_id,
                plugin_id=plugin_id,
                enabled=False,
            )

        if hasattr(record, "metadata"):
            record.metadata = metadata
            await self.user_plugin_repository.session.flush()

        return record

    async def get_user_plugin_config(
        self,
        user_id: str,
        plugin_id: str,
    ) -> Dict[str, Any]:
        """Return user-specific plugin configuration."""

        await self.require_plugin(plugin_id)

        if self.user_plugin_repository is None:
            return {}

        record = await self.user_plugin_repository.get(
            user_id=user_id,
            plugin_id=plugin_id,
        )

        if record is None:
            return {}

        return getattr(record, "metadata", {}) or {}

    # ------------------------------------------------------------------
    # Device operation metadata
    # ------------------------------------------------------------------

    async def build_install_request(
        self,
        plugin_id: str,
        device_id: str,
    ) -> Dict[str, Any]:
        """
        Build the command that the backend sends to a device.

        This method DOES NOT install anything.
        """

        plugin = await self.require_plugin(plugin_id)

        return {
            "type": "plugin.install",
            "request_id": f"install:{plugin_id}:{device_id}",
            "device_id": device_id,
            "plugin": {
                "id": plugin.id,
                "name": plugin.name,
                "version": plugin.version,
                "platforms": plugin.platforms,
                "architectures": plugin.architectures,
                "capabilities": plugin.capabilities,
                "package_url": getattr(
                    plugin,
                    "package_url",
                    None,
                ),
                "checksum": getattr(
                    plugin,
                    "checksum",
                    None,
                ),
                "size": getattr(
                    plugin,
                    "size",
                    0,
                ),
            },
        }

    async def build_uninstall_request(
        self,
        plugin_id: str,
        device_id: str,
    ) -> Dict[str, Any]:
        """Build a device uninstall command."""

        await self.require_plugin(plugin_id)

        return {
            "type": "plugin.uninstall",
            "request_id": f"uninstall:{plugin_id}:{device_id}",
            "device_id": device_id,
            "plugin_id": plugin_id,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_plugin(
        plugin: PluginInfo,
        is_enabled: bool = False,
    ) -> Dict[str, Any]:
        """
        Convert PluginInfo into a JSON-friendly dictionary.
        """

        return {
            "id": plugin.id,
            "name": plugin.name,
            "version": plugin.version,
            "platforms": list(plugin.platforms),
            "architectures": list(plugin.architectures),
            "capabilities": list(plugin.capabilities),
            "package_url": getattr(
                plugin,
                "package_url",
                None,
            ),
            "checksum": getattr(
                plugin,
                "checksum",
                None,
            ),
            "size": getattr(
                plugin,
                "size",
                0,
            ),
            "is_enabled": is_enabled,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return backend plugin manager status."""

        return {
            "initialized": self._initialized,
            "plugin_count": len(self._catalog),
            "plugins": list(self._catalog.keys()),
        }