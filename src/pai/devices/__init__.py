"""PAI device management."""

from pai.devices.capabilities import (
    CapabilityRegistry,
    device_has_all_capabilities,
    device_has_any_capability,
    device_has_capability,
    missing_capabilities,
)
from pai.devices.connection import (
    ConnectionState,
    DeviceConnection,
    DeviceConnectionFactory,
    LocalDeviceConnection,
)
from pai.devices.manager import DeviceManager
from pai.devices.models import (
    DeviceCapability,
    DeviceFilter,
    DeviceHeartbeat,
    DeviceInfo,
    DeviceRegistration,
    DeviceStatus,
    DeviceType,
)
from pai.devices.registry import DeviceRegistry

__all__ = [
    "CapabilityRegistry",
    "ConnectionState",
    "DeviceCapability",
    "DeviceConnection",
    "DeviceConnectionFactory",
    "DeviceFilter",
    "DeviceHeartbeat",
    "DeviceInfo",
    "DeviceManager",
    "DeviceRegistration",
    "DeviceRegistry",
    "DeviceStatus",
    "DeviceType",
    "LocalDeviceConnection",
    "device_has_all_capabilities",
    "device_has_any_capabilities",
    "device_has_capability",
    "missing_capabilities",
]