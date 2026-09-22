"""Device manager."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid4

from pai.devices.connection import DeviceConnection
from pai.devices.models import (
    DeviceFilter,
    DeviceInfo,
    DeviceRegistration,
    DeviceStatus,
)
from pai.devices.registry import DeviceRegistry
from pai.storage.repositories.device import DeviceRepository


class DeviceManager:
    """
    High-level device management facade.

    Responsibilities:
    - Register/unregister devices
    - Persist device metadata to the DB (when session_factory is set)
    - Track device metadata/state
    - Track live device connections
    - Find devices by capabilities
    - Send messages through active connections

    The registry (in-memory) is the runtime view; the DB is the
    durable view. On initialize() the DB is loaded into the registry.
    On register/status changes the DB is updated alongside the registry.
    """

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
    ) -> None:
        self.registry = registry or DeviceRegistry()
        self._session_factory = session_factory

        # Active transport connections.
        self._connections: Dict[str, DeviceConnection] = {}

        self._initialized = False

    # ------------------------------------------------------------------
    # Internal session helper
    # ------------------------------------------------------------------

    async def _with_session(self, fn):
        """
        Open a scoped session, run [fn], commit, close.

        If no session_factory is configured, this is a no-op that
        silently skips persistence — useful for tests that don't
        have a DB.
        """
        if self._session_factory is None:
            return None

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
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize device management and load persisted devices."""

        await self.registry.initialize()

        if self._session_factory is not None:
            try:
                await self._load_from_db()
            except Exception as exc:
                logger.warning("Failed to load devices from DB: {}", exc)

        self._initialized = True

        logger.info(
            "Device manager initialized ({} devices in registry)",
            len(await self.registry.all()),
        )

    async def _load_from_db(self) -> None:
        """Load every DeviceModel row into the in-memory registry."""

        async def _do(session: AsyncSession):
            repo = DeviceRepository(session)
            return await repo.list_all()

        records = await self._with_session(_do) or []

        for r in records:
            info = DeviceInfo(
                id=r.id,
                name=r.name or "",
                device_type=r.device_type or "unknown",
                status=r.status or "offline",
                app_version=r.app_version or "",
                runtime_version=r.runtime_version or "",
                platform=r.platform or "unknown",
                platform_version=r.platform_version or "",
                os_version=r.os_version or "",
                architecture=r.architecture or "",
                hostname=r.hostname or "",
                capabilities=r.capabilities or [],
            )
            try:
                await self.registry.register(info)
            except Exception as exc:
                logger.warning(
                    "Failed to restore device {} from DB: {}",
                    r.id, exc,
                )

        logger.info("Loaded {} devices from DB", len(records))

    async def get_status(self, user_id: str) -> Dict[str, Any]:
        """Get the status of the device manager."""

        try:
            registered = await self.registry.all(user_id)
        except TypeError:
            # Some registries ignore user_id.
            registered = await self.registry.all()

        return {
            "initialized": self._initialized,
            "registered_devices": len(registered),
            "connected_devices": len(self._connections),
        }

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(
        self,
        registration: DeviceRegistration | DeviceInfo,
        *,
        connection_type: Optional[str] = None,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> DeviceInfo:
        """
        Register a device.

        Registration does NOT establish a network connection.

        The actual WebSocket connection is established later by:

            WS /devices/{device_id}/ws
        """

        if isinstance(registration, DeviceRegistration):

            data = registration.device.model_dump()

            device = DeviceInfo(
                id=str(uuid4()),
                **data,
            )

            connection_type = (
                registration.connection_type or connection_type
            )
            connection_config = (
                registration.connection_config or connection_config
            )

        else:
            device = registration

            if not device.id:
                device.id = str(uuid4())

        # 1. Persist to DB first so that if the process crashes we still
        #    have a record. If persistence fails we don't block registration.
        try:
            await self._persist_upsert(device)
        except Exception as exc:
            logger.warning(
                "Failed to persist device {} to DB: {}",
                device.id, exc,
            )

        # 2. Mirror in memory.
        await self.registry.register(device)

        if connection_type:
            logger.debug(
                "Device {} requested connection type '{}'",
                device.id,
                connection_type,
            )

        return device

    async def _persist_upsert(self, device: DeviceInfo) -> None:
        async def _do(session: AsyncSession):
            repo = DeviceRepository(session)
            await repo.upsert(
                device_id=device.id,
                name=getattr(device, "name", "") or "",
                device_type=getattr(device, "device_type", "unknown") or "unknown",
                platform=getattr(device, "platform", "unknown") or "unknown",
                platform_version=getattr(device, "platform_version", "") or "",
                os_version=getattr(device, "os_version", "") or "",
                architecture=getattr(device, "architecture", "") or "",
                hostname=getattr(device, "hostname", "") or "",
                app_version=getattr(device, "app_version", "") or "",
                runtime_version=getattr(device, "runtime_version", "") or "",
                status=getattr(device, "status", "offline") or "offline",
                capabilities=getattr(device, "capabilities", []) or [],
            )

        await self._with_session(_do)

    async def unregister(
        self,
        device_id: str,
    ) -> bool:
        """Disconnect and unregister a device."""

        await self.disconnect(device_id)

        # Remove from DB first.
        try:
            async def _do(session: AsyncSession):
                repo = DeviceRepository(session)
                await repo.delete(device_id)

            await self._with_session(_do)
        except Exception as exc:
            logger.warning(
                "Failed to delete device {} from DB: {}",
                device_id, exc,
            )

        return await self.registry.unregister(device_id)

    # ------------------------------------------------------------------
    # Device lookup
    # ------------------------------------------------------------------

    async def get(self, device_id: str) -> Optional[DeviceInfo]:
        return await self.registry.get(device_id)

    async def require(self, device_id: str) -> DeviceInfo:
        return await self.registry.require(device_id)

    async def list_devices(self, user_id: str) -> List[DeviceInfo]:
        return await self.registry.all(user_id)

    async def find(self, device_filter: DeviceFilter) -> List[DeviceInfo]:
        return await self.registry.find(device_filter)

    # ------------------------------------------------------------------
    # Device state
    # ------------------------------------------------------------------

    async def update_status(
        self,
        device_id: str,
        status: DeviceStatus,
    ) -> bool:
        try:
            await self.registry.update_status(device_id, status)

            # Persist alongside the in-memory update.
            try:
                async def _do(session: AsyncSession):
                    repo = DeviceRepository(session)
                    await repo.update_status(device_id, str(status))

                await self._with_session(_do)
            except Exception as exc:
                logger.warning(
                    "Failed to persist status for device {}: {}",
                    device_id, exc,
                )

            return True

        except Exception as exc:
            logger.error(
                "Failed to update status for device {}: {}",
                device_id, exc,
            )
            return False

    async def _touch_device(self, device_id: str) -> None:
        """Update last_seen in the DB. Best-effort; never raises."""
        if self._session_factory is None:
            return
        try:
            async def _do(session: AsyncSession):
                repo = DeviceRepository(session)
                await repo.touch(device_id)

            await self._with_session(_do)
        except Exception as exc:
            logger.debug(
                "Failed to touch device {}: {}", device_id, exc,
            )

    # ------------------------------------------------------------------
    # Live connection management
    # ------------------------------------------------------------------

    async def attach_connection(
        self,
        device_id: str,
        connection: DeviceConnection,
    ) -> None:
        """
        Attach an already-established connection to a device.

        The WebSocket endpoint should call this after accepting
        the WebSocket connection.
        """

        await self.require(device_id)

        # Close an existing connection if one exists.
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

        await self.registry.update_status(device_id, DeviceStatus.ONLINE)
        await self.registry.heartbeat(device_id)

        # Persist ONLINE status.
        try:
            async def _do(session: AsyncSession):
                repo = DeviceRepository(session)
                await repo.update_status(device_id, str(DeviceStatus.ONLINE))

            await self._with_session(_do)
        except Exception as exc:
            logger.warning(
                "Failed to persist ONLINE status for {}: {}",
                device_id, exc,
            )

        logger.info("Connection attached to device {}", device_id)

    async def detach_connection(self, device_id: str) -> None:
        """Remove the active connection from a device."""

        connection = self._connections.pop(device_id, None)

        if connection is not None:
            try:
                await connection.disconnect()
            except Exception as exc:
                logger.warning(
                    "Failed to disconnect {}: {}", device_id, exc,
                )

        try:
            await self.registry.update_status(device_id, DeviceStatus.OFFLINE)
        except Exception as exc:
            logger.warning(
                "Failed to update offline status for {}: {}",
                device_id, exc,
            )

        # Persist OFFLINE status.
        try:
            async def _do(session: AsyncSession):
                repo = DeviceRepository(session)
                await repo.update_status(device_id, str(DeviceStatus.OFFLINE))

            await self._with_session(_do)
        except Exception as exc:
            logger.warning(
                "Failed to persist OFFLINE status for {}: {}",
                device_id, exc,
            )

        logger.info("Connection detached from device {}", device_id)

    async def connect(self, device_id: str) -> None:
        """
        Connect an already-configured DeviceConnection.

        Retained for local connection implementations. WebSocket
        devices should connect through the WebSocket endpoint and
        attach_connection() instead.
        """

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
        """Disconnect a device."""

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
        """Return whether a device has an active connection."""

        connection = self._connections.get(device_id)

        return connection is not None and connection.connected

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(
        self,
        device_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a message to a connected device."""

        connection = self._connections.get(device_id)

        if connection is None:
            raise RuntimeError(f"Device {device_id} is not connected")

        if not connection.connected:
            raise RuntimeError(f"Device {device_id} is not connected")

        result = await connection.send(payload)

        # Update last_seen in memory, then in DB (best-effort).
        await self.registry.heartbeat(device_id)
        await self._touch_device(device_id)

        return result

    async def ping(self, device_id: str) -> bool:
        """Ping a device."""

        connection = self._connections.get(device_id)

        if connection is None:
            return False

        return await connection.ping()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Shutdown all device connections."""

        for device_id in list(self._connections.keys()):
            try:
                await self.disconnect(device_id)
            except Exception as exc:
                logger.warning(
                    "Failed to disconnect {}: {}", device_id, exc,
                )

        self._connections.clear()

        await self.registry.shutdown()

        self._initialized = False

        logger.info("Device manager stopped")

    async def refresh_metadata(
        self, device_id: str, update: dict, user_id: str | None = None,
    ) -> None:
        """Update an existing device's metadata in memory and in the DB."""
        device = await self.registry.get(device_id)
        if device is None:
            return

        for k, v in update.items():
            if hasattr(device, k) and v is not None:
                setattr(device, k, v)

        if self._session_factory is not None:
            async def _do(session):
                from pai.storage.repositories.device import DeviceRepository
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


    async def assign_user(self, device_id: str, user_id: str) -> None:
        """Attach the owning user to a device row."""
        if self._session_factory is None:
            return

        async def _do(session):
            from pai.storage.repositories.device import DeviceRepository
            repo = DeviceRepository(session)
            record = await repo.get(device_id)
            if record is not None:
                record.user_id = user_id
                await session.flush()
        await self._with_session(_do)