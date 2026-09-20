"""Device manager."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from pai.devices.connection import (
    DeviceConnection,
    DeviceConnectionFactory,
)
from pai.devices.models import (
    DeviceFilter,
    DeviceInfo,
    DeviceRegistration,
    DeviceStatus,
)
from pai.devices.registry import DeviceRegistry


class DeviceManager:
    """
    High-level device management facade.

    Responsibilities:
    - Register devices
    - Track device state
    - Manage device connections
    - Find devices by capabilities
    - Send low-level messages to devices

    Task planning and execution belong to orchestration/executors.
    """

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
    ) -> None:
        self.registry = registry or DeviceRegistry()

        self._connections: Dict[
            str,
            DeviceConnection,
        ] = {}

        self._initialized = False

    async def initialize(self) -> None:
        """Initialize device management."""
        await self.registry.initialize()

        self._initialized = True

        logger.info("Device manager initialized")

    async def register(
        self,
        registration: DeviceRegistration | DeviceInfo,
        *,
        connection_type: Optional[str] = None,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> DeviceInfo:
        """
        Register a device and optionally create its connection.
        """

        if isinstance(
            registration,
            DeviceRegistration,
        ):
            device = registration.device
            connection_type = (
                registration.connection_type
                or connection_type
            )
            connection_config = (
                registration.connection_config
                or connection_config
            )
        else:
            device = registration

        await self.registry.register(device)

        if connection_type:
            connection = DeviceConnectionFactory.create(
                connection_type=connection_type,
                device_id=device.id,
                config=connection_config,
            )

            self._connections[device.id] = connection

        return device

    async def unregister(
        self,
        device_id: str,
    ) -> bool:
        """Disconnect and unregister a device."""

        connection = self._connections.pop(
            device_id,
            None,
        )

        if connection is not None:
            try:
                await connection.disconnect()
            except Exception as exc:
                logger.warning(
                    "Failed to disconnect {}: {}",
                    device_id,
                    exc,
                )

        return await self.registry.unregister(
            device_id
        )

    async def get(
        self,
        device_id: str,
    ) -> Optional[DeviceInfo]:
        return await self.registry.get(device_id)

    async def require(
        self,
        device_id: str,
    ) -> DeviceInfo:
        return await self.registry.require(device_id)

    async def list_devices(self) -> List[DeviceInfo]:
        return await self.registry.all()

    async def find(
        self,
        device_filter: DeviceFilter,
    ) -> List[DeviceInfo]:
        return await self.registry.find(
            device_filter
        )

    async def connect(
        self,
        device_id: str,
    ) -> None:
        """Connect a registered device."""
        device = await self.require(device_id)

        connection = self._connections.get(device_id)

        if connection is None:
            raise RuntimeError(
                f"No connection configured for device "
                f"{device_id}"
            )

        await self.registry.update_status(
            device_id,
            DeviceStatus.CONNECTING,
        )

        try:
            await connection.connect()

            await self.registry.update_status(
                device_id,
                DeviceStatus.ONLINE,
            )

            await self.registry.heartbeat(
                device_id
            )

        except Exception:
            await self.registry.update_status(
                device_id,
                DeviceStatus.ERROR,
            )
            raise

    async def disconnect(
        self,
        device_id: str,
    ) -> None:
        """Disconnect a device."""
        device = await self.require(device_id)

        connection = self._connections.get(device.id)

        if connection is None:
            return

        await connection.disconnect()

        await self.registry.update_status(
            device_id,
            DeviceStatus.OFFLINE,
        )

    async def is_connected(
        self,
        device_id: str,
    ) -> bool:
        connection = self._connections.get(
            device_id
        )

        return (
            connection is not None
            and connection.connected
        )

    async def send(
        self,
        device_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a message to a connected device."""
        connection = self._connections.get(
            device_id
        )

        if connection is None:
            raise RuntimeError(
                f"No connection configured for device "
                f"{device_id}"
            )

        if not connection.connected:
            raise RuntimeError(
                f"Device {device_id} is not connected"
            )

        result = await connection.send(
            payload
        )

        await self.registry.heartbeat(
            device_id
        )

        return result

    async def ping(
        self,
        device_id: str,
    ) -> bool:
        """Ping a device."""
        connection = self._connections.get(
            device_id
        )

        if connection is None:
            return False

        return await connection.ping()

    async def shutdown(self) -> None:
        """Shutdown all device connections."""
        for device_id, connection in list(
            self._connections.items()
        ):
            try:
                await connection.disconnect()
            except Exception as exc:
                logger.warning(
                    "Failed to disconnect {}: {}",
                    device_id,
                    exc,
                )

        self._connections.clear()

        await self.registry.shutdown()

        self._initialized = False

        logger.info("Device manager stopped")