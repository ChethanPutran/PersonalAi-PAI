"""Device domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field
from pai.common.enums.device import DeviceArchitecture, DeviceControlOp, DeviceMessageType, DeviceOpStatus, DevicePlatform, DeviceType
from pai.utils.helpers import get_date


class DeviceMessage(BaseModel):
    """
    Represents a request to execute a capability on a device.
    """
    type: DeviceMessageType # The type of the device message, indicating the action to be performed (e.g., "invoke", "pause", "resume").
    operation: DeviceControlOp = DeviceControlOp.NONE # The specific control operation to be performed on the device (e.g., "pause", "resume", "cancel").
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4())) # A unique identifier for the request, used to correlate responses.
    task_id: Optional[str] = None # The unique identifier of the task associated with this request.
    status: DeviceOpStatus = DeviceOpStatus.NONE # The status of the request, indicating whether it was successful or failed.
    sucess: Optional[bool] = None # An optional boolean indicating the success of the request (True for success, False for failure).
    result: Optional[dict] = None # An optional dictionary containing the result of the execution, if applicable.
    error: Optional[str] = None # An optional error message if the execution failed.
    capability: Optional[str] = None # The capability to be executed.
    parameters: Dict[str, Any] = Field(default_factory=dict) # A dictionary of parameters required for executing the capability.
    timestamp: Optional[float] = None # An optional timestamp indicating when the request was created or processed.
    idempotency_key: Optional[str] = None # Stable across retries/recovery: a device that already ran this key must not run it again.
    attempt: int = 0 # 0 for the first send; increments on each retry/recovery re-send.


class DeviceFrame(BaseModel):
    """A frame RECEIVED from a device (parsed once at the WebSocket boundary).

    Typed so handlers never poke at raw dict keys. Unknown extras are kept.
    """
    model_config = ConfigDict(extra="allow")

    type: str
    request_id: Optional[str] = None
    task_id: Optional[str] = None
    result: Optional[Any] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    status: Optional[str] = None            # control_ack: DeviceOpStatus value
    operation: Optional[str] = None         # control_ack: DeviceControlOp value
    sequence: Optional[int] = None          # stream.frame ordering
    data: Optional[Any] = None              # stream.frame payload

    @property
    def failed(self) -> bool:
        return bool(self.error) or self.success is False



class DeviceStatus(StrEnum):
    """
    Device lifecycle state.

    StrEnum (not Enum) is critical: it makes
        str(DeviceStatus.ONLINE) == "online"
        DeviceStatus.ONLINE == "online"
    so accidental `str(status)` calls produce the correct value instead
    of "DeviceStatus.ONLINE". This eliminates the class of bug where
    enum reprs leak into DB columns.
    """
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

    # Application information.
    app_version: str
    runtime_version: str

    # Device owner.
    user_id: str

    # Device platform information.
    platform: DevicePlatform 
    architecture: DeviceArchitecture
    device_type: DeviceType
    platform_version: Optional[str] = None
    os_version: Optional[str] = None
    

    description: Optional[str] = None
    status: DeviceStatus = DeviceStatus.UNKNOWN

    # Network information.
    hostname: Optional[str] = None
    address: Optional[str] = None
    port: Optional[int] = None

    # Device capabilities.
    capabilities: List[DeviceCapability] = Field(default_factory=list)

    # Arbitrary device metadata.
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Timestamps.
    registered_at: datetime = Field(default_factory=get_date)
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
        return (
            f"{self.device_type}:{self.platform}:{self.architecture}:"
            f"{self.name}:{uuid.uuid4()}"
        )

    def is_online(self) -> bool:
        """Return whether the device is considered online."""
        return self.status == DeviceStatus.ONLINE


class DeviceRegistrationInfo(BaseModel):
    """Inner block of a device registration request."""

    name: str
    device_type: DeviceType

    app_version: str
    runtime_version: str

    platform: DevicePlatform
    platform_version: str
    os_version: str

    architecture: DeviceArchitecture
    hostname: Optional[str] = None

    capabilities: List[DeviceCapability] = Field(default_factory=list)

    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeviceRegistration(BaseModel):
    """
    Registration payload sent by the app.

    device_id is optional. When the app has previously registered and
    stored a device id, it sends that id back so the backend updates
    the existing row instead of minting a new one on every launch.
    """

    device: DeviceRegistrationInfo

    device_id: Optional[str] = None

    connection_type: Optional[str] = None

    connection_config: Dict[str, Any] = Field(default_factory=dict)


class DeviceRegistrationResponse(BaseModel):
    """Response to a device registration request."""
    success: bool
    device_id: str
    device: DeviceInfo
    message: Optional[str] = None
    error: Optional[str] = None
    registered_at: datetime = Field(default_factory=get_date)



class DeviceHeartbeat(BaseModel):
    """Heartbeat sent by a device/executor."""

    device_id: str
    timestamp: datetime = Field(default_factory=get_date)

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