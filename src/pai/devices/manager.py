"""Device manager."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from pai.common.enums.device import DeviceArchitecture
from pai.config.models.devices import DeviceConfig
from pai.devices.connection import DeviceConnection
from pai.devices.models import (
    DeviceFilter,
    DeviceInfo,
    DeviceMessage,
    DevicePlatform,
    DeviceRegistration,
    DeviceRegistrationInfo,
    DeviceStatus,
    DeviceType,
)
from pai.devices.registry import DeviceRegistry
from pai.devices.utils import _normalize_status, _device_summary
from pai.event_bus.bus import EventBus
from pai.event_bus.events import EventType
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
        event_bus: EventBus,
        config: Optional[DeviceConfig] = None,
    ) -> None:
        super().__init__(session_factory=session_factory, event_bus=event_bus)
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
                        device_type=DeviceType(
                            r.device_type) if r.device_type else DeviceType.UNKNOWN,
                        status=_normalize_status(r.status),
                        app_version=r.app_version or "",
                        runtime_version=r.runtime_version or "",
                        platform=DevicePlatform(
                            r.platform) if r.platform else DevicePlatform.UNKNOWN,
                        platform_version=r.platform_version or "",
                        os_version=r.os_version or "",
                        architecture=DeviceArchitecture(
                            r.architecture) if r.architecture else DeviceArchitecture.UNKNOWN,
                        hostname=r.hostname or "",
                        capabilities=r.capabilities or [],
                        allow_remote_install=bool(r.allow_remote_install),
                        allow_remote_invoke=bool(r.allow_remote_invoke),
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

    async def mark_seen(self, device_id: str) -> None:
        """Persist last_seen without touching status or metadata."""
        await self._touch_device(device_id)

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
        registration: DeviceRegistration,
        user_id: str,
    ) -> DeviceInfo:
        """
        Register a device. Does NOT establish a network connection —
        the WebSocket endpoint does that later via `attach_connection`.
        """

        # ---------- A device id that belongs to someone else is never reused ----------
        # An app keeps its device id across logins. If a DIFFERENT account signs in on the same install
        # it sends the old id: reusing it would hand this user another user's device (and used to move
        # its ownership in the database while the registry kept the old owner, so every call then said
        # "Not your device"). Each user gets their own device record; the response carries the new id.
        if registration.device_id:
            other = await self.get(registration.device_id)
            if other is not None and getattr(other, "user_id", None) != user_id:
                logger.warning(
                    "Device id {} belongs to another user; registering a new device for user {}",
                    registration.device_id, user_id)
                registration = registration.model_copy(update={"device_id": None})

        # ---------- Reuse existing (in-memory) device ----------
        if registration.device_id:
            existing = await self.get(registration.device_id)

            if existing is not None:
                try:
                    await self.refresh_metadata(
                        registration.device_id, registration.device, user_id=user_id,
                    )
                except AttributeError:
                    logger.debug(
                        "device_manager.refresh_metadata not available; "
                        "skipping refresh"
                    )

                logger.info(
                    "Device re-registered: %s (%s)",
                    registration.device_id, existing.name,
                )
                return existing

            # ---------- Not in memory — restore into memory with the caller's id ----------
            logger.info(
                "Existing device id %s not in memory — restoring with caller's id",
                registration.device_id,
            )

            device = await self._register_new_device(registration, user_id=user_id)

            logger.info("Device restored: %s (%s)", device.id, device.name)

            return device

        # ---------- Mint a new device ----------
        device = await self._register_new_device(registration, user_id=user_id)

        logger.info("Device registered: %s (%s)", device.id, device.name)

        # Best-effort ownership attach (in case register() didn't persist it).
        await self.assign_user(device.id, user_id)

        return device


    async def _register_new_device(
            self,
            registration: DeviceRegistration,
            user_id: str,):

        connection_type = registration.connection_type
        connection_config = registration.connection_config
        tracer = get_tracer()
        async with tracer.span(
            "device_manager.register",
            user_id=user_id,
            connection_type=connection_type,
            connection_config=connection_config,
        ) as span:

            if isinstance(registration, DeviceRegistration):
                data = registration.device.model_dump()
                device = DeviceInfo(
                    id=str(uuid4()),
                    user_id=user_id,
                    **data,
                )
            else:
                device = registration
                if not device.id:
                    device.id = str(uuid4())
                device.user_id = user_id

            device.status = DeviceStatus.ONLINE

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

            await self._publish(EventType.DEVICE_REGISTERED, {
                "device_id": device.id,
                "user_id": user_id,
                "platform": str(device.platform),
                "architecture": device.architecture,
                "device_type": str(device.device_type),
                "status": device.status,
            })

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
                device_type=getattr(device, "device_type",
                                    "unknown") or "unknown",
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

    async def set_remote_access(
        self, device_id: str, *, install: Optional[bool] = None, invoke: Optional[bool] = None,
    ) -> Optional[DeviceInfo]:
        """Change what other devices may do to ``device_id``. The caller (the API route) has
        already checked that the user owns it. Persisted first, then mirrored in memory; the
        router cache is dropped so the change takes effect on the next request."""
        async def _do(session: AsyncSession):
            return await DeviceRepository(session).set_remote_access(
                device_id, install=install, invoke=invoke)

        record = await self._with_session(_do)
        if record is None:
            return None
        device = await self.registry.get(device_id)
        if device is not None:
            device.allow_remote_install = bool(record.allow_remote_install)
            device.allow_remote_invoke = bool(record.allow_remote_invoke)
        await self._publish(EventType.DEVICE_REMOTE_ACCESS_CHANGED, {
            "device_id": device_id, "user_id": record.user_id,
            "allow_remote_install": bool(record.allow_remote_install),
            "allow_remote_invoke": bool(record.allow_remote_invoke),
        })
        self._invalidate_router(record.user_id)
        # Tell the device itself right away, so its own enforcement never lags behind the owner.
        try:
            if await self.is_connected(device_id):
                await self.send(device_id, {
                    "type": "device.remote_access",
                    "remote_access": {"install": bool(record.allow_remote_install),
                                      "invoke": bool(record.allow_remote_invoke)},
                })
        except Exception as exc:
            logger.debug("remote-access push to {} failed: {}", device_id, exc)
        return device

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
            await self._publish(EventType.DEVICE_UNREGISTERED, {
                "device_id": device_id,
                "user_id": user_id,
            })
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

            await self._publish(EventType.DEVICE_STATUS, {
                "device_id": device_id,
                "status": "online",
                "user_id": getattr(device, "user_id", None),
            })
            await self._publish(EventType.DEVICE_CONNECTED, {
                "device_id": device_id,
                "user_id": getattr(device, "user_id", None),
                "device_type": getattr(device, "device_type", None),
            })

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
            device_type=DeviceType(
                row.device_type) if row.device_type else DeviceType.UNKNOWN,
            status=DeviceStatus(
                row.status) if row.status else DeviceStatus.UNKNOWN,
            platform=DevicePlatform(
                row.platform) if row.platform else DevicePlatform.UNKNOWN,
            platform_version=row.platform_version or "",
            os_version=row.os_version or "",
            architecture=DeviceArchitecture(
                row.architecture) if row.architecture else DeviceArchitecture.UNKNOWN,
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
                await self._publish(EventType.DEVICE_DISCONNECTED, {
                    "device_id": device_id,
                    "user_id": user_id,
                })
                await self._publish(EventType.DEVICE_STATUS, {
                    "device_id": device_id,
                    "status": "offline",
                    "user_id": getattr(device, "user_id", None),
                })
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
        self, device_id: str, payload: DeviceMessage,
    ) -> Dict[str, Any]:
        tracer = get_tracer()

        connection = self._connections.get(device_id)
        if connection is None or not connection.connected:
            raise RuntimeError(f"Device {device_id} is not connected")

        # Callers pass DeviceMessage models; the transport and tracing want plain JSON.
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")

        capability = payload.get("capability") or "unknown"
        plugin = payload.get("plugin") or payload.get("plugin_id") or "unknown"

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
        self,
        device_id: str,
        update: DeviceRegistrationInfo,
        user_id: str | None = None,
    ) -> None:
        tracer = get_tracer()

        async with tracer.span(
            "device_manager.refresh_metadata",
            device_id=device_id,
            updated_keys=list(update.model_fields_set),
        ):
            device = await self.registry.get(device_id)
            if device is None:
                return

            updates = update.model_dump(exclude_none=True)

            changed = {
                key: value
                for key, value in updates.items()
                if getattr(device, key, None) != value
            }

            if not changed and user_id is None:
                return

            for key, value in changed.items():
                if hasattr(device, key):
                    setattr(device, key, value)

            if self._session_factory is not None:

                async def _do(session):
                    repo = DeviceRepository(session)

                    kwargs = {
                        field: getattr(device, field)
                        for field in DeviceRegistrationInfo.model_fields
                    }

                    kwargs["device_id"] = device_id

                    # Metadata refresh never changes WHO owns the device (assign_user does that,
                    # deliberately): keep the owner the registry already has.
                    kwargs["user_id"] = device.user_id

                    await repo.upsert(**kwargs)

                await self._with_session(_do)

            self._emit(EventType.DEVICE_METADATA_REFRESHED, {
                "device_id": device_id,
                "user_id": user_id,
                "updated_keys": list(changed.keys()),
            })

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
            self._emit(EventType.DEVICE_OWNERSHIP_ASSIGNED, {
                "device_id": device_id,
                "user_id": user_id
            })
            self._invalidate_router(user_id)
