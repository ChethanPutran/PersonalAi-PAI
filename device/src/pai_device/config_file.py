"""The device agent's YAML config file.

Everything the agent used to hardcode or take only from the environment can be set in one file:

    backend:        where the PAI backend is
    account:        who the device signs in as (prefer env vars for the password)
    device:         its name and type
    paths:          where it keeps its state, plugins and logs
    timing:         heartbeat / reconcile / reconnect intervals
    remote_access:  the STARTING value of the owner's remote-install / remote-use switches

Lookup order for the file: ``--config`` / the explicit argument, ``$PAI_DEVICE_CONFIG``, ``./pai-device.yaml``,
``$PAI_HOME/pai-device.yaml`` (``~/.pai/pai-device.yaml``). Precedence for every value: environment variable,
then the file, then the built-in default. An unknown key is an error (a typo must not silently do nothing).
See ``pai-device.yaml`` in the package folder for every key with its meaning.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

FILE_NAME = "pai-device.yaml"
ENV_VAR = "PAI_DEVICE_CONFIG"


class ConfigFileError(RuntimeError):
    """The config file is missing (when it was asked for), unreadable, or not valid."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendSection(_Section):
    url: Optional[str] = None                 # e.g. http://localhost:8000


class AccountSection(_Section):
    email: Optional[str] = None
    password: Optional[str] = None            # prefer PAI_SERVER_PASSWORD; if set here keep the file private (chmod 600)


class DeviceSection(_Section):
    name: Optional[str] = None                # default: this machine's hostname
    type: str = "server"


class PathsSection(_Section):
    home: Optional[str] = None                # root of everything below (default ~/.pai); also $PAI_HOME
    state_dir: Optional[str] = None           # device id and local state (default <home>/state)
    plugin_dir: Optional[str] = None          # downloaded plugins (default <home>/plugins)
    log_dir: Optional[str] = None             # http.log (default <home>/logs)


class TimingSection(_Section):
    heartbeat_interval: float = Field(default=20.0, gt=0)      # seconds between liveness signals
    reconcile_interval: float = Field(default=60.0, gt=0)      # seconds between plugin syncs with the backend
    reconnect_min: float = Field(default=1.0, gt=0)            # WebSocket reconnect backoff, first wait
    reconnect_max: float = Field(default=60.0, gt=0)           # ... and the cap
    progress_keepalive: float = Field(default=20.0, gt=0)      # seconds between "still installing" frames; keep well
                                                               # under the backend's 120 s stall limit


class RemoteAccessSection(_Section):
    """STARTING value of what other devices may do to this one, used until the backend reports the owner's
    setting (it does so as soon as the device connects, and again whenever the owner changes it). Fail closed:
    leave both false unless you have a reason to accept remote requests before that report arrives."""
    install: bool = False
    invoke: bool = False


class DeviceFile(_Section):
    backend: BackendSection = Field(default_factory=BackendSection)
    account: AccountSection = Field(default_factory=AccountSection)
    device: DeviceSection = Field(default_factory=DeviceSection)
    paths: PathsSection = Field(default_factory=PathsSection)
    timing: TimingSection = Field(default_factory=TimingSection)
    remote_access: RemoteAccessSection = Field(default_factory=RemoteAccessSection)


def find_config_file(explicit: Optional[str] = None, *, home: Optional[Path] = None) -> Optional[Path]:
    """The file to load, or None when there is none. A file that was asked for (argument or env var) must
    exist; the default places are simply skipped when absent."""
    asked = explicit or os.environ.get(ENV_VAR)
    if asked:
        path = Path(asked).expanduser()
        if not path.is_file():
            raise ConfigFileError(f"Config file not found: {path}")
        return path
    candidates: List[Path] = [Path.cwd() / FILE_NAME]
    base = home if home is not None else _default_home()
    if base is not None:
        candidates.append(base / FILE_NAME)
    return next((c for c in candidates if c.is_file()), None)


def _default_home() -> Optional[Path]:
    env = os.environ.get("PAI_HOME")
    if env:
        return Path(env).expanduser()
    try:
        return Path.home() / ".pai"
    except RuntimeError:
        return None


def load_config_file(path: Path) -> DeviceFile:
    """Parse and validate ``path``. An empty file means "all defaults"."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigFileError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"{path} is not valid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigFileError(f"{path}: the top level must be a mapping of sections (backend, account, ...)")
    try:
        parsed = DeviceFile.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigFileError(f"{path}: {problems}") from exc
    _warn_if_password_is_readable(path, parsed)
    return parsed


def _warn_if_password_is_readable(path: Path, parsed: DeviceFile) -> None:
    if not parsed.account.password:
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        logger.warning("%s holds a password but other users can read it: run `chmod 600 %s` "
                       "(or use PAI_SERVER_PASSWORD instead)", path, path)
