"""Device manager."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from uuid_utils import uuid4

from pai.devices.connection import DeviceConnection
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
    - Register/unregister devices
    - Track device metadata/state
    - Track live device connections
    - Find devices by capabilities
    - Send messages through active connections

    WebSocket transport is managed by the WebSocket connection layer.
    """

    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
    ) -> None:
        self.registry = registry or DeviceRegistry()

        # Active transport connections.
        #
        # For now this can contain a WebSocket-backed
        # DeviceConnection implementation.
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

    async def get_status(self, user_id: str) -> Dict[str, Any]:
        """Get the status of the device manager."""

        return {
            "initialized": self._initialized,
            "registered_devices": len(
                await self.registry.all(user_id)
            ),
            "connected_devices": len(
                self._connections
            ),
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
                registration.connection_type
                or connection_type
            )

            connection_config = (
                registration.connection_config
                or connection_config
            )

        else:
            device = registration

            if not device.id:
                device.id = str(uuid4())

        await self.registry.register(device)
        if connection_type:
            logger.debug(
                "Device {} requested connection type '{}'",
                device.id,
                connection_type,
            )

        return device

    async def unregister(
        self,
        device_id: str,
    ) -> bool:
        """Disconnect and unregister a device."""

        await self.disconnect(device_id)

        return await self.registry.unregister(
            device_id
        )

    # ------------------------------------------------------------------
    # Device lookup
    # ------------------------------------------------------------------

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

    async def list_devices(
        self,
        user_id: str,
    ) -> List[DeviceInfo]:
        return await self.registry.all(user_id)

    async def find(
        self,
        device_filter: DeviceFilter,
    ) -> List[DeviceInfo]:
        return await self.registry.find(
            device_filter
        )

    # ------------------------------------------------------------------
    # Device state
    # ------------------------------------------------------------------

    async def update_status(
        self,
        device_id: str,
        status: DeviceStatus,
    ) -> bool:
        try:
            await self.registry.update_status(
                device_id,
                status,
            )

            return True

        except Exception as exc:
            logger.error(
                "Failed to update status for device {}: {}",
                device_id,
                exc,
            )

            return False

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
                    device_id,
                    exc,
                )

        self._connections[device_id] = connection

        await self.registry.update_status(
            device_id,
            DeviceStatus.ONLINE,
        )

        await self.registry.heartbeat(
            device_id
        )

        logger.info(
            "Connection attached to device {}",
            device_id,
        )

    async def detach_connection(
        self,
        device_id: str,
    ) -> None:
        """
        Remove the active connection from a device.
        """

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

        try:
            await self.registry.update_status(
                device_id,
                DeviceStatus.OFFLINE,
            )
        except Exception as exc:
            logger.warning(
                "Failed to update offline status for {}: {}",
                device_id,
                exc,
            )

        logger.info(
            "Connection detached from device {}",
            device_id,
        )

    async def connect(
        self,
        device_id: str,
    ) -> None:
        """
        Connect an already-configured DeviceConnection.

        This method is retained for compatibility with local
        connection implementations.

        WebSocket devices should normally connect through the
        WebSocket endpoint and attach_connection().
        """

        device = await self.require(device_id)

        connection = self._connections.get(
            device_id
        )

        if connection is None:
            raise RuntimeError(
                f"No connection configured for device {device_id}"
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

        connection = self._connections.get(
            device_id
        )

        if connection is None:
            await self.registry.update_status(
                device_id,
                DeviceStatus.OFFLINE,
            )
            return

        try:
            await connection.disconnect()

        finally:
            self._connections.pop(
                device_id,
                None,
            )

            await self.registry.update_status(
                device_id,
                DeviceStatus.OFFLINE,
            )

    async def is_connected(
        self,
        device_id: str,
    ) -> bool:
        """Return whether a device has an active connection."""

        connection = self._connections.get(
            device_id
        )

        return (
            connection is not None
            and connection.connected
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

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
                f"Device {device_id} is not connected"
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

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Shutdown all device connections."""

        for device_id in list(
            self._connections.keys()
        ):
            try:
                await self.disconnect(
                    device_id
                )
            except Exception as exc:
                logger.warning(
                    "Failed to disconnect {}: {}",
                    device_id,
                    exc,
                )

        self._connections.clear()

        await self.registry.shutdown()

        self._initialized = False

        logger.info(
            "Device manager stopped"
        )