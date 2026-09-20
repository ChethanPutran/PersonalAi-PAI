from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from pai.plugins.base import BasePlugin, PluginState
from pai.plugins.models import PluginManifest
from pai.plugins.registry import PluginRegistry


class PluginManager:
    """
    Manages the runtime lifecycle of plugins.

    Responsibilities:
        - Initialize registered plugins
        - Start plugins
        - Stop plugins
        - Execute plugin actions
        - Route plugin events
        - Expose runtime plugin information

    NOT responsible for:
        - User enable/disable state
        - Authorization
        - Device selection
        - Task orchestration
        - Database persistence
    """

    def __init__(
        self,
        registry: PluginRegistry,
    ) -> None:
        self.registry = registry

        self._instances: Dict[str, BasePlugin] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Discover plugins and initialize their runtime instances."""

        await self.registry.discover()

        for manifest in self.registry.get_all_manifests():
            await self._initialize_plugin(manifest)

        self._initialized = True

        logger.info(
            f"Plugin manager initialized with "
            f"{len(self._instances)} plugins"
        )

    async def _initialize_plugin(
        self,
        manifest: PluginManifest,
    ) -> None:
        plugin_class = self.registry.get_plugin_class(manifest.id)

        if plugin_class is None:
            raise RuntimeError(
                f"No implementation registered for "
                f"plugin '{manifest.id}'"
            )

        if manifest.id in self._instances:
            logger.warning(
                f"Plugin '{manifest.id}' is already initialized"
            )
            return

        plugin = plugin_class(
            plugin_id=manifest.id,
            config=self._default_config(manifest),
        )

        try:
            await plugin.initialize()
            await plugin.start()

            self._instances[manifest.id] = plugin

            logger.info(
                f"Started plugin '{manifest.id}'"
            )

        except Exception:
            plugin.state = PluginState.FAILED

            logger.exception(
                f"Failed to initialize plugin "
                f"'{manifest.id}'"
            )

            raise

    @staticmethod
    def _default_config(
        manifest: PluginManifest,
    ) -> Dict[str, Any]:
        """Extract default values from the manifest config schema."""

        config: Dict[str, Any] = {}

        for key, schema in manifest.config_schema.items():
            if "default" in schema:
                config[key] = schema["default"]

        return config

    async def execute(
        self,
        plugin_id: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Execute an action on a running plugin.

        Authorization and device routing must happen before this method.
        """

        plugin = self._instances.get(plugin_id)

        if plugin is None:
            raise ValueError(
                f"Plugin '{plugin_id}' is not loaded"
            )

        if not plugin.is_running:
            raise RuntimeError(
                f"Plugin '{plugin_id}' is not running"
            )

        manifest = self.registry.get_manifest(plugin_id)

        if manifest is None:
            raise ValueError(
                f"Manifest for plugin '{plugin_id}' not found"
            )

        manifest.validate_capability(action)

        if not plugin.supports(action):
            raise ValueError(
                f"Plugin '{plugin_id}' declares capability "
                f"'{action}' but does not implement it"
            )

        return await plugin.execute(
            action,
            params or {},
        )

    async def shutdown_plugin(
        self,
        plugin_id: str,
    ) -> None:
        """Shutdown one plugin runtime."""

        plugin = self._instances.pop(plugin_id, None)

        if plugin is None:
            return

        try:
            await plugin.shutdown()
        except Exception:
            logger.exception(
                f"Error shutting down plugin '{plugin_id}'"
            )

    async def shutdown(self) -> None:
        """Shutdown all plugin runtimes."""

        for plugin_id in list(self._instances):
            await self.shutdown_plugin(plugin_id)

        self._initialized = False

        logger.info("Plugin manager shutdown")

    async def get_plugin(
        self,
        plugin_id: str,
    ) -> Optional[BasePlugin]:
        return self._instances.get(plugin_id)

    async def get_plugin_info(
        self,
        plugin_id: str,
    ) -> Optional[Dict[str, Any]]:
        manifest = self.registry.get_manifest(plugin_id)
        plugin = self._instances.get(plugin_id)

        if manifest is None:
            return None

        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "type": manifest.plugin_type,
            "description": manifest.description,
            "permissions": manifest.permissions,
            "dependencies": manifest.dependencies,
            "capabilities": manifest.capability_names,
            "config_schema": manifest.config_schema,
            "state": (
                plugin.state.value
                if plugin is not None
                else "not_loaded"
            ),
        }

    async def list_plugins(self) -> List[Dict[str, Any]]:
        """Return metadata for all registered plugins."""

        result = []

        for manifest in self.registry.get_all_manifests():
            info = await self.get_plugin_info(manifest.id)

            if info is not None:
                result.append(info)

        return result

    async def handle_event(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Broadcast an event to loaded plugins."""

        for plugin in self._instances.values():
            try:
                await plugin.handle_event(
                    event_type,
                    data,
                )
            except Exception:
                logger.exception(
                    f"Plugin '{plugin.plugin_id}' "
                    f"failed to handle event '{event_type}'"
                )