"""Device domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceType(str, Enum):
    """Supported device categories."""

    DESKTOP = "desktop"
    LAPTOP = "laptop"
    MOBILE = "mobile"
    TABLET = "tablet"
    SERVER = "server"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class DeviceStatus(str, Enum):
    """Current device availability."""

    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ERROR = "error"
    UNKNOWN = "unknown"


class DeviceCapability(BaseModel):
    """
    Capability exposed by a device.

    Examples:
        browser
        filesystem.read
        filesystem.write
        notification
        camera
        microphone
        shell
    """

    name: str
    description: Optional[str] = None

    # Optional metadata describing the capability.
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # Whether this capability requires explicit authorization.
    requires_authorization: bool = False

    # Optional risk classification used by the security layer.
    risk: Optional[str] = None


class DeviceInfo(BaseModel):
    """Static and dynamic information about a device."""

    id: str
    name: str

    device_type: DeviceType = DeviceType.UNKNOWN
    status: DeviceStatus = DeviceStatus.UNKNOWN

    # Application information.
    app_version: str
    runtime_version: str

    # Device platform information.
    platform: Optional[str] = None
    platform_version: Optional[str] = None
    os_version: str
    architecture: Optional[str] = None

    # Network information.
    hostname: Optional[str] = None
    address: Optional[str] = None
    port: Optional[int] = None

    # Device capabilities.
    capabilities: List[DeviceCapability] = Field(default_factory=list)

    # Arbitrary device metadata.
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Timestamps.
    registered_at: datetime = Field(default_factory=utc_now)
    last_seen: Optional[datetime] = None

    # Executor identifier, if the device is associated with one.
    executor_id: Optional[str] = None

    def has_capability(self, capability: str) -> bool:
        """Return whether the device exposes a capability."""
        return any(
            item.name == capability
            for item in self.capabilities
        )

    def capability_names(self) -> List[str]:
        """Return capability names."""
        return [item.name for item in self.capabilities]

    def generate_id(self) -> str:
        """Generate a unique device identifier based on its properties."""
        return f"{self.device_type}:{self.platform}:{self.architecture}:{self.name}:{uuid.uuid4()}"  # Replace 'uuid' with actual unique identifier logic if needed.

class DeviceRegistrationInfo(BaseModel):
    name: str
    device_type: str = "unknown"

    app_version: str = "1.0.0"
    runtime_version: str = "1.0.0"

    platform: Optional[str] = None
    platform_version: Optional[str] = None
    os_version: Optional[str] = None

    architecture: Optional[str] = None
    hostname: Optional[str] = None

    capabilities: List[Dict[str, Any]] = Field(
        default_factory=list
    )

    metadata: Dict[str, Any] = Field(
        default_factory=dict
    )


class DeviceRegistration(BaseModel):
    device: DeviceRegistrationInfo

    connection_type: Optional[str] = None

    connection_config: Dict[str, Any] = Field(
        default_factory=dict
    )

class DeviceHeartbeat(BaseModel):
    """Heartbeat sent by a device/executor."""

    device_id: str
    timestamp: datetime = Field(default_factory=utc_now)

    status: DeviceStatus = DeviceStatus.ONLINE

    # Optional runtime information.
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeviceFilter(BaseModel):
    """Criteria used to select devices."""

    device_type: Optional[DeviceType] = None
    status: Optional[DeviceStatus] = None

    capability: Optional[str] = None
    platform: Optional[str] = None

    tags: List[str] = Field(default_factory=list)

    metadata: Dict[str, Any] = Field(default_factory=dict)