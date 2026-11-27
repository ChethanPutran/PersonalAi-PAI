"""Device agent configuration + wire models."""
from __future__ import annotations

import os
import platform as _platform 
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .config_file import DeviceFile, find_config_file, load_config_file


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

    log_dir: Optional[Path] = None                     # None: <pai home>/logs

    heartbeat_interval: float = 20.0
    reconcile_interval: float = 60.0
    reconnect_min: float = 1.0
    reconnect_max: float = 60.0
    index_interval_minutes: float = 30.0               # document re-index period (only when the Files plugin has "Index documents" on)
    progress_keepalive_seconds: float = 20.0           # "still installing" frames; under the backend's 120 s stall limit

    # Remote access from the config file / environment: True or False = apply that to the backend when the agent
    # starts; None = follow the owner's setting. The device refuses remote requests until the backend has
    # reported that setting (fail closed), whatever these say.
    remote_access_install: Optional[bool] = None
    remote_access_invoke: Optional[bool] = None

    config_file: Optional[Path] = None                 # the file these settings came from, if any

    @property
    def api_base(self) -> str:
        return self.backend_url.rstrip("/") + "/api/v1"

    @property
    def ws_url(self) -> str:
        u = self.backend_url.rstrip("/")
        u = u.replace("https://", "wss://").replace("http://", "ws://")
        return u + "/api/v1"

    @classmethod
    def from_env(cls, config_file: Optional[str] = None) -> "DeviceConfig":
        """Kept for callers that predate the config file: same as :meth:`load`."""
        return cls.load(config_file)

    @classmethod
    def load(cls, config_file: Optional[str] = None) -> "DeviceConfig":
        """Environment variable > config file (``pai-device.yaml``, see :mod:`pai_device.config_file`) > default.

        Raises ``RuntimeError`` (``ConfigFileError`` for a bad file) with a message that says what to set."""
        env = os.environ
        path = find_config_file(config_file)
        f = load_config_file(path) if path else DeviceFile()

        # Accept PAI_USERNAME as an alias of PAI_EMAIL (the backend field is `email: EmailStr`).
        email = env.get("PAI_SERVER_EMAIL") or env.get("PAI_USERNAME") or f.account.email
        password = env.get("PAI_SERVER_PASSWORD") or f.account.password
        backend = env.get("PAI_BACKEND_URL") or f.backend.url

        missing = [
            name for name, val in (
                ("backend URL (PAI_BACKEND_URL or backend.url)", backend),
                ("account email (PAI_SERVER_EMAIL or account.email)", email),
                ("account password (PAI_SERVER_PASSWORD or account.password)", password),
            ) if not val
        ]
        if missing:
            where = f" (config file: {path})" if path else " (no config file found)"
            raise RuntimeError(f"Missing required setting(s): {', '.join(missing)}{where}")

        home_setting = env.get("PAI_HOME") or f.paths.home
        home = Path(home_setting).expanduser() if home_setting else pai_home()

        def folder(env_name: str, file_value: Optional[str], default: str) -> Path:
            chosen = env.get(env_name) or file_value
            return Path(chosen).expanduser() if chosen else home / default

        return cls(
            backend_url=backend,
            email=email,
            password=password,
            device_name=env.get("PAI_DEVICE_NAME") or f.device.name or socket.gethostname(),
            device_type=env.get("PAI_DEVICE_TYPE") or f.device.type,
            state_dir=folder("PAI_STATE_DIR", f.paths.state_dir, "state"),
            plugin_dir=folder("PAI_PLUGIN_DIR", f.paths.plugin_dir, "plugins"),
            log_dir=folder("PAI_LOG_DIR", f.paths.log_dir, "logs"),
            heartbeat_interval=f.timing.heartbeat_interval,
            reconcile_interval=f.timing.reconcile_interval,
            reconnect_min=f.timing.reconnect_min,
            reconnect_max=f.timing.reconnect_max,
            index_interval_minutes=f.timing.index_interval_minutes,
            progress_keepalive_seconds=f.timing.progress_keepalive,
            remote_access_install=_flag(env.get("PAI_REMOTE_INSTALL"), f.remote_access.install),
            remote_access_invoke=_flag(env.get("PAI_REMOTE_INVOKE"), f.remote_access.invoke),
            config_file=path,
        )


def _flag(env_value: Optional[str], file_value: Optional[bool]) -> Optional[bool]:
    """A true/false/unset setting: the environment variable (1/true/yes/on or 0/false/no/off) wins over the
    file; unset everywhere stays None."""
    if env_value is None or not env_value.strip():
        return file_value
    return env_value.strip().lower() in ("1", "true", "yes", "on")


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
