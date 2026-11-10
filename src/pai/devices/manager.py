"""Device manager."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from pai.devices.connection import DeviceConnection
from pai.devices.models import (
    DeviceFilter,
    DeviceInfo,
    DevicePlatform,
    DeviceRegistration,
    DeviceStatus,
    DeviceType,
)
from pai.devices.registry import DeviceRegistry
from pai.devices.utils import _normalize_status, _device_summary
from pai.observability.tracer import get_tracer
from pai.storage.repositories.device import DeviceRepository
from pai.common.manager import Manager

# ============================================================================
# Helpers
# ============================================================================




# ============================================================================
# DeviceManager
# ============================================================================

class DeviceManager(Manager):
    """
    High-level device management facade.

    The registry (in-memory) is the runtime view; the DB is the durable
    view. On `initialize()` the DB is loaded into the registry. Every
    state change is mirrored to both.
    """
    name = "device_manager"

    def __init__(
        self,
        session_factory: Optional[Callable[[], AsyncSession]],
    ) -> None:
        super().__init__(session_factory=session_factory)
        self.registry = DeviceRegistry()
        self._connections: Dict[str, DeviceConnection] = {}
        self._capability_router: Any = None

    # ------------------------------------------------------------------
    # Router wiring
    # ------------------------------------------------------------------

    def set_capability_router(self, router: Any) -> None:
        self._capability_router = router

    def _invalidate_router(self, user_id: Optional[str] = None) -> None:
        if self._capability_router is None:
            logger.warning(
                "Capability router not set — cannot invalidate for user {}", user_id,
            )
            return
        try:
            self._capability_router.invalidate(user_id)
        except Exception as exc:
            logger.warning("Router invalidation failed: {}", exc)


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _on_initialize(self):
        tracer = get_tracer()
        async with tracer.span("device_manager.initialize") as span:
            await self.registry.initialize()

            if self._session_factory is not None:
                try:
                    await self._load_from_db()
                except Exception as exc:
                    logger.warning("Failed to load devices from DB: {}", exc)
                    span.set_error(str(exc))

            devices = await self.registry.all()
            span.set_attribute("device_count", len(devices))
            span.set_attribute(
                "devices",
                [
                    _device_summary(d, connected=d.id in self._connections)
                    for d in devices
                ],
            )

            status_breakdown: Dict[str, int] = {}
            for d in devices:
                key = _normalize_status(getattr(d, "status", None))
                status_breakdown[key] = status_breakdown.get(key, 0) + 1
            span.set_attribute("status_breakdown", status_breakdown)

            logger.info(
                "Device manager initialized ({} devices: {})",
                len(devices), status_breakdown,
            )

    async def _load_from_db(self) -> None:
        """
        Load every DeviceModel row into the registry.

        Per-row try/except — one bad row is skipped with a warning, the
        rest load normally. The span records both loaded and failed rows
        so the trace shows exactly what happened at startup.
        """
        tracer = get_tracer()
        async with tracer.span("device_manager.load_from_db") as span:
            async def _do(session: AsyncSession):
                repo = DeviceRepository(session)
                return await repo.list_all()

            records = await self._with_session(_do) or []
            span.set_attribute("row_count", len(records))

            loaded: List[Dict[str, Any]] = []
            failed: List[Dict[str, str]] = []

            if not records:
                logger.info("No devices found in DB")
                return

            for r in records:
                try:
                    if (r.user_id is None):
                        raise ValueError(
                            f"Device {r.id} not owned by user {r.user_id}"
                        )
                    info = DeviceInfo(
                        id=r.id,
                        name=r.name or "",
                        user_id=r.user_id,
                        device_type= DeviceType(r.device_type) if r.device_type else DeviceType.UNKNOWN,
                        status=_normalize_status(r.status),
                        app_version=r.app_version or "",
                        runtime_version=r.runtime_version or "",
                        platform= DevicePlatform(r.platform) if r.platform else DevicePlatform.UNKNOWN,
                        platform_version=r.platform_version or "",
                        os_version=r.os_version or "",
                        architecture=r.architecture or "",
                        hostname=r.hostname or "",
                        capabilities=r.capabilities or [],
                    )
                    await self.registry.register(info)
                    loaded.append({
                        "id":       r.id,
                        "name":     info.name,
                        "status":   info.status,
                        "user_id":  r.user_id,
                        "raw_status_in_db": str(r.status),
                    })
                except Exception as exc:
                    logger.warning(
                        "Skipping bad device row {}: {}", r.id, exc,
                    )
                    failed.append({
                        "device_id": str(r.id),
                        "error": str(exc)[:200],
                    })

            span.set_attribute("loaded", loaded)
            span.set_attribute("failed", failed)

            logger.info(
                "Loaded {} of {} devices from DB ({} skipped)",
                len(loaded), len(records), len(failed),
            )

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Return a compact status summary for a user.

        Uses `registry.all()` (unfiltered) plus a Python filter so a
        registry bug in `all(user_id)` can't hide devices from callers
        that rely on this method for health checks.
        """
        try:
            all_devices = await self.registry.all()
            registered = [
                d for d in all_devices
                if getattr(d, "user_id", None) == user_id
            ]
        except Exception:
            registered = []

        return {
            "initialized":        self._initialized,
            "registered_devices": len(registered),
            "connected_devices":  len(self._connections),
        }

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(
        self,
        registration: DeviceRegistration | DeviceInfo,
        *,
        user_id: str,
        connection_type: Optional[str] = None,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> DeviceInfo:
        """
        Register a device. Does NOT establish a network connection —
        the WebSocket endpoint does that later via `attach_connection`.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.register",
            user_id=user_id,
            connection_type=connection_type,
        ) as span:

            if isinstance(registration, DeviceRegistration):
                data = registration.device.model_dump()
                device = DeviceInfo(
                    id=str(uuid4()),
                    user_id=user_id,
                    **data,
                )
                connection_type = registration.connection_type or connection_type
                connection_config = (
                    registration.connection_config or connection_config
                )
            else:
                device = registration
                if not device.id:
                    device.id = str(uuid4())
                device.user_id = user_id

            device.status = _normalize_status(getattr(device, "status", None))

            span.set_attribute("device_id", device.id)
            span.set_attribute("device_type", device.device_type)
            span.set_attribute("platform", device.platform)
            span.set_attribute("status", device.status)

            # 1. Persist first
            try:
                await self._persist_upsert(device)
            except Exception as exc:
                logger.warning(
                    "Failed to persist device {} to DB: {}", device.id, exc,
                )
                span.set_attribute("persist_failed", True)

            # 2. Mirror in memory
            await self.registry.register(device)
            self._invalidate_router(user_id)

            logger.info("Device registered: {} ({})", device.id, device.name)
            return device

    async def _persist_upsert(self, device) -> None:
        async def _do(session):
            repo = DeviceRepository(session)
            await repo.upsert(
                device_id=device.id,
                name=getattr(device, "name", "") or "",
                user_id=getattr(device, "user_id", None),
                device_type=getattr(device, "device_type", "unknown") or "unknown",
                platform=getattr(device, "platform", "unknown") or "unknown",
                platform_version=getattr(device, "platform_version", "") or "",
                os_version=getattr(device, "os_version", "") or "",
                architecture=getattr(device, "architecture", "") or "",
                hostname=getattr(device, "hostname", "") or "",
                app_version=getattr(device, "app_version", "") or "",
                runtime_version=getattr(device, "runtime_version", "") or "",
                status=_normalize_status(getattr(device, "status", None)),
                capabilities=getattr(device, "capabilities", []) or [],
            )
        await self._with_session(_do)

    async def unregister(self, device_id: str) -> bool:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.unregister", device_id=device_id,
        ) as span:
            existing = await self.registry.get(device_id)
            user_id = getattr(existing, "user_id", None) if existing else None

            await self.disconnect(device_id)

            try:
                async def _do(session: AsyncSession):
                    repo = DeviceRepository(session)
                    await repo.delete(device_id)
                await self._with_session(_do)
            except Exception as exc:
                logger.warning(
                    "Failed to delete device {} from DB: {}", device_id, exc,
                )
                span.set_attribute("db_delete_failed", True)

            removed = await self.registry.unregister(device_id)
            span.set_attribute("removed", removed)
            self._invalidate_router(user_id)
            return removed

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def get(self, device_id: str) -> Optional[DeviceInfo]:
        return await self.registry.get(device_id)

    async def require(self, device_id: str) -> DeviceInfo:
        return await self.registry.require(device_id)

    async def list_devices(self, user_id: str) -> List[DeviceInfo]:
        """
        Return the devices owned by `user_id`.

        If the registry has no devices for this user but the DB might,
        reload once. Defense against registry drift — if a device was
        persisted to DB but the registry lost it (e.g. process restarted
        after a failed `_load_from_db`), we still return correct data.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.list_devices", user_id=user_id,
        ) as span:
            devices = await self.registry.all(user_id)

            if not devices and self._session_factory is not None:
                logger.info(
                    "Registry empty for user {} — reloading from DB", user_id,
                )
                try:
                    await self._load_from_db()
                    devices = await self.registry.all(user_id)
                    span.set_attribute("reloaded_from_db", True)
                except Exception as exc:
                    logger.warning("Fallback reload failed: {}", exc)
                    span.set_attribute("reload_failed", str(exc)[:200])

            span.set_attribute("count", len(devices))
            span.set_attribute(
                "devices",
                [
                    _device_summary(d, connected=d.id in self._connections)
                    for d in devices
                ],
            )
            return devices

    async def find(self, device_filter: DeviceFilter) -> List[DeviceInfo]:
        return await self.registry.find(device_filter)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    async def update_status(self, device_id: str, status) -> bool:
        tracer = get_tracer()
        status_value = _normalize_status(status)

        async with tracer.span(
            "device_manager.update_status",
            device_id=device_id,
            status=status_value,
        ) as span:
            try:
                await self.registry.update_status(device_id, status_value)

                try:
                    async def _do(session: AsyncSession):
                        repo = DeviceRepository(session)
                        await repo.update_status(device_id, status_value)
                    await self._with_session(_do)
                except Exception as exc:
                    logger.warning(
                        "Failed to persist status for device {}: {}",
                        device_id, exc,
                    )
                    span.set_attribute("persist_failed", True)

                if status_value in (
                    DeviceStatus.ONLINE.value,
                    DeviceStatus.OFFLINE.value,
                ):
                    self._invalidate_router()

                return True

            except Exception as exc:
                logger.error(
                    "Failed to update status for device {}: {}",
                    device_id, exc,
                )
                span.set_error(str(exc))
                return False

    async def _touch_device(self, device_id: str) -> None:
        if self._session_factory is None:
            return
        try:
            async def _do(session: AsyncSession):
                repo = DeviceRepository(session)
                await repo.touch(device_id)
            await self._with_session(_do)
        except Exception as exc:
            logger.debug("Failed to touch device {}: {}", device_id, exc)

    # ------------------------------------------------------------------
    # Live connections
    # ------------------------------------------------------------------

    async def attach_connection(
        self, device_id: str, connection: DeviceConnection,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.attach_connection", device_id=device_id,
        ) as span:

            device = await self._resolve_device_or_reload(device_id, span)
            span.set_attribute("user_id", getattr(device, "user_id", None))
            span.set_attribute(
                "device_type", getattr(device, "device_type", None),
            )

            existing = self._connections.get(device_id)
            if existing is not None:
                try:
                    await existing.disconnect()
                except Exception as exc:
                    logger.warning(
                        "Failed to close existing connection for {}: {}",
                        device_id, exc,
                    )

            self._connections[device_id] = connection

            status_value = DeviceStatus.ONLINE.value
            await self.registry.update_status(device_id, status_value)
            await self.registry.heartbeat(device_id)

            try:
                async def _do(session: AsyncSession):
                    repo = DeviceRepository(session)
                    await repo.update_status(device_id, status_value)
                await self._with_session(_do)
            except Exception as exc:
                logger.warning(
                    "Failed to persist ONLINE status for {}: {}",
                    device_id, exc,
                )
                span.set_attribute("persist_failed", True)

            self._invalidate_router(getattr(device, "user_id", None))
            logger.info("Connection attached to device {}", device_id)

    async def _resolve_device_or_reload(self, device_id: str, span) -> DeviceInfo:
        """
        Return the DeviceInfo for `device_id`.

        Tries the registry first. On miss, reloads from DB. If both fail,
        raises KeyError so the WebSocket can close cleanly.
        """
        try:
            return await self.require(device_id)
        except KeyError:
            pass

        logger.info("Registry miss for {} — reloading from DB", device_id)
        if self._session_factory is None:
            span.set_error("device_not_found")
            raise KeyError(f"Device not found: {device_id}")

        async def _do(session: AsyncSession):
            repo = DeviceRepository(session)
            return await repo.get(device_id)

        row = await self._with_session(_do)
        if row is None:
            span.set_error("device_not_found")
            raise KeyError(f"Device not found: {device_id}")

        if (row.user_id is None):
            span.set_error("device_not_owned")
            raise KeyError(f"Device {device_id} not owned by any user")
        
        info = DeviceInfo(
            id=row.id,
            name=row.name or "",
            user_id=row.user_id,
            device_type=DeviceType(row.device_type) if row.device_type else DeviceType.UNKNOWN,
            status=DeviceStatus(row.status) if row.status else DeviceStatus.UNKNOWN,
            platform= DevicePlatform(row.platform) if row.platform else DevicePlatform.UNKNOWN,
            platform_version=row.platform_version or "",
            os_version=row.os_version or "",
            architecture=row.architecture or "",
            hostname=row.hostname or "",
            app_version=row.app_version or "",
            runtime_version=row.runtime_version or "",
            capabilities=row.capabilities or [],
        )
        await self.registry.register(info)
        logger.info("Restored device {} from DB", device_id)
        span.set_attribute("restored_from_db", True)
        return info

    async def detach_connection(self, device_id: str) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.detach_connection", device_id=device_id,
        ) as span:

            device = await self.registry.get(device_id)
            user_id = getattr(device, "user_id", None) if device else None

            connection = self._connections.pop(device_id, None)
            span.set_attribute("had_connection", connection is not None)

            if connection is not None:
                try:
                    await connection.disconnect()
                except Exception as exc:
                    logger.warning(
                        "Failed to disconnect {}: {}", device_id, exc,
                    )

            status_value = DeviceStatus.OFFLINE.value

            try:
                await self.registry.update_status(device_id, status_value)
            except Exception as exc:
                logger.warning(
                    "Failed to update offline status for {}: {}",
                    device_id, exc,
                )

            try:
                async def _do(session: AsyncSession):
                    repo = DeviceRepository(session)
                    await repo.update_status(device_id, status_value)
                await self._with_session(_do)
            except Exception as exc:
                logger.warning(
                    "Failed to persist OFFLINE status for {}: {}",
                    device_id, exc,
                )

            self._invalidate_router(user_id)
            logger.info("Connection detached from device {}", device_id)

    async def connect(self, device_id: str) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.connect", device_id=device_id,
        ):
            await self.require(device_id)
            connection = self._connections.get(device_id)
            if connection is None:
                raise RuntimeError(
                    f"No connection configured for device {device_id}"
                )

            await self.update_status(device_id, DeviceStatus.CONNECTING)
            try:
                await connection.connect()
                await self.update_status(device_id, DeviceStatus.ONLINE)
                await self.registry.heartbeat(device_id)
            except Exception:
                await self.update_status(device_id, DeviceStatus.ERROR)
                raise

    async def disconnect(self, device_id: str) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.disconnect", device_id=device_id,
        ):
            connection = self._connections.get(device_id)
            if connection is None:
                await self.update_status(device_id, DeviceStatus.OFFLINE)
                return
            try:
                await connection.disconnect()
            finally:
                self._connections.pop(device_id, None)
                await self.update_status(device_id, DeviceStatus.OFFLINE)

    async def is_connected(self, device_id: str) -> bool:
        connection = self._connections.get(device_id)
        return connection is not None and connection.connected

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(
        self, device_id: str, payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        tracer = get_tracer()

        connection = self._connections.get(device_id)
        if connection is None or not connection.connected:
            raise RuntimeError(f"Device {device_id} is not connected")

        capability = payload.get("capability") or payload.get("type", "unknown")
        plugin = (
            payload.get("plugin") or payload.get("plugin_id") or "unknown"
        )

        tracer.log_tool_call(
            capability=capability,
            plugin=plugin,
            device_id=device_id,
            parameters=payload.get("parameters") or payload.get("payload"),
        )

        t0 = time.perf_counter()
        try:
            result = await connection.send(payload)
            duration_ms = (time.perf_counter() - t0) * 1000

            tracer.log_tool_call(
                capability=capability,
                plugin=plugin,
                device_id=device_id,
                result=result,
                duration_ms=duration_ms,
            )

            await self.registry.heartbeat(device_id)
            await self._touch_device(device_id)
            return result

        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            tracer.log_tool_call(
                capability=capability,
                plugin=plugin,
                device_id=device_id,
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise

    async def ping(self, device_id: str) -> bool:
        connection = self._connections.get(device_id)
        if connection is None:
            return False
        return await connection.ping()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _on_shutdown(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.shutdown",
            connected_count=len(self._connections),
        ):
            for device_id in list(self._connections.keys()):
                try:
                    await self.disconnect(device_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to disconnect {}: {}", device_id, exc,
                    )
            self._connections.clear()
            await self.registry.shutdown()
            logger.info("Device manager stopped")

    async def refresh_metadata(
        self, device_id: str, update: dict, user_id: str | None = None,
    ) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.refresh_metadata",
            device_id=device_id,
            updated_keys=list(update.keys()),
        ):
            device = await self.registry.get(device_id)
            if device is None:
                return

            if "status" in update:
                update["status"] = _normalize_status(update["status"])

            for k, v in update.items():
                if hasattr(device, k) and v is not None:
                    setattr(device, k, v)

            if self._session_factory is not None:
                async def _do(session):
                    repo = DeviceRepository(session)
                    await repo.upsert(
                        device_id=device_id,
                        name=update.get("name") or getattr(device, "name", ""),
                        device_type=update.get("device_type") or getattr(device, "device_type", "unknown"),
                        platform=update.get("platform") or getattr(device, "platform", "unknown"),
                        platform_version=update.get("platform_version") or getattr(device, "platform_version", ""),
                        os_version=update.get("os_version") or getattr(device, "os_version", ""),
                        architecture=update.get("architecture") or getattr(device, "architecture", ""),
                        hostname=update.get("hostname") or getattr(device, "hostname", ""),
                        app_version=update.get("app_version") or getattr(device, "app_version", ""),
                        runtime_version=update.get("runtime_version") or getattr(device, "runtime_version", ""),
                        user_id=user_id,
                    )
                await self._with_session(_do)

            self._invalidate_router(user_id)

    async def assign_user(self, device_id: str, user_id: str) -> None:
        """Attach the owning user to a device in both DB and registry."""
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.assign_user",
            device_id=device_id, user_id=user_id,
        ) as span:
            # ---- DB first ----
            if self._session_factory is not None:
                async def _do(session):
                    repo = DeviceRepository(session)
                    record = await repo.get(device_id)
                    if record is not None:
                        record.user_id = user_id
                        await session.flush()
                        return True
                    return False
                updated = await self._with_session(_do)
                span.set_attribute("db_updated", bool(updated))

            # ---- Registry second (was missing before — caused registry
            #      to hold stale ownership after a user reassignment) ----
            device = await self.registry.get(device_id)
            if device is not None:
                device.user_id = user_id
                span.set_attribute("registry_updated", True)
                logger.info(
                    "Reassigned device {} → user {}", device_id, user_id,
                )
            else:
                span.set_attribute("registry_updated", False)
                logger.warning(
                    "assign_user: device {} not in registry — DB only",
                    device_id,
                )

            self._invalidate_router(user_id)