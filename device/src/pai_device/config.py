"""Device agent configuration + wire models."""
from __future__ import annotations

import os
import platform as _platform 
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class StorageNotWritable(RuntimeError):
    """A directory the agent must write to (device id, plugins, logs) is not writable."""


def pai_home() -> Path:
    """Root of everything the agent stores: ``$PAI_HOME``, else ``~/.pai``.

    In a container whose user has no passwd entry ``Path.home()`` fails; set ``PAI_HOME`` (the
    official image does) or mount a volume and point ``PAI_HOME`` at it."""
    env = os.environ.get("PAI_HOME")
    if env:
        return Path(env).expanduser()
    try:
        return Path.home() / ".pai"
    except RuntimeError as exc:                       # no HOME and no passwd entry
        raise StorageNotWritable(
            "Cannot work out a home directory for this user. Set PAI_HOME to a writable "
            "directory (for Docker: -e PAI_HOME=/data -v pai-device:/data).") from exc


def ensure_writable_dir(path: Path, what: str) -> Path:
    """Create ``path`` if needed and prove we can write in it, or explain how to fix it."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-test-{os.getpid()}"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise StorageNotWritable(
            f"The {what} directory {path} is not writable ({exc.strerror or exc}).\n"
            "  Docker: a named volume created by an older image is owned by root. Remove it once "
            "(docker volume rm pai-device) and run again,\n"
            "          or run as your own user with a folder you own: "
            "--user \"$(id -u):$(id -g)\" -e PAI_HOME=/data -v \"$HOME/.pai-device:/data\".\n"
            "  Otherwise: make the directory writable (chown/chmod), or point PAI_HOME / "
            "PAI_STATE_DIR / PAI_PLUGIN_DIR at one that is.") from exc
    return path


def _default_arch() -> str:
    m = _platform.machine().lower()
    return {
        "x86_64": "x86_64", "amd64": "x86_64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(m, m or "unknown")


def _default_platform() -> str:
    s = _platform.system().lower()
    return {
        "linux": "linux", "darwin": "macos", "windows": "windows",
    }.get(s, "unknown")


@dataclass
class DeviceConfig:
    backend_url: str                                   # e.g. "http://localhost:8000"
    email: str                                         # backend user email
    password: str                                      # backend user password
    device_name: str                                   # e.g. "worker-01"
    device_type: str = "server"
    platform: str = field(default_factory=_default_platform)
    architecture: str = field(default_factory=_default_arch)
    os_version: str = field(default_factory=_platform.release)
    hostname: str = field(default_factory=socket.gethostname)
    app_version: str = "1.0.0"
    runtime_version: str = "1.0.0"

    state_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PAI_STATE_DIR") or pai_home() / "state")
    )
    plugin_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PAI_PLUGIN_DIR") or pai_home() / "plugins")
    )

    heartbeat_interval: float = 20.0
    reconcile_interval: float = 60.0
    reconnect_min: float = 1.0
    reconnect_max: float = 60.0

    @property
    def api_base(self) -> str:
        return self.backend_url.rstrip("/") + "/api/v1"

    @property
    def ws_url(self) -> str:
        u = self.backend_url.rstrip("/")
        u = u.replace("https://", "wss://").replace("http://", "ws://")
        return u + "/api/v1"

    @classmethod
    def from_env(cls) -> "DeviceConfig":
        # Accept PAI_USERNAME as an alias of PAI_EMAIL (the backend
        # field is `email: EmailStr`, so the value must be email-shaped).
        email = os.environ.get("PAI_SERVER_EMAIL") or os.environ.get("PAI_USERNAME")
        password = os.environ.get("PAI_SERVER_PASSWORD")
        backend = os.environ.get("PAI_BACKEND_URL")

        missing = [
            name for name, val in (
                ("PAI_BACKEND_URL", backend),
                ("PAI_SERVER_EMAIL or PAI_SERVER_EMAIL", email),
                ("PAI_SERVER_PASSWORD", password),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )

        return cls(
            backend_url=backend,
            email=email,
            password=password,
            device_name=os.environ.get("PAI_DEVICE_NAME") or socket.gethostname(),
            device_type=os.environ.get("PAI_DEVICE_TYPE", "server"),
        )


# ---------------------------------------------------------------------------
# Wire models — mirror the backend's pydantic schemas
# ---------------------------------------------------------------------------

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
    capabilities: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


class RegisterRequest(BaseModel):
    device: DeviceRegistrationInfo
    device_id: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    is_admin: bool = False


class InstallReport(BaseModel):
    device_id: str
    version: str
    success: bool
    artifact_sha256: Optional[str] = None
    error: Optional[str] = None


class UninstallReport(BaseModel):
    device_id: str
    success: bool
    error: Optional[str] = None


class DevicePluginReport(BaseModel):
    id: str
    version: str
    enabled: bool = False
    sha256: Optional[str] = None
