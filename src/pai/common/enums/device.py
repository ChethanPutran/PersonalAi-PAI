"""Wire-protocol enums shared by the backend and (mirrored) the Flutter app.

Every string that crosses the WebSocket between backend and device
should come from one of these enums. They are the single source of
truth for field values — a typo in a `"pause"` string is now a
compile-time error on both sides.
"""
from __future__ import annotations

from enum import Enum, StrEnum

class DevicePlatform(StrEnum):
    """The `platform` field on a device registration frame."""

    ANDROID = "android"
    IOS = "ios"
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"

class DeviceArchitecture(StrEnum):
    """The `architecture` field on a device registration frame."""

    ARM64 = "arm64"
    X86_64 = "x86_64"
    X86 = "x86"

    @classmethod
    def _missing_(cls, value):
        """Accept the names other toolchains use (Android ABI names, uname output...)."""
        aliases = {
            "arm64-v8a": cls.ARM64, "aarch64": cls.ARM64, "armv8": cls.ARM64,
            "x64": cls.X86_64, "amd64": cls.X86_64, "x86-64": cls.X86_64,
            "i386": cls.X86, "i686": cls.X86,
        }
        return aliases.get(str(value).strip().lower())

class DeviceType(StrEnum):
    """The `device_type` field on a device registration frame."""

    PHONE = "phone"
    TABLET = "tablet"
    LAPTOP = "laptop"
    DESKTOP = "desktop"
    SERVER = "server"       # the backend host itself (server-side plugins, embedded agent)
    UNKNOWN = "unknown"     # device that did not report a type

    @classmethod
    def _missing_(cls, value):
        """Old rows/manifests used 'mobile' (= phone) and similar loose names."""
        aliases = {
            "mobile": cls.PHONE, "smartphone": cls.PHONE, "handset": cls.PHONE,
            "notebook": cls.LAPTOP, "pc": cls.DESKTOP, "workstation": cls.DESKTOP,
        }
        return aliases.get(str(value).strip().lower())

class DeviceOpStatus(StrEnum):
    """The `status` field on a CONTROL_ACK frame — what the device reports."""

    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"
    UNKNOWN = "unknown"  # Used in CONTROL_ACK frames to indicate that the device does not know the status of the operation.
    NONE = "none"  # Used in CONTROL_ACK frames to indicate that the device does not support the operation.
    
class DeviceMessageType(StrEnum):
    """The `type` field on any frame in either direction."""

    # ── Lifecycle / handshake ──
    CONNECTED = "connected"
    PING = "ping"
    PONG = "pong"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    ERROR = "error"
    STREAM_START = "stream.start"
    STREAM_END = "stream.end"
    STREAME_FRAME = "stream.frame"

    # ── Plugin sync ──
    SYNC_PLUGINS_REQUEST = "sync_plugins_request"
    SYNC_PLUGINS_RESPONSE = "sync_plugins_response"

    # ── Capability invocation ──
    INVOKE = "invoke"                  # backend → device
    RESULT = "result"                  # device  → backend
    CONTROL = "control"                # backend → device (pause/resume/cancel)
    CONTROL_ACK = "control_ack"        # device  → backend

    # ── Events (device → backend) ──
    EVENT = "event"
    PLUGIN_PROGRESS = "plugin.progress"   # device → backend: progress of a plugin install/uninstall

    # ── Task / plan events (backend → device) ──
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_CANCELLED = "task.cancelled"
    PLAN_CREATED = "plan.created"
    PLAN_COMPLETED = "plan.completed"
    PLAN_FAILED = "plan.failed"


class DeviceControlOp(StrEnum):
    """The `op` field on a CONTROL frame — what the backend wants done."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    NONE = "none"  # Used in CONTROL_ACK frames to indicate no-op (e.g., pause/resume not supported).


# Convenience sets used by the WS route to dispatch without listing
# every member inline.
TASK_EVENT_TYPES: frozenset[str] = frozenset({
    DeviceMessageType.TASK_CREATED.value,
    DeviceMessageType.TASK_UPDATED.value,
    DeviceMessageType.TASK_STARTED.value,
    DeviceMessageType.TASK_COMPLETED.value,
    DeviceMessageType.TASK_FAILED.value,
    DeviceMessageType.TASK_PAUSED.value,
    DeviceMessageType.TASK_RESUMED.value,
    DeviceMessageType.TASK_CANCELLED.value,
    DeviceMessageType.PLAN_CREATED.value,
    DeviceMessageType.PLAN_COMPLETED.value,
    DeviceMessageType.PLAN_FAILED.value,
})