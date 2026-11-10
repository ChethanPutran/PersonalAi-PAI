"""Live capability index.

Routes every capability to one of two executors:

  - `device_executor` — plugins whose artifact runs on the device
    (installed via `device_plugins`, downloaded from `plugin_registry`).
    Dispatches an `invoke` frame over the device WebSocket.

  - `server_executor` — plugins that run as Python code on the backend
    (browser, planner). No device installation needed; the "device_id"
    in the target is the session's device, not a delivery address.

The router no longer dispatches to `mobile_executor`, `desktop_executor`,
or `android_executor`. Those were per-transport executors. Now there is
one transport per *kind* of plugin: WebSocket for device plugins, in-
process for server plugins.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.devices.manager import DeviceManager
from pai.devices.models import DeviceStatus
from pai.observability.tracer import get_tracer
from pai.plugins.manager import PluginManager
from pai.storage.repositories.device_plugin import DevicePluginRepository
from pai.storage.repositories.plugin import UserPluginRepository
from pai.common.manager import Manager

# ============================================================================
# Executor ids
# ============================================================================
# Only two executors exist. Every capability routes to one of them based
# on the *plugin*, not the device. A camera plugin on a laptop uses
# `device_executor`; a browser plugin on the same laptop uses
# `server_executor`.
_EXECUTOR_DEVICE = "device_executor"
_EXECUTOR_SERVER = "server_executor"


# ============================================================================
# Platform aliases
# ============================================================================
_PLATFORM_ALIASES = {
    "android": {"android"},
    "ios":     {"ios"},
    "linux":   {"linux"},
    "windows": {"windows"},
    "macos":   {"macos", "darwin", "osx"},
}

_CACHE_TTL_SECONDS = 30.0


# ============================================================================
# Target / error dataclasses
# ============================================================================

@dataclass(frozen=True)
class CapabilityTarget:
    capability: str
    plugin_id: str
    device_id: str
    executor_id: str
    device_type: str
    is_preferred: bool = False
    score: float = 0.0


@dataclass(frozen=True)
class CapabilityError:
    capability: str
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)


class CapabilityNotAvailable(RuntimeError):
    def __init__(self, error: CapabilityError):
        super().__init__(f"{error.capability}: {error.reason}")
        self.error = error


@dataclass
class _CacheEntry:
    index: Dict[str, List[CapabilityTarget]]
    built_at: float


# ============================================================================
# CapabilityRouter
# ============================================================================

class CapabilityRouter(Manager):             
    name = "capability_router"
    def __init__(
        self,
        plugin_manager: PluginManager,
        device_manager: DeviceManager,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
    ):
        super().__init__(session_factory=session_factory)   
        self.plugin_manager = plugin_manager
        self.device_manager = device_manager
        self._cache: Dict[str, _CacheEntry] = {}
        # user_id → list of async callbacks fired on invalidate
        self._on_change: list = []

    def register_change_listener(self, callback) -> None:
        """
        Register an async callback invoked after a cache invalidation.

        Signature: `async def callback(user_id: str | None) -> None`.
        Called synchronously from `invalidate()` via task scheduling so
        slow listeners cannot block the caller.
        """
        self._on_change.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _on_initialize(self) -> None:                
        if not self.plugin_manager.is_initialized():
            raise RuntimeError("CapabilityRouter: plugin_manager not initialized")
        logger.info(
            "CapabilityRouter initialized ({} plugins in catalog)",
            len(self.plugin_manager.get_catalog()),
        )

    def is_initialized(self) -> bool:
        is_initialized = getattr(self.plugin_manager, "is_initialized", None)
        return bool(is_initialized()) if callable(is_initialized) else False

    async def _on_shutdown(self) -> None:                  
        self._cache.clear()
        logger.info("CapabilityRouter shutdown complete")

    async def get_status(self,*, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "cached_users": list(self._cache.keys()),
            "catalog_plugins": len(self.plugin_manager.get_catalog()),
        }

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def invalidate(self, user_id: Optional[str] = None) -> None:
        """Unchanged behaviour, plus fire listeners asynchronously."""
        # existing cache-clear logic
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

        if not self._on_change:
            return

        import asyncio
        loop = asyncio.get_event_loop_policy().get_event_loop()
        for cb in self._on_change:
            try:
                loop.create_task(cb(user_id))
            except RuntimeError:
                # No running loop (e.g. during shutdown) — skip silently
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(
        self,
        *,
        capability: str,
        user_id: str,
        preferred_device_id: Optional[str] = None,
    ) -> List[CapabilityTarget]:
        tracer = get_tracer()
        async with tracer.span(
            "router.resolve",
            capability=capability,
            user_id=user_id,
            preferred_device_id=preferred_device_id,
        ) as span:

            index = await self._get_index(user_id)
            targets = list(index.get(capability, []))
            span.set_attribute("candidate_count", len(targets))

            if not targets:
                diag = await self._diagnose(capability, user_id)
                span.set_error(f"unavailable: {diag.detail.get('stage')}")
                raise CapabilityNotAvailable(diag)

            targets.sort(
                key=lambda t: (
                    0 if t.device_id == preferred_device_id else 1,
                    -t.score,
                    t.device_id,
                )
            )

            if preferred_device_id and targets[0].device_id == preferred_device_id:
                targets[0] = CapabilityTarget(
                    **{**targets[0].__dict__, "is_preferred": True},
                )

            span.set_attribute(
                "chosen",
                {
                    "device_id": targets[0].device_id,
                    "executor_id": targets[0].executor_id,
                    "plugin_id": targets[0].plugin_id,
                    "score": targets[0].score,
                },
            )

            logger.debug(
                "Router: capability={} user={} → {} target(s): {}",
                capability, user_id, len(targets),
                [f"{t.device_id}@{t.executor_id}" for t in targets],
            )
            return targets

    async def has_capability(self, capability: str, user_id: str) -> bool:
        try:
            await self.resolve(capability=capability, user_id=user_id)
            return True
        except CapabilityNotAvailable:
            return False

    async def list_capabilities(self, user_id: str) -> List[str]:
        index = await self._get_index(user_id)
        return sorted(index.keys())

    async def describe_capabilities(
        self, user_id: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        index = await self._get_index(user_id)
        return {
            cap: [
                {
                    "plugin_id": t.plugin_id,
                    "device_id": t.device_id,
                    "executor_id": t.executor_id,
                    "device_type": t.device_type,
                }
                for t in targets
            ]
            for cap, targets in index.items()
        }

    async def describe_capabilities_with_schema(
        self, user_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        tracer = get_tracer()
        async with tracer.span(
            "router.describe_with_schema",
            user_id=user_id,
        ) as span:

            targets_by_cap = await self.describe_capabilities(user_id)
            catalog = {p.id: p for p in self.plugin_manager.get_catalog()}

            out: Dict[str, Dict[str, Any]] = {}
            missing_schema = 0

            for cap_id, targets in targets_by_cap.items():
                schema_entry = None
                for target in targets:
                    plugin = catalog.get(target["plugin_id"])
                    if plugin and cap_id in getattr(plugin, "capability_schemas", {}):
                        schema_entry = plugin.capability_schemas[cap_id]
                        break
                if schema_entry is None:
                    schema_entry = {"description": "", "parameters": {}}
                    missing_schema += 1

                out[cap_id] = {
                    "id": cap_id,
                    "description": schema_entry.get("description", ""),
                    "parameters": schema_entry.get("parameters", {}),
                    "targets": targets,
                }

            span.set_attribute("capability_count", len(out))
            span.set_attribute("missing_schema_count", missing_schema)
            return out

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    async def _get_index(self, user_id: str) -> Dict[str, List[CapabilityTarget]]:
        entry = self._cache.get(user_id)
        if entry and (time.time() - entry.built_at) < _CACHE_TTL_SECONDS:
            return entry.index

        index = await self._build_index(user_id)
        self._cache[user_id] = _CacheEntry(index=index, built_at=time.time())
        return index

    async def _build_index(self, user_id: str) -> Dict[str, List[CapabilityTarget]]:
        tracer = get_tracer()
        async with tracer.span("router.build_index", user_id=user_id) as span:

            # ---- 1. User-enabled plugins ----
            enabled_ids = await self._user_enabled_plugin_ids(user_id)
            span.set_attribute("user_enabled_plugins", sorted(enabled_ids))
            if not enabled_ids:
                span.set_attribute("stage", "no_user_plugins")
                return {}

            # ---- 2. Those in the catalog with capabilities ----
            catalog = {p.id: p for p in self.plugin_manager.get_catalog()}
            span.set_attribute(
                "catalog_capability_counts",
                {pid: len(p.capabilities) for pid, p in catalog.items()},
            )
            span.set_attribute(
                "missing_from_catalog",
                [pid for pid in enabled_ids if pid not in catalog],
            )

            capable = {
                pid: catalog[pid]
                for pid in enabled_ids
                if pid in catalog and catalog[pid].capabilities
            }
            span.set_attribute("capable_plugins", sorted(capable.keys()))
            if not capable:
                span.set_attribute("stage", "no_capable_plugins")
                return {}

            # ---- 3. User's devices ----
            devices = await self.device_manager.list_devices(user_id)
            online: Dict[str, Any] = {}
            offline_reasons: Dict[str, str] = {}

            for d in devices:
                status = getattr(d, "status", "unknown")
                if status == DeviceStatus.ONLINE:
                    online[d.id] = d
                else:
                    offline_reasons[d.id] = status

            span.set_attribute(
                "device_breakdown",
                {
                    "total":   len(devices),
                    "online":  len(online),
                    "offline": len(offline_reasons),
                },
            )
            span.set_attribute(
                "online_devices",
                [
                    {
                        "id":       d.id,
                        "name":     getattr(d, "name", "") or "",
                        "type":     getattr(d, "device_type", "unknown"),
                        "platform": getattr(d, "platform", "unknown"),
                        "user_id":  getattr(d, "user_id", None),
                    }
                    for d in online.values()
                ],
            )
            if offline_reasons:
                span.set_attribute("offline_devices", offline_reasons)

            if not online:
                span.set_attribute("stage", "no_online_devices")
                return {}

            # ---- 4. Per-device plugin state ----
            # Only device plugins appear here — server plugins are not
            # "installed" on any device, so they'll be handled below
            # separately.
            device_plugins = await self._device_enabled_plugin_ids(
                list(online.keys()),
            )
            span.set_attribute(
                "device_plugin_state",
                {did: sorted(pids) for did, pids in device_plugins.items()},
            )

            # ---- 5. Cross-product ----
            index: Dict[str, List[CapabilityTarget]] = {}
            skipped: List[Dict[str, str]] = []
            server_plugins: List[str] = []

            for plugin_id, plugin in capable.items():
                executor_id = self._executor_for(plugin)

                if executor_id == _EXECUTOR_SERVER:
                    # Server plugins run on the backend and are available
                    # to *every* online device (the device_id is the
                    # session's device, not a delivery target). Add one
                    # target per online device so preferred-device
                    # ranking still works.
                    server_plugins.append(plugin_id)
                    for device_id, device in online.items():
                        for cap in plugin.capabilities:
                            index.setdefault(cap, []).append(
                                CapabilityTarget(
                                    capability=cap,
                                    plugin_id=plugin_id,
                                    device_id=device_id,
                                    executor_id=_EXECUTOR_SERVER,
                                    device_type=getattr(device, "device_type", "") or "",
                                    score=self._score(device, plugin),
                                )
                            )
                    continue

                # Device plugins: only add targets for devices that have
                # the plugin installed AND enabled in `device_plugins`.
                for device_id, device in online.items():
                    if plugin_id not in device_plugins.get(device_id, set()):
                        skipped.append({
                            "device": device_id,
                            "plugin": plugin_id,
                            "reason": "not installed/enabled on device",
                        })
                        continue

                    for cap in plugin.capabilities:
                        index.setdefault(cap, []).append(
                            CapabilityTarget(
                                capability=cap,
                                plugin_id=plugin_id,
                                device_id=device_id,
                                executor_id=_EXECUTOR_DEVICE,
                                device_type=getattr(device, "device_type", "") or "",
                                score=self._score(device, plugin),
                            )
                        )

            span.set_attribute("capabilities", sorted(index.keys()))
            span.set_attribute(
                "targets_total", sum(len(v) for v in index.values()),
            )
            span.set_attribute("server_plugins", sorted(server_plugins))
            if skipped:
                span.set_attribute("skipped_combinations", skipped[:20])

            logger.info(
                "Router: built index for user={} "
                "capabilities={} targets={} "
                "(devices online={}, offline={}, skipped={}, "
                "server_plugins={})",
                user_id,
                len(index),
                sum(len(v) for v in index.values()),
                len(online),
                len(offline_reasons),
                len(skipped),
                len(server_plugins),
            )
            return index

    # ------------------------------------------------------------------
    # Repository queries
    # ------------------------------------------------------------------

    async def _user_enabled_plugin_ids(self, user_id: str) -> set[str]:
        if self._session_factory is None:
            return set()
        session = self._session_factory()
        try:
            repo = UserPluginRepository(session)
            records = await repo.list_for_user(user_id)
            return {r.plugin_id for r in records if r.is_enabled}
        finally:
            await session.close()

    async def _device_enabled_plugin_ids(
        self, device_ids: List[str],
    ) -> Dict[str, set[str]]:
        if self._session_factory is None or not device_ids:
            return {d: set() for d in device_ids}

        out: Dict[str, set[str]] = {d: set() for d in device_ids}
        session = self._session_factory()
        try:
            repo = DevicePluginRepository(session)
            for device_id in device_ids:
                for r in await repo.list_for_device(device_id):
                    if r.is_installed and r.enabled_on_device:
                        out[device_id].add(r.plugin_id)
        finally:
            await session.close()
        return out

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    async def _diagnose(self, capability: str, user_id: str) -> CapabilityError:
        tracer = get_tracer()
        async with tracer.span(
            "router.diagnose",
            capability=capability,
            user_id=user_id,
        ) as span:

            catalog = self.plugin_manager.get_catalog()
            providers = [p for p in catalog if capability in p.capabilities]
            if not providers:
                span.set_attribute("stage", "not_in_catalog")
                return CapabilityError(
                    capability=capability,
                    reason=(
                        f"No plugin in the catalog provides '{capability}'. "
                        f"Install a plugin that does, then enable it."
                    ),
                    detail={"stage": "not_in_catalog"},
                )

            enabled = await self._user_enabled_plugin_ids(user_id)
            enabled_providers = [p for p in providers if p.id in enabled]
            if not enabled_providers:
                span.set_attribute("stage", "not_enabled_by_user")
                return CapabilityError(
                    capability=capability,
                    reason=(
                        f"'{capability}' is provided by "
                        f"{[p.id for p in providers]} but none are enabled "
                        f"for your account. Enable the plugin first."
                    ),
                    detail={
                        "stage": "not_enabled_by_user",
                        "providers": [p.id for p in providers],
                    },
                )

            # Server plugin → only needs an online device (any will do)
            server_providers = [
                p for p in enabled_providers
                if self._executor_for(p) == _EXECUTOR_SERVER
            ]
            devices = await self.device_manager.list_devices(user_id)
            online = [d for d in devices if d.status == DeviceStatus.ONLINE.value]

            if not online:
                span.set_attribute("stage", "no_online_devices")
                return CapabilityError(
                    capability=capability,
                    reason=(
                        f"'{capability}' is enabled but you have no online "
                        f"devices. Connect a device and retry."
                    ),
                    detail={
                        "stage": "no_online_devices",
                        "providers": [p.id for p in enabled_providers],
                    },
                )

            if server_providers:
                # Device is online, plugin is server-side → should have
                # worked. Something else is wrong (cache staleness,
                # executor not registered, etc.)
                span.set_attribute("stage", "server_plugin_missing_from_index")
                return CapabilityError(
                    capability=capability,
                    reason=(
                        f"'{capability}' is served by server plugins "
                        f"{[p.id for p in server_providers]}, and devices "
                        f"are online — but it's not in the index. The "
                        f"executor may be misconfigured or the cache is "
                        f"stale."
                    ),
                    detail={
                        "stage": "server_plugin_missing_from_index",
                        "providers": [p.id for p in server_providers],
                        "online_devices": [d.id for d in online],
                    },
                )

            # Device plugin → needs installation + enablement on a compatible device
            provider_ids = [p.id for p in enabled_providers]
            state = await self._device_enabled_plugin_ids([d.id for d in online])

            installed_somewhere = False
            compatible_somewhere = False
            for device_id, plugin_set in state.items():
                if not any(pid in plugin_set for pid in provider_ids):
                    continue
                installed_somewhere = True
                device = next(d for d in online if d.id == device_id)
                for plugin in enabled_providers:
                    if self._plugin_supports_device(plugin, device):
                        compatible_somewhere = True
                        break
                if compatible_somewhere:
                    break

            stage = (
                "not_enabled_on_device" if installed_somewhere
                else "not_installed_on_device"
            )
            if installed_somewhere and not compatible_somewhere:
                stage = "platform_mismatch"

            span.set_attribute("stage", stage)

            return CapabilityError(
                capability=capability,
                reason=(
                    f"'{capability}' is enabled but no online device has "
                    f"plugin(s) {provider_ids} installed AND enabled for "
                    f"this platform. Install and enable the plugin on a "
                    f"compatible device."
                ),
                detail={
                    "stage": stage,
                    "providers": provider_ids,
                    "online_devices": [d.id for d in online],
                    "device_platforms": {
                        d.id: getattr(d, "platform", "unknown") for d in online
                    },
                },
            )

    # ------------------------------------------------------------------
    # Executor mapping — one function, one job
    # ------------------------------------------------------------------

    @staticmethod
    def _executor_for(plugin: Any) -> str:
        """
        Return the executor id for `plugin`.

        Only two executors exist:

          - `device_executor` — plugins whose artifact is downloaded and
            loaded on the device (camera, notifications, etc.). These
            carry a `package_url` because `PluginManager._load_device_plugin`
            always sets one.

          - `server_executor` — server-side Python plugins (browser,
            planner). Loaded via `PluginRegistry`; no downloadable
            artifact, no device installation.

        Preference order:
          1. `plugin.runtime.kind` — for callers that pass a full
             PluginManifest (has runtime metadata)
          2. `plugin.package_url` — the reliable signal on PluginInfo
          3. Default to server_executor
        """
        # 1. Explicit runtime kind (full manifests)
        runtime = getattr(plugin, "runtime", None)
        if runtime is not None:
            kind = str(getattr(runtime, "kind", "") or "").lower()
            if kind in ("device", "native"):
                return _EXECUTOR_DEVICE
            if kind == "server":
                return _EXECUTOR_SERVER

        # 2. Downloadable artifact → device plugin
        if getattr(plugin, "package_url", None):
            return _EXECUTOR_DEVICE

        # 3. Everything else runs in-process
        return _EXECUTOR_SERVER

    @staticmethod
    def _plugin_supports_device(plugin: Any, device: Any) -> bool:
        """True if the plugin declares support for the device's platform."""
        plugin_platforms = {
            (p or "").lower()
            for p in (getattr(plugin, "platforms", []) or [])
            if p
        }
        if not plugin_platforms:
            return True

        device_platform = (getattr(device, "platform", "") or "").lower()
        if not device_platform:
            return True

        allowed = _PLATFORM_ALIASES.get(device_platform, {device_platform})
        return bool(plugin_platforms & allowed)

    @staticmethod
    def _score(device: Any, plugin: Any) -> float:
        """Ranking score — prefer the right device for the capability."""
        dt = (getattr(device, "device_type", "") or "").lower()
        caps = getattr(plugin, "capabilities", []) or []

        score = 0.0
        if any("camera" in c or "microphone" in c for c in caps):
            score += 10 if dt in ("mobile", "phone", "tablet") else 0
        if any("browser" in c or "desktop" in c or "screenshot" in c for c in caps):
            score += 10 if dt in ("desktop", "laptop", "workstation") else 0
        return score