"""Device capability definitions and matching utilities."""

from __future__ import annotations

from typing import Iterable, List, Sequence

from pai.devices.models import DeviceCapability, DeviceInfo


class CapabilityRegistry:
    """
    Registry of known device capabilities.

    This is intentionally lightweight. It does not execute capabilities.
    Execution belongs to executors.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, DeviceCapability] = {}

    def register(
        self,
        capability: DeviceCapability,
    ) -> None:
        self._capabilities[capability.name] = capability

    def register_many(
        self,
        capabilities: Iterable[DeviceCapability],
    ) -> None:
        for capability in capabilities:
            self.register(capability)

    def get(
        self,
        name: str,
    ) -> DeviceCapability | None:
        return self._capabilities.get(name)

    def has(self, name: str) -> bool:
        return name in self._capabilities

    def all(self) -> List[DeviceCapability]:
        return list(self._capabilities.values())


def normalize_capability(name: str) -> str:
    """
    Normalize capability names.

    Example:
        " FileSystem.Read " -> "filesystem.read"
    """
    return name.strip().lower()


def device_has_capability(
    device: DeviceInfo,
    capability: str,
) -> bool:
    """Check whether a device exposes a capability."""
    capability = normalize_capability(capability)

    return any(
        normalize_capability(item.name) == capability
        for item in device.capabilities
    )


def device_has_all_capabilities(
    device: DeviceInfo,
    capabilities: Sequence[str],
) -> bool:
    """Check whether a device exposes every requested capability."""
    return all(
        device_has_capability(device, capability)
        for capability in capabilities
    )


def device_has_any_capability(
    device: DeviceInfo,
    capabilities: Sequence[str],
) -> bool:
    """Check whether a device exposes at least one capability."""
    return any(
        device_has_capability(device, capability)
        for capability in capabilities
    )


def missing_capabilities(
    device: DeviceInfo,
    required: Sequence[str],
) -> List[str]:
    """Return capabilities required but not available on the device."""
    return [
        capability
        for capability in required
        if not device_has_capability(device, capability)
    ]