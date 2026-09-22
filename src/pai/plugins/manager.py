from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.plugins.catalog.catalog import PLUGIN_CATALOG
from pai.plugins.models import PluginInfo
from pai.plugins.registry import PluginRegistry
from pai.storage.repositories.plugin import UserPluginRepository
from pai.storage.repositories.device_plugin import DevicePluginRepository

T = TypeVar("T")

class PluginManager:
    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
    ):
        self.registry = registry
        self._session_factory = session_factory

        self._initialized = False
        self._catalog: Dict[str, PluginInfo] = {}

    # ------------------------------------------------------------------
    # Internal: open a scoped session, run [fn], commit, close.
    # ------------------------------------------------------------------

    

    async def _with_session(
        self,
        fn: Callable[[AsyncSession], Awaitable[T]],
    ) -> T:
        if self._session_factory is None:
            raise RuntimeError("PluginManager has no session_factory")

        session = self._session_factory()
        try:
            result = await fn(session)
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    # ------------------------------------------------------------------
    # Lifecycle (unchanged)
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        if self._initialized:
            return
        logger.info("Initializing backend plugin manager")

        if self.registry is not None:
            try:
                await self.registry.discover()
                for manifest in self.registry.get_all_manifests():
                    if getattr(manifest, "id", None):
                        self._catalog[manifest.id] = manifest
            except Exception as exc:
                logger.warning("Plugin registry discovery failed: {}", exc)

        for plugin in PLUGIN_CATALOG:
            self._catalog[plugin.id] = plugin

        self._initialized = True
        logger.info(
            "Backend plugin manager initialized with {} plugins",
            len(self._catalog),
        )

    async def shutdown(self) -> None:
        self._catalog.clear()
        self._initialized = False

    async def set_device_enabled(
        self,
        device_id: str,
        plugin_id: str,
        enabled: bool,
    ) -> None:
        """
        Flip the `enabled_on_device` flag on a device_plugins row.

        Called by /plugins/{id}/enable and /disable after the user
        toggles the switch in the app. The user_plugins table
        records the *authorization*; this table records the
        *device runtime state* (whether the .so is currently loaded).
        """
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            await repo.set_device_enabled(device_id, plugin_id, enabled)

        await self._with_session(_do)
        logger.info(
            "Set device enabled: device={} plugin={} enabled={}",
            device_id, plugin_id, enabled,
        )

    # ------------------------------------------------------------------
    # Catalog (no DB) — unchanged
    # ------------------------------------------------------------------

    def get_catalog(self) -> List[PluginInfo]:
        return list(self._catalog.values())

    async def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        return self._catalog.get(plugin_id)

    async def require_plugin(self, plugin_id: str) -> PluginInfo:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        return plugin

    async def get_compatible_plugins(
        self, platform: str, architecture: str,
    ) -> List[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        return [
            p for p in self._catalog.values()
            if platform in p.platforms and architecture in p.architectures
        ]

    async def is_compatible(
        self, plugin_id: str, platform: str, architecture: str,
    ) -> bool:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            return False
        return platform in plugin.platforms and architecture in plugin.architectures

    async def get_plugin_for_capability(
        self, capability: str,
    ) -> Optional[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        for p in self._catalog.values():
            if capability in p.capabilities:
                return p
        return None

    # ------------------------------------------------------------------
    # User plugin state — each method opens a session
    # ------------------------------------------------------------------

    async def list_plugins(
        self,
        user_id: Optional[str] = None,
        platform: Optional[str] = None,
        architecture: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        plugins = self.get_catalog()

        if platform:
            plugins = [p for p in plugins if platform in p.platforms]
        if architecture:
            plugins = [p for p in plugins if architecture in p.architectures]

        if not user_id or self._session_factory is None:
            return [self._serialize_plugin(p) for p in plugins]

        async def _load(session: AsyncSession):
            repo = UserPluginRepository(session)
            records = await repo.list_for_user(user_id)
            return {r.plugin_id: r.is_enabled for r in records}

        enabled_map = await self._with_session(_load)

        return [
            self._serialize_plugin(
                p,
                is_enabled=enabled_map.get(p.id, False),
            )
            for p in plugins
        ]

    async def enable(self, user_id: str, plugin_id: str) -> None:
        await self.require_plugin(plugin_id)

        async def _do(session: AsyncSession):
            repo = UserPluginRepository(session)
            await repo.set_enabled(user_id, plugin_id, True)

        await self._with_session(_do)
        logger.info("Plugin '{}' enabled for user '{}'", plugin_id, user_id)

    async def disable(self, user_id: str, plugin_id: str) -> None:
        await self.require_plugin(plugin_id)

        async def _do(session: AsyncSession):
            repo = UserPluginRepository(session)
            await repo.set_enabled(user_id, plugin_id, False)

        await self._with_session(_do)
        logger.info("Plugin '{}' disabled for user '{}'", plugin_id, user_id)

    async def get_user_plugin_config(
        self, user_id: str, plugin_id: str,
    ) -> Dict[str, Any]:
        await self.require_plugin(plugin_id)
        if self._session_factory is None:
            return {}

        async def _do(session: AsyncSession):
            repo = UserPluginRepository(session)
            return await repo.get_config(user_id, plugin_id)

        return await self._with_session(_do)

    async def set_user_plugin_config(
        self, user_id: str, plugin_id: str, metadata: Dict[str, Any],
    ) -> None:
        await self.require_plugin(plugin_id)

        async def _do(session: AsyncSession):
            repo = UserPluginRepository(session)
            await repo.set_config(user_id, plugin_id, metadata)

        await self._with_session(_do)

    # ------------------------------------------------------------------
    # Device plugin state — same pattern
    # ------------------------------------------------------------------

    async def mark_installed_on_device(
        self,
        device_id: str,
        plugin_id: str,
        version: str,
        artifact_sha256: str | None = None,
    ) -> None:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            await repo.mark_installed(
                device_id, plugin_id, version, artifact_sha256,
            )

        await self._with_session(_do)
        logger.info(
            "Marked {}@{} installed on device {}",
            plugin_id, version, device_id,
        )

    async def mark_uninstalled_on_device(
        self, device_id: str, plugin_id: str,
    ) -> None:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            await repo.mark_uninstalled(device_id, plugin_id)

        await self._with_session(_do)

    async def record_device_error(
        self, device_id: str, plugin_id: str, error: str,
    ) -> None:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            await repo.record_error(device_id, plugin_id, error)

        await self._with_session(_do)

    async def reconcile_device_plugins(
        self, device_id: str, device_plugins: list[dict],
    ) -> dict:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            return await repo.reconcile(device_id, device_plugins)

        return await self._with_session(_do)

    async def list_device_plugins(self, device_id: str) -> list[dict]:
        if self._session_factory is None:
            return []

        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            return await repo.list_for_device(device_id)

        records = await self._with_session(_do)
        return [
            {
                "plugin_id": r.plugin_id,
                "version": r.version,
                "is_installed": r.is_installed,
                "enabled_on_device": r.enabled_on_device,
                "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                "last_error": r.last_error,
            }
            for r in records
        ]

    # ------------------------------------------------------------------
    # Install request builder (no DB)
    # ------------------------------------------------------------------

    async def build_install_request(
        self, plugin_id: str, device_id: str,
    ) -> Dict[str, Any]:
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
            },
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_plugin(
        plugin: PluginInfo, is_enabled: bool = False,
    ) -> Dict[str, Any]:
        return {
            "id": plugin.id,
            "name": plugin.name,
            "version": plugin.version,
            "platforms": list(plugin.platforms),
            "architectures": list(plugin.architectures),
            "capabilities": list(plugin.capabilities),
            "package_url": getattr(plugin, "package_url", None),
            "checksum": getattr(plugin, "checksum", None),
            "size": getattr(plugin, "size", 0),
            "is_enabled": is_enabled,
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "plugin_count": len(self._catalog),
            "plugins": list(self._catalog.keys()),
        }