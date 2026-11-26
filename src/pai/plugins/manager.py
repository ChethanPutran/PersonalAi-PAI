from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.plugins.operations import PluginOperations
from pai.event_bus.bus import EventBus
from pai.event_bus.events import EventType
from pai.observability.tracer import get_tracer
from pai.plugins.manifest_info import PluginInfo
from pai.plugins.manifest import PluginManifest
from pai.plugins.packager import PluginPackager
from pai.plugins.registry import PluginRegistry
from pai.plugins.store import PluginArtifactStore
from pai.storage.models import UserPluginModel
from pai.storage.repositories.plugin import UserPluginRepository
from pai.storage.repositories.device_plugin import PERMISSION_STATES, DevicePluginRepository
from pai.common.manager import Manager

T = TypeVar("T")



class PluginManager(Manager):
    def __init__(
        self,
        event_bus: EventBus,
        plugin_registry_dir: Path,
        artifact_store: PluginArtifactStore,
        packager: PluginPackager,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
        **kwargs,
    ):
        super().__init__(session_factory=session_factory, event_bus=event_bus)
        self.registry = PluginRegistry(plugin_registry_dir,artifact_store,packager)
        self._catalog: Dict[str, PluginInfo] = {}
        self._capability_router: Any = None
        # Tracked remote install/uninstall operations; attached once the device manager exists.
        self.operations: Optional[PluginOperations] = None
        # Provided by the backend itself (see pai.agent.plugin): catalog entries only, nothing to install.
        self._builtin_plugins: Dict[str, PluginInfo] = {}

        if isinstance(plugin_registry_dir, str):
            plugin_registry_dir = Path(plugin_registry_dir)

        self.plugin_registry_dir = plugin_registry_dir

    async def _on_initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span("plugin_manager.initialize") as span:
            logger.info("Initializing backend plugin manager")

            # Initialize the plugin registry
            await self.registry.initialize()
            if self.operations is not None:
                await self.operations.start()
            self._emit(EventType.PLUGIN_MANAGER_INITIALIZED,{})

    def attach_operations(self, device_manager: Any) -> PluginOperations:
        """Create the operation tracker (needs the device manager to push updates)."""
        self.operations = PluginOperations(
            sessions=self._sessions, device_manager=device_manager, event_bus=self._event_bus)
        return self.operations

    async def get_plugin_index_for_device(
        self, device_platform: str, device_architecture: str, device_type: Optional[str] = None,
        app_version: Optional[str] = None,
    ) -> List[PluginInfo]:
        """
        Return a list of plugins compatible with the given device platform and architecture.
        """
        if not self._initialized:
            await self.initialize()

        compatible_plugins = await self.registry.get_compatible_plugins(
            device_platform, device_architecture, app_version)
        plugin_index = []

        for plugin in compatible_plugins:
            if device_type and plugin.deviceTypes and device_type not in plugin.deviceTypes:
                continue  # Skip plugins that don't support the device type

            plugin_index.append(plugin)

        return plugin_index
    
    # ------------------------------------------------------------------
    # Router wiring
    # ------------------------------------------------------------------

    def set_capability_router(self, router: Any) -> None:
        self._capability_router = router

    def _invalidate(self, user_id: Optional[str] = None) -> None:
        if self._capability_router is not None:
            self._capability_router.invalidate(user_id)

    async def shutdown(self) -> None:
        if self.operations is not None:
            await self.operations.stop()
        self._initialized = False

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def register_builtin_plugin(self, info: PluginInfo) -> None:
        """A plugin the backend provides itself: shown in the catalog and enabled per user like any
        other, but never installed on a device."""
        self._builtin_plugins[info.id] = info

    def get_catalog(self) -> List[PluginInfo]:
        return [*self.registry.get_catalog(), *self._builtin_plugins.values()]

    def get_manifest(self, plugin_id: str, version: Optional[str] = None) -> Optional[PluginManifest]:
        """Manifest of ``version`` (default: newest the runtime can host).

        Anything deciding what a *device* may do must pass the version that device has
        installed, not rely on the default.
        """
        return self.registry.get_manifest(plugin_id, version)

    def get_plugin_version(self, plugin_id: str, version: Optional[str]) -> Optional[PluginInfo]:
        return self.registry.get_info(plugin_id, version)

    def list_plugin_versions(self, plugin_id: str) -> List[PluginInfo]:
        return self.registry.list_versions(plugin_id)
    
    async def get_plugin_by_name(self, plugin_name: str, platform: str, architecture: str, version: Optional[str] = None) -> Optional[PluginInfo]:
        if not version:
            return await self.registry.get_latest_plugin(plugin_name, platform, architecture)
        return await self.registry.get_plugin_at_version(plugin_name, version, platform, architecture)

    def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        return self._builtin_plugins.get(plugin_id) or self.registry.get_plugin(plugin_id)
    
    def get_plugin_for_capability(
        self, capability: str,
    ) -> Optional[PluginInfo]:
        self._check_initialized()
        for p in self._catalog.values():
            if capability in p.capabilities:
                return p
        return None
    
    def _check_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("PluginManager is not initialized")
        
    def require_plugin(self, plugin_id: str) -> PluginInfo:
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise ValueError(f"Plugin '{plugin_id}' not found in catalog")
        return plugin
    # ------------------------------------------------------------------
    # User plugin state
    # ------------------------------------------------------------------

    async def is_enabled_for_user(
        self, user_id: str, plugin_id: str,
    ) -> bool:
        if not user_id or self._session_factory is None:
            return False

        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.is_enabled_for_user",
            user_id=user_id,
            plugin_id=plugin_id,
        ) as span:
            async def _do(session: AsyncSession):
                repo = UserPluginRepository(session)
                records = await repo.list_for_user(user_id)
                for r in records:
                    if r.plugin_id == plugin_id:
                        return r.is_enabled
                return False

            result = await self._with_session(_do)
            span.set_attribute("enabled", result)
            return bool(result)
        
    async def list_user_plugins(
        self,
        user_id: str,
        enabled: Optional[bool] = None,
    ) -> List[UserPluginModel]:
        if not self._initialized:
            await self.initialize()

        async def _load(session: AsyncSession):
            repo = UserPluginRepository(session)
            # repo = DevicePluginRepository(session)
            records = await repo.list_for_user(user_id)

            return records

        user_plugins = await self._with_session(_load) or {}
        enabled_map = {r.plugin_id: r.is_enabled for r in user_plugins}

        if enabled_map is None:
            enabled_map = {}

        if enabled is not None:
            plugin_index = [
                p for p in user_plugins if enabled_map.get(p.id, False) == enabled
            ]
        else:
            plugin_index = [
                p for p in user_plugins if p.id in enabled_map
            ]

        return plugin_index
    
    
    async def list_plugins(
        self,
        user_id: Optional[str] = None,
        platform: Optional[str] = None,
        architecture: Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> List[PluginInfo]:
        plugins = self.get_catalog()

        if platform:
            plugins = [p for p in plugins if platform in p.platforms]
        if architecture:
            plugins = [p for p in plugins if architecture in p.architectures]
        if device_type:
            plugins = [p for p in plugins if device_type in p.deviceTypes]

        if not user_id or self._session_factory is None:
            return plugins

        async def _load(session: AsyncSession):
            repo = UserPluginRepository(session)
            records = await repo.list_for_user(user_id)
            return {r.plugin_id: r.is_enabled for r in records}

        enabled_map = await self._with_session(_load)

        return plugins
        

    async def enable(self, user_id: str, plugin_id: str) -> None:
        self.require_plugin(plugin_id)

        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.enable",
            user_id=user_id, plugin_id=plugin_id,
        ):
            async def _do(session: AsyncSession):
                repo = UserPluginRepository(session)
                await repo.set_enabled(user_id, plugin_id, True)

            await self._with_session(_do)
            self._invalidate(user_id)
            logger.info("Plugin '{}' enabled for user '{}'", plugin_id, user_id)

            await self._publish(
                "plugin.enabled",
                {"plugin_id": plugin_id, "user_id": user_id},
                distributed=True,
            )

    async def disable(self, user_id: str, plugin_id: str) -> None:
        self.require_plugin(plugin_id)

        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.disable",
            user_id=user_id, plugin_id=plugin_id,
        ):
            async def _do(session: AsyncSession):
                repo = UserPluginRepository(session)
                await repo.set_enabled(user_id, plugin_id, False)

            await self._with_session(_do)
            self._invalidate(user_id)
            logger.info("Plugin '{}' disabled for user '{}'", plugin_id, user_id)

            await self._publish(
                "plugin.disabled",
                {"plugin_id": plugin_id, "user_id": user_id},
                distributed=True,
            )

    async def get_user_plugin_config(
        self, user_id: str, plugin_id: str,
    ) -> Dict[str, Any]:
        self.require_plugin(plugin_id)
        if self._session_factory is None:
            return {} 

        async def _do(session: AsyncSession):
            repo = UserPluginRepository(session)
            return await repo.get_config(user_id, plugin_id)

        return (await self._with_session(_do)) or {}

    async def set_user_plugin_config(
        self, user_id: str, plugin_id: str, metadata: Dict[str, Any],
    ) -> None:
        self.require_plugin(plugin_id)

        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.set_user_config",
            user_id=user_id, plugin_id=plugin_id,
            metadata_keys=list(metadata.keys()),
        ):
            async def _do(session: AsyncSession):
                repo = UserPluginRepository(session)
                await repo.set_config(user_id, plugin_id, metadata)

            await self._with_session(_do)

            self._emit(
                EventType.PLUGIN_CONFIG_UPDATED,
                {
                    "plugin_id": plugin_id,
                    "user_id": user_id,
                    "keys": sorted(metadata.keys()),
                },
                distributed=False,
            )

    # ------------------------------------------------------------------
    # Device plugin state
    # ------------------------------------------------------------------

    async def set_device_enabled(
        self, device_id: str, plugin_id: str, enabled: bool,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.set_device_enabled",
            device_id=device_id, plugin_id=plugin_id, enabled=enabled,
        ):
            async def _do(session: AsyncSession):
                repo = DevicePluginRepository(session)
                await repo.set_device_enabled(device_id, plugin_id, enabled)

            await self._with_session(_do)
            self._invalidate()
            logger.info(
                "Set device enabled: device={} plugin={} enabled={}",
                device_id, plugin_id, enabled,
            )

            await self._publish(
                "plugin.device_enabled",
                {
                    "plugin_id": plugin_id,
                    "device_id": device_id,
                    "enabled": enabled,
                },
                distributed=True,
            )

    async def build_uninstall_request(
        self, plugin_id: str, device_id: str,
    ) -> Dict[str, Any]:
        plugin = self.require_plugin(plugin_id)
        return {
            "type": "plugin.uninstall",
            "request_id": f"uninstall:{plugin_id}:{device_id}",
            "device_id": device_id,
            "plugin": {"id": plugin.id, "version": plugin.version},
        }

    async def mark_installed_on_device(
        self,
        device_id: str,
        plugin_id: str,
        version: str,
        artifact_sha256: str | None = None,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.mark_installed",
            device_id=device_id, plugin_id=plugin_id, version=version,
        ):
            async def _do(session: AsyncSession):
                repo = DevicePluginRepository(session)
                await repo.mark_installed(
                    device_id, plugin_id, version, artifact_sha256,
                )

            await self._with_session(_do)
            self._invalidate()
            logger.info(
                "Marked {}@{} installed on device {}",
                plugin_id, version, device_id,
            )

            await self._publish(
                "plugin.installed",
                {
                    "plugin_id": plugin_id,
                    "device_id": device_id,
                    "version": version,
                    "artifact_sha256": artifact_sha256,
                },
                distributed=True,
            )

    async def mark_uninstalled_on_device(
        self, device_id: str, plugin_id: str,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "plugin_manager.mark_uninstalled",
            device_id=device_id, plugin_id=plugin_id,
        ):
            async def _do(session: AsyncSession):
                repo = DevicePluginRepository(session)
                await repo.mark_uninstalled(device_id, plugin_id)

            await self._with_session(_do)
            self._invalidate()

            await self._publish(
                "plugin.uninstalled",
                {"plugin_id": plugin_id, "device_id": device_id},
                distributed=True,
            )

    async def record_device_error(
        self, device_id: str, plugin_id: str, error: str,
    ) -> None:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            await repo.record_error(device_id, plugin_id, error)

        await self._with_session(_do)

        self._emit(
            EventType.PLUGIN_ERROR_RECORDED,
            {
                "plugin_id": plugin_id,
                "device_id": device_id,
                "error": error[:200],
            },
            distributed=False,
        )

    async def reconcile_device_plugins(
        self, device_id: str, device_plugins: list[dict],
    ) -> dict:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            return await repo.reconcile(device_id, device_plugins)

        result = await self._with_session(_do)
        self._invalidate()

        self._emit(
            EventType.PLUGIN_RECONCILED,
            {
                "device_id": device_id,
                "added": result.get("added", []) if isinstance(result, dict) else [],
                "updated": result.get("updated", []) if isinstance(result, dict) else [],
                "removed": result.get("removed", []) if isinstance(result, dict) else [],
                "reported": result.get("reported", 0) if isinstance(result, dict) else 0,
            },
            distributed=False,
        )

        return result or {}

    async def report_device_permissions(
        self, *, device_id: str, plugins: Dict[str, Dict[str, str]],
    ) -> Dict[str, List[str]]:
        """Record what the user has allowed ON THE DEVICE, as the device reports it.

        Advisory: the device re-checks before every invoke; this only lets the server stop offering a
        capability the device would refuse and explain why. A report is trusted for nothing else, so it is
        deliberately narrow: only plugins INSTALLED on that device, only permissions that plugin's manifest
        declares, only the three known states. Anything else is ignored (never an error: a device must not
        be able to make the server reject its whole report because of one odd entry).
        """
        updated: List[str] = []
        ignored: List[str] = []

        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            for plugin_id, perms in plugins.items():
                record = await repo.get(device_id, plugin_id)
                if record is None or not record.is_installed:
                    ignored.append(plugin_id)
                    continue
                manifest = (self.registry.get_manifest(plugin_id, record.version)
                            or self.registry.get_manifest(plugin_id))
                declared = {str(getattr(p, "value", p)) for p in (manifest.permissions if manifest else [])}
                clean = {k: v for k, v in perms.items() if k in declared and v in PERMISSION_STATES}
                if not clean:
                    ignored.append(plugin_id)
                    continue
                await repo.set_permissions(device_id, plugin_id, clean)
                updated.append(plugin_id)

        await self._with_session(_do)
        if updated:
            self._invalidate()          # the router's capability index depends on this
            self._emit("plugin.permissions_reported", {"device_id": device_id, "plugins": updated},
                       distributed=False)
        return {"updated": updated, "ignored": ignored}

    async def list_device_plugins(self, device_id: str) -> list[dict]:
        if self._session_factory is None:
            return []

        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            return await repo.list_for_device(device_id)

        records = await self._with_session(_do)

        if not records:
            logger.warning(
                "No device plugin records found for device {}. "
                "This may indicate a device that has never reported its plugin state.",
                device_id,
            )
            return []
        return [
            {
                "plugin_id": r.plugin_id,
                "version": r.version,
                "is_installed": r.is_installed,
                "enabled_on_device": r.enabled_on_device,
                "artifact_sha256": r.artifact_sha256,
                "installed_at": (
                    r.installed_at.isoformat() if r.installed_at else None
                ),
                "last_error": r.last_error,
                "permissions": dict(r.permissions or {}),
                "permissions_reported_at": (
                    r.permissions_reported_at.isoformat() if r.permissions_reported_at else None
                ),
            }
            for r in records
        ]

    # ------------------------------------------------------------------
    # Install request builder (kept for compatibility)
    # ------------------------------------------------------------------

    async def build_install_request(
        self, plugin_id: str, device_id: str,
    ) -> Dict[str, Any]:
        """
        NOTE: The API route builds the WS payload directly from the
        per-platform manifest. This helper is kept only for callers that
        already know they want the merged/latest view.
        """
        plugin = self.require_plugin(plugin_id)
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
    # Serialization / status
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_plugin(
        plugin: PluginInfo, is_enabled: bool = False,
    ) -> Dict[str, Any]:
        return {
            "id": plugin.id,
            "name": plugin.name,
            "version": plugin.version,
            "description": plugin.description,
            "type": plugin.type,
            "platforms": plugin.platforms,
            "architectures": plugin.architectures,
            "device_types": plugin.deviceTypes,
            "capabilities": plugin.capabilities,
            "package_url": plugin.packageUrl,
            "checksum": plugin.checksum,
            "size": plugin.size,
            "is_enabled": is_enabled,
        }

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            **await super().get_status(user_id=user_id),
            "plugin_count": len(self._catalog),
            "plugins": list(self._catalog.keys()),
            "plugin_registry_dir": str(self.plugin_registry_dir),
        }