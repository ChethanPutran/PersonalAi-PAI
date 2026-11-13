from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.event_bus.bus import EventBus
from pai.observability.tracer import get_tracer
from pai.plugins.models import PluginInfo, PluginManifest
from pai.plugins.registry import PluginRegistry
from pai.storage.repositories.plugin import UserPluginRepository
from pai.storage.repositories.device_plugin import DevicePluginRepository
from pai.common.manager import Manager

T = TypeVar("T")

PLUGIN_REGISTRY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "plugin_registry"

class PluginManager(Manager):
    def __init__(
        self,
        event_bus:EventBus,
        registry: Optional[PluginRegistry] = None,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
        plugin_registry_dir: Optional[str | Path] = PLUGIN_REGISTRY_DIR,
        **kwargs
        
    ):
        super().__init__(session_factory=session_factory, event_bus=event_bus)
        self.registry = registry
        self._catalog: Dict[str, PluginInfo] = {}
        self._capability_router: Any = None
        if isinstance(plugin_registry_dir, str):
            plugin_registry_dir = Path(plugin_registry_dir)
        self.plugin_registry_dir = plugin_registry_dir

    async def _on_initialize(self) -> None:
        # Move the *entire* body of the current `initialize()` here,
        # minus the `if self._initialized: return` guard and the final
        # `self._initialized = True` — those are now handled by the base.
        tracer = get_tracer()
        async with tracer.span("plugin_manager.initialize") as span:
            logger.info("Initializing backend plugin manager")
            
            # ── Pass 1: server-side Python plugins ──
            # Scans `pai/plugins/*/manifest.json` for plugins with
            # `runtime.kind == "server"`. These are the browser, planner,
            # etc. — Python classes loaded into the backend process.
            if self.registry is not None:
                try:
                    await self.registry.discover()

                    for manifest in self.registry.get_all_manifests():
                        if not getattr(manifest, "id", None):
                            continue
                        try:
                            info = self._manifest_to_info(manifest)
                            self._catalog[info.id] = info
                        except Exception as exc:
                            logger.warning(
                                "Failed to ingest manifest {}: {}",
                                getattr(manifest, "id", "<unknown>"), exc,
                            )

                except Exception as exc:
                    logger.warning("Plugin registry discovery failed: {}", exc)

            server_count = len(self._catalog)

            # ── Pass 2: device plugins from plugin_registry/ ──
            # Scans `plugin_registry/<plugin-id>/<version>/manifest.json`
            # for native plugins like camera, notifications.
            # These manifest entries OVERRIDE any stale static catalog
            # entry with the same id.
            device_count = 0
            device_failures: List[Dict[str, str]] = []

            if self.plugin_registry_dir.exists():
                for plugin_dir in sorted(self.plugin_registry_dir.iterdir()):
                    if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                        continue

                    try:
                        info = self._load_device_plugin(plugin_dir)
                        if info is not None:
                            self._catalog[info.id] = info
                            device_count += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to ingest device plugin {}: {}",
                            plugin_dir.name, exc,
                        )
                        device_failures.append({
                            "plugin": plugin_dir.name,
                            "error": str(exc),
                        })
            else:
                logger.warning(
                    "Device plugin registry not found at {}; "
                    "device plugins will come from the static catalog only",
                    self.plugin_registry_dir,
                )

            static_count = len(self._catalog) - server_count - device_count


            # ── Diagnostic attributes for the trace UI ──
            span.set_attribute("server_plugins", server_count)
            span.set_attribute("device_plugins", device_count)
            span.set_attribute("static_fallback", static_count)
            span.set_attribute("total_plugins", len(self._catalog))
            span.set_attribute("plugin_count", len(self._catalog))
            span.set_attribute("plugins", sorted(self._catalog.keys()))
            span.set_attribute(
                "catalog_capability_counts",
                {pid: len(p.capabilities) for pid, p in self._catalog.items()},
            )
            span.set_attribute(
                "catalog_schema_counts",
                {
                    pid: len(getattr(p, "capability_schemas", {}) or {})
                    for pid, p in self._catalog.items()
                },
            )
            span.set_attribute("device_failures", device_failures)

            logger.info(
                "Backend plugin manager initialized: "
                "{} server + {} device + {} static = {} plugins",
                server_count, device_count, static_count, len(self._catalog),
            )

            # ── EVENT: plugin.catalog_loaded ──
            #
            # Pure observability — the catalog is per-replica (each
            # process loads its own), so this doesn't propagate over
            # NATS. An admin dashboard on the same replica can show
            # "N plugins loaded, M failures" without hitting the manager
            # directly. `_emit` (fire-and-forget) because startup must
            # not block on the bus.
            self._emit(
                "plugin.catalog_loaded",
                {
                    "plugin_count": len(self._catalog),
                    "server_count": server_count,
                    "device_count": device_count,
                    "static_count": static_count,
                    "failures": device_failures,
                },
                distributed=False,
            )


    # ------------------------------------------------------------------
    # Router wiring
    # ------------------------------------------------------------------

    def set_capability_router(self, router: Any) -> None:
        self._capability_router = router

    def _invalidate(self, user_id: Optional[str] = None) -> None:
        if self._capability_router is not None:
            self._capability_router.invalidate(user_id)

    

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

 

    def _load_device_plugin(self, plugin_dir: Path) -> Optional[PluginInfo]:
        """
        Load a single device plugin directory.

        Layout expected:
            plugin_registry/<plugin-id>/
                <version>/manifest.json
                <version>/linux/libpai_*.so
                <version>/android/arm64-v8a/libpai_*.so

        Picks the highest version directory when multiple are present.
        Returns None if no manifest was found — the caller just skips
        the folder without raising.
        """
        versions: List[tuple[str, Path, Dict[str, Any]]] = []

        for version_dir in plugin_dir.iterdir():
            if not version_dir.is_dir():
                continue

            manifest_path = version_dir / "manifest.json"
            if not manifest_path.exists():
                continue

            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                version = str(data.get("version") or version_dir.name)
                versions.append((version, version_dir, data))
            except Exception as exc:
                logger.debug(
                    "Skipping malformed manifest at {}: {}",
                    manifest_path, exc,
                )

        if not versions:
            return None

        # Highest version wins. Simple lexicographic sort — replace with
        # `packaging.version.parse` if you start using semver pre-releases
        # (e.g. "1.0.0-rc1" would sort after "1.0.0" lexicographically).
        versions.sort(key=lambda v: v[0], reverse=True)
        chosen_version, chosen_dir, chosen_data = versions[0]

        try:
            manifest = PluginManifest.from_dict(chosen_data)
        except Exception as exc:
            logger.warning(
                "PluginManifest.from_dict failed for device plugin {}@{}: {}",
                plugin_dir.name, chosen_version, exc,
            )
            raise

        info = self._manifest_to_info(manifest)

        # Attach the artifact URL so the mobile app can download it.
        # Matches the shape returned by /api/v1/plugins/catalog, so clients
        # see a consistent response regardless of where the entry came from.
        info = replace(
            info,
            package_url=f"/api/v1/plugins/{info.id}/{chosen_version}/artifact",
        )

        logger.info(
            "Loaded device plugin '{}' v{} from {} ({} capabilities, "
            "{} schemas)",
            info.id,
            chosen_version,
            chosen_dir,
            len(info.capabilities),
            len(info.capability_schemas),
        )
        return info

    @staticmethod
    def _manifest_to_info(manifest: Any) -> PluginInfo:
        cap_names: List[str] = []
        if hasattr(manifest, "capability_names"):
            cap_names = list(manifest.capability_names)
        elif hasattr(manifest, "capabilities"):
            for c in manifest.capabilities:
                if hasattr(c, "name"):
                    cap_names.append(c.name)
                elif isinstance(c, str):
                    cap_names.append(c)
                elif isinstance(c, dict):
                    cid = c.get("id") or c.get("name")
                    if cid:
                        cap_names.append(cid)
        cap_names = [c for c in cap_names if c]

        schemas: Dict[str, Dict[str, Any]] = {}
        if hasattr(manifest, "capability_schemas"):
            schemas = dict(manifest.capability_schemas or {})

        if not schemas and hasattr(manifest, "capabilities"):
            for c in manifest.capabilities:
                if hasattr(c, "name") and hasattr(c, "parameters"):
                    schemas[c.name] = {
                        "description": getattr(c, "description", "") or "",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                pname: {
                                    "type": p.type,
                                    "description": p.description or "",
                                    **({"default": p.default}
                                       if p.default is not None else {}),
                                }
                                for pname, p in (c.parameters or {}).items()
                            },
                        },
                    }

        platforms = getattr(manifest, "platforms", []) or []
        if isinstance(platforms, dict):
            platforms = list(platforms.keys())
        else:
            platforms = list(platforms)

        return PluginInfo(
            id=str(manifest.id),
            name=getattr(manifest, "name", None) or str(manifest.id),
            version=getattr(manifest, "version", "1.0.0"),
            platforms=platforms,
            architectures=list(getattr(manifest, "architectures", []) or []),
            device_types=list(getattr(manifest, "device_types", []) or []),
            capabilities=cap_names,
            capability_schemas=schemas,
            description=getattr(manifest, "description", "") or "",
            plugin_type=getattr(manifest, "plugin_type", "action") or "action",
        )

    async def shutdown(self) -> None:
        self._catalog.clear()
        self._initialized = False

    # ------------------------------------------------------------------
    # Catalog
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

    async def get_manifest_at_version(
        self, plugin_id: str, version: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Read a specific version's manifest from the plugin_registry dir,
        bypassing the in-memory catalog.

        The catalog only holds the *latest* version per plugin. When a
        caller asks for `browser@1.0.0` after a bump to `1.0.1`, the
        catalog entry says `1.0.1` — but the disk still has `1.0.0/`.
        This method lets the caller install an older version explicitly.
        """
        import json
        from pathlib import Path

        if self.plugin_registry_dir is None:
            return None
        path = Path(self.plugin_registry_dir) / plugin_id / version / "manifest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            logger.warning(
                "Failed to read manifest %s: %s", path, exc
            )
            return None
    
    async def get_plugin_for_capability(
        self, capability: str,
    ) -> Optional[PluginInfo]:
        if not self._initialized:
            await self.initialize()
        for p in self._catalog.values():
            if capability in p.capabilities:
                return p
        return None

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
        device_type: Optional[str] = None
    ) -> bool:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            return False
        return platform in plugin.platforms and architecture in plugin.architectures and (device_type is None or device_type in getattr(plugin, "device_types", []))

    # ------------------------------------------------------------------
    # User plugin state
    # ------------------------------------------------------------------

    async def is_enabled_for_user(
        self, user_id: str, plugin_id: str,
    ) -> bool:
        """True iff the user has enabled the given plugin."""
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
            return result

    async def list_plugins(
        self,
        user_id: Optional[str] = None,
        platform: Optional[str] = None,
        architecture: Optional[str] = None,
        device_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        plugins = self.get_catalog()

        if platform:
            plugins = [p for p in plugins if platform in p.platforms]
        if architecture:
            plugins = [p for p in plugins if architecture in p.architectures]

        if device_type:
            plugins = [p for p in plugins if device_type in p.device_types]

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

            # ── EVENT: plugin.enabled ──
            #
            # `_publish` (awaited) because the CapabilityRouter on
            # *this* replica was just invalidated inline — but any
            # other backend replica still holds a stale per-user cache
            # that says "this plugin is disabled for user X". Awaited
            # delivery ensures the event reaches NATS before `enable()`
            # returns, so a subsequent request that lands on replica B
            # sees fresh capabilities.
            #
            # `distributed=True` — this is precisely the case NATS
            # exists for.
            await self._publish(
                "plugin.enabled",
                {
                    "plugin_id": plugin_id,
                    "user_id": user_id,
                },
                distributed=True,
            )

    async def disable(self, user_id: str, plugin_id: str) -> None:
        await self.require_plugin(plugin_id)

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

            # ── EVENT: plugin.disabled ──
            #
            # Symmetric to `plugin.enabled`. Awaited + distributed for
            # the same reason — cross-replica cache invalidation.
            await self._publish(
                "plugin.disabled",
                {
                    "plugin_id": plugin_id,
                    "user_id": user_id,
                },
                distributed=True,
            )

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

            # ── EVENT: plugin.config_updated ──
            #
            # `_emit` (fire-and-forget) because config changes don't
            # invalidate the capability router — a plugin's availability
            # is unchanged, only its per-user metadata. Observability
            # for the settings UI, not a cache-invalidation trigger.
            #
            # Emit only the keys, not the values — user config may
            # contain secrets (API keys, credentials) and the event
            # bus payload lands in logs and NATS.
            self._emit(
                "plugin.config_updated",
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
        self,
        device_id: str,
        plugin_id: str,
        enabled: bool,
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

            # ── EVENT: plugin.device_enabled ──
            #
            # `_publish(distributed=True)` — the flip changes whether
            # a capability is routable to this device. Replica B's
            # router has a cached "capability X is on device D" entry
            # that's now stale. Awaited so the invalidation lands
            # before the HTTP response returns to the caller.
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
        plugin = await self.require_plugin(plugin_id)
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

            # ── EVENT: plugin.installed ──
            #
            # `_publish(distributed=True)` — a new capability just
            # became routable on `device_id`. Every replica's router
            # cache must drop its "no such capability" verdict for
            # this plugin/device pair, or the next chat request
            # routes nowhere.
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

            # ── EVENT: plugin.uninstalled ──
            #
            # `_publish(distributed=True)` — symmetric to installed.
            # The capability is no longer routable to this device;
            # any replica that cached it needs to invalidate.
            await self._publish(
                "plugin.uninstalled",
                {
                    "plugin_id": plugin_id,
                    "device_id": device_id,
                },
                distributed=True,
            )

    async def record_device_error(
        self, device_id: str, plugin_id: str, error: str,
    ) -> None:
        async def _do(session: AsyncSession):
            repo = DevicePluginRepository(session)
            await repo.record_error(device_id, plugin_id, error)

        await self._with_session(_do)

        # ── EVENT: plugin.error_recorded ──
        #
        # `_emit(distributed=False)` — pure observability. The admin
        # panel wants "this device/plugin had an error"; no cache
        # invalidation needed (an error doesn't change routability).
        # Truncate the error to keep the bus payload small — the full
        # traceback lives in `device_plugins.last_error` if needed.
        self._emit(
            "plugin.error_recorded",
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

        # ── EVENT: plugin.reconciled ──
        #
        # `_emit(distributed=False)` — the reconcile returns an
        # added/updated/removed diff that admin tooling wants to see,
        # but the router invalidation already happened inline via
        # `_invalidate()`. Cross-replica propagation is a separate
        # concern the router will handle on its next cold read.
        #
        # `result` is whatever DevicePluginRepository.reconcile()
        # returns — defensively pull the three keys admin expects.
        self._emit(
            "plugin.reconciled",
            {
                "device_id": device_id,
                "added": result.get("added", []) if isinstance(result, dict) else [],
                "updated": result.get("updated", []) if isinstance(result, dict) else [],
                "removed": result.get("removed", []) if isinstance(result, dict) else [],
                "reported": result.get("reported", 0) if isinstance(result, dict) else 0,
            },
            distributed=False,
        )

        return result

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
                "artifact_sha256": getattr(r, "artifact_sha256", None),   # ← add
                "installed_at": (
                    r.installed_at.isoformat() if r.installed_at else None
                ),
                "last_error": r.last_error,
            }
            for r in records
        ]
    # ------------------------------------------------------------------
    # Install request builder
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
            "description": getattr(plugin, "description", ""),
            "plugin_type": getattr(plugin, "plugin_type", "action"),
            "platforms": list(plugin.platforms),
            "architectures": list(plugin.architectures),
            "device_types": list(plugin.device_types),
            "capabilities": list(plugin.capabilities),
            "capability_schemas": dict(getattr(plugin, "capability_schemas", {})),
            "package_url": getattr(plugin, "package_url", None),
            "checksum": getattr(plugin, "checksum", None),
            "size": getattr(plugin, "size", 0),
            "is_enabled": is_enabled
        }

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            **await super().get_status(user_id=user_id),
            "plugin_count": len(self._catalog),
            "plugins": list(self._catalog.keys()),
            "plugin_registry_dir": str(self.plugin_registry_dir),
        }