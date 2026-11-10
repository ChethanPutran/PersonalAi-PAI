"""In-memory device registry.

The registry holds DeviceInfo objects keyed by device id. Filtering by
`user_id` is a simple attribute check — no separate index, no metadata
lookup. See the earlier `all(user_id)` bug where filtering on
`device.metadata["user_id"]` (which is never populated) returned 0 for
every user.

Tracing: registry operations are wrapped by `DeviceManager` spans
(`device_manager.list_devices`, `device_manager.register`, etc.).
Adding per-call spans here would just add nesting without new signal.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from loguru import logger

from pai.devices.models import (
    DeviceFilter,
    DeviceInfo,
    DeviceStatus,
)
from pai.devices.utils import _normalize_status, _utc_now



class DeviceRegistry:
    """In-memory registry of known devices."""

    def __init__(self) -> None:
        self._devices: Dict[str, DeviceInfo] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        logger.info("Device registry initialized")

    async def shutdown(self) -> None:
        await self.clear()
        logger.info("Device registry stopped")

    # ------------------------------------------------------------------
    # Register / unregister
    # ------------------------------------------------------------------

    async def register(self, device: DeviceInfo) -> DeviceInfo:
        """Register or update a device. Backend owns ID generation."""
        async with self._lock:
            existing = self._devices.get(device.id) if device.id else None
            if existing is not None:
                logger.info(
                    "Updating device: {} ({})", existing.id, device.name,
                )
                # Preserve the original registration timestamp
                try:
                    device.registered_at = existing.registered_at
                except AttributeError:
                    # DeviceInfo may not have this field — skip silently
                    pass
                self._devices[device.id] = device
                return device

            logger.info(
                "Registering new device: {} ({})", device.id, device.name,
            )
            self._devices[device.id] = device
            return device

    async def unregister(self, device_id: str) -> bool:
        async with self._lock:
            existed = device_id in self._devices
            if existed:
                del self._devices[device_id]

        if existed:
            logger.info("Device unregistered: {}", device_id)
        return existed

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def get(self, device_id: str) -> Optional[DeviceInfo]:
        async with self._lock:
            return self._devices.get(device_id)

    async def require(self, device_id: str) -> DeviceInfo:
        device = await self.get(device_id)
        if device is None:
            raise KeyError(f"Device not found: {device_id}")
        return device

    async def all(self, user_id: Optional[str] = None) -> List[DeviceInfo]:
        """
        Return devices, optionally filtered by owner.

        Filtering is a direct attribute comparison against
        `device.user_id`. This is what the previous version got wrong —
        it looked in `device.metadata["user_id"]`, a dict that never
        contains that key, so the filter returned 0 devices for every
        user even when the registry held the device.

        - `user_id=None` → all devices (admin views)
        - `user_id="X"`  → devices owned by user X
        """
        async with self._lock:
            devices = list(self._devices.values())

        if user_id is None:
            return devices

        return [d for d in devices if getattr(d, "user_id", None) == user_id]

    async def find(self, device_filter: DeviceFilter) -> List[DeviceInfo]:
        """Find devices matching a DeviceFilter."""
        devices = await self.all()
        result: List[DeviceInfo] = []

        for device in devices:
            if (
                device_filter.device_type is not None
                and device.device_type != device_filter.device_type
            ):
                continue
            if (
                device_filter.status is not None
                and _normalize_status(device.status)
                   != _normalize_status(device_filter.status)
            ):
                continue
            if (
                device_filter.platform is not None
                and device.platform != device_filter.platform
            ):
                continue
            if (
                device_filter.capability is not None
                and not device.has_capability(device_filter.capability)
            ):
                continue
            if device_filter.tags:
                device_tags = set(device.metadata.get("tags", []))
                if not set(device_filter.tags).issubset(device_tags):
                    continue
            if not all(
                device.metadata.get(k) == v
                for k, v in device_filter.metadata.items()
            ):
                continue
            result.append(device)

        return result

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    async def update_status(self, device_id: str, status) -> DeviceInfo:
        async with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                raise KeyError(f"Device not found: {device_id}")
            device.status = _normalize_status(status)
            self._devices[device_id] = device
            return device

    async def heartbeat(self, device_id: str) -> DeviceInfo:
        async with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                raise KeyError(f"Device not found: {device_id}")
            device.status = DeviceStatus.ONLINE
            device.last_seen = _utc_now()
            self._devices[device_id] = device
            return device

    async def clear(self) -> None:
        async with self._lock:
            self._devices.clear()