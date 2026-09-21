"""Device registry."""

from __future__ import annotations

import asyncio
from typing import Dict, Iterable, List, Optional

from loguru import logger

from pai.devices.models import (
    DeviceFilter,
    DeviceInfo,
    DeviceStatus,
)


class DeviceRegistry:
    """
    In-memory registry of known devices.

    Persistence/discovery can be layered on top later.
    """

    def __init__(self) -> None:
        self._devices: Dict[str, DeviceInfo] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize registry."""
        logger.info("Device registry initialized")

    async def register(
        self,
        device: DeviceInfo,
    ) -> DeviceInfo:
        """
        Register or update a device.

        Backend owns device ID generation.
        """

        async with self._lock:

            # --------------------------------------------------
            # Existing device
            # --------------------------------------------------

            if device.id:
                existing = self._devices.get(device.id)

                if existing is not None:
                    logger.info(
                        "Updating device: {} ({})",
                        existing.id,
                        device.name,
                    )

                    device.registered_at = (
                        existing.registered_at
                    )

                    self._devices[device.id] = device

                    return device

            # --------------------------------------------------
            # New device
            # --------------------------------------------------

            logger.info(
                "Registering new device: {} ({})",
                device.id,
                device.name,
            )

            self._devices[device.id] = device

            return device

    async def unregister(
        self,
        device_id: str,
    ) -> bool:
        """Remove a device."""
        async with self._lock:
            existed = device_id in self._devices

            if existed:
                del self._devices[device_id]

        if existed:
            logger.info(
                "Device unregistered: {}",
                device_id,
            )

        return existed

    async def get(
        self,
        device_id: str,
    ) -> Optional[DeviceInfo]:
        """Get a device by ID."""
        async with self._lock:
            return self._devices.get(device_id)

    async def require(
        self,
        device_id: str,
    ) -> DeviceInfo:
        """Get a device or raise."""
        device = await self.get(device_id)

        if device is None:
            raise KeyError(
                f"Device not found: {device_id}"
            )

        return device

    async def all(self,user_id: Optional[str] = None) -> List[DeviceInfo]:
        """Return all registered devices."""
        async with self._lock:
            if user_id:
                return [device for device in self._devices.values() if device.metadata.get("user_id") == user_id]
            return list(self._devices.values())


    async def update_status(
        self,
        device_id: str,
        status: DeviceStatus,
    ) -> DeviceInfo:
        """Update device status."""
        async with self._lock:
            device = self._devices.get(device_id)

            if device is None:
                raise KeyError(
                    f"Device not found: {device_id}"
                )

            device.status = status

            self._devices[device_id] = device

            return device

    async def heartbeat(
        self,
        device_id: str,
    ) -> DeviceInfo:
        """Mark a device as alive."""
        from datetime import datetime, timezone

        async with self._lock:
            device = self._devices.get(device_id)

            if device is None:
                raise KeyError(
                    f"Device not found: {device_id}"
                )

            device.status = DeviceStatus.ONLINE
            device.last_seen = datetime.now(timezone.utc)

            self._devices[device_id] = device

            return device

    async def find(
        self,
        device_filter: DeviceFilter,
    ) -> List[DeviceInfo]:
        """Find devices matching a filter."""
        devices = await self.all()

        result: List[DeviceInfo] = []

        for device in devices:
            if (
                device_filter.device_type is not None
                and device.device_type
                != device_filter.device_type
            ):
                continue

            if (
                device_filter.status is not None
                and device.status
                != device_filter.status
            ):
                continue

            if (
                device_filter.platform is not None
                and device.platform
                != device_filter.platform
            ):
                continue

            if (
                device_filter.capability is not None
                and not device.has_capability(
                    device_filter.capability
                )
            ):
                continue

            if device_filter.tags:
                device_tags = set(
                    device.metadata.get("tags", [])
                )

                if not set(device_filter.tags).issubset(
                    device_tags
                ):
                    continue

            matches_metadata = all(
                device.metadata.get(key) == value
                for key, value
                in device_filter.metadata.items()
            )

            if not matches_metadata:
                continue

            result.append(device)

        return result

    async def clear(self) -> None:
        """Remove all devices."""
        async with self._lock:
            self._devices.clear()

    async def shutdown(self) -> None:
        """Shutdown registry."""
        await self.clear()
        logger.info("Device registry stopped")