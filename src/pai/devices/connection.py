"""Device connection abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional

from loguru import logger


class ConnectionState(str, Enum):
    """Connection lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class DeviceConnection(ABC):
    """
    Abstract connection to a device.

    The connection layer only manages communication.
    It does not decide what task should be executed.
    """

    def __init__(
        self,
        device_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.device_id = device_id
        self.config = config or {}

        self._state = ConnectionState.DISCONNECTED

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection."""
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        raise NotImplementedError

    @abstractmethod
    async def send(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send a message to the device.

        The payload is intentionally generic. Executors can build
        higher-level execution protocols on top of it.
        """
        raise NotImplementedError

    async def ping(self) -> bool:
        """Check whether the connection is alive."""
        try:
            await self.send({"type": "ping"})
            return True
        except Exception as exc:
            logger.debug(
                "Device ping failed for {}: {}",
                self.device_id,
                exc,
            )
            return False

    async def __aenter__(self) -> "DeviceConnection":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        await self.disconnect()


class LocalDeviceConnection(DeviceConnection):
    """
    Connection for the local process.

    Useful for desktop/server devices where the PAI process itself
    represents the device.
    """

    async def connect(self) -> None:
        self._state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    async def send(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError(
                f"Device {self.device_id} is not connected"
            )

        if payload.get("type") == "ping":
            return {
                "ok": True,
                "device_id": self.device_id,
            }

        return {
            "ok": True,
            "device_id": self.device_id,
            "payload": payload,
        }


class DeviceConnectionFactory:
    """Create connections from a connection type."""

    _registry: Dict[str, type[DeviceConnection]] = {
        "local": LocalDeviceConnection,
    }

    @classmethod
    def register(
        cls,
        connection_type: str,
        connection_class: type[DeviceConnection],
    ) -> None:
        cls._registry[connection_type] = connection_class

    @classmethod
    def create(
        cls,
        connection_type: str,
        device_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> DeviceConnection:
        connection_class = cls._registry.get(connection_type)

        if connection_class is None:
            raise ValueError(
                f"Unknown connection type: {connection_type}"
            )

        return connection_class(
            device_id=device_id,
            config=config,
        )