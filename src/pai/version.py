"""Single source of truth for the versions the backend speaks.

Three independent version axes exist:

* ``RUNTIME_VERSION``       – this backend / plugin runtime (what a manifest's
                              ``runtime.minPaiVersion`` is compared against).
* ``MANIFEST_SCHEMA_*``     – the manifest ``schemaVersion`` values this backend can parse.
* app version               – the Flutter client; reported per device at registration
                              (``devices.app_version``) and compared against
                              ``runtime.minAppVersion``.

Keep this module dependency-free: it is imported by ``pai/__init__`` and by manifest parsing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from packaging.version import InvalidVersion, Version

RUNTIME_VERSION = "1.0.0"

#: Oldest / newest manifest ``schemaVersion`` this backend understands.
MANIFEST_SCHEMA_MIN = 1
MANIFEST_SCHEMA_MAX = 2

#: Oldest client app the backend still talks to (older apps are told to upgrade).
MIN_SUPPORTED_APP_VERSION = "1.0.0"


def parse_version(value: Optional[str]) -> Optional[Version]:
    """Lenient parse: ``None``/empty/garbage -> ``None`` (never raises)."""
    if not value:
        return None
    try:
        return Version(str(value).strip().lstrip("vV"))
    except InvalidVersion:
        return None


def version_key(value: Optional[str]) -> Version:
    """Sort key; unparseable versions sort lowest."""
    return parse_version(value) or Version("0.0.0")


def is_valid_version(value: Optional[str]) -> bool:
    return parse_version(value) is not None


def is_newer(candidate: Optional[str], current: Optional[str]) -> bool:
    c, cur = parse_version(candidate), parse_version(current)
    return c is not None and cur is not None and c > cur


@dataclass(frozen=True)
class Compatibility:
    compatible: bool
    #: machine-readable: ``runtime_too_old`` | ``app_too_old`` | ``schema_unsupported``
    code: Optional[str] = None
    reason: Optional[str] = None
    #: the version the user/runtime must reach to use this plugin
    required: Optional[str] = None

    def to_dict(self) -> dict:
        return {"compatible": self.compatible, "code": self.code,
                "reason": self.reason, "required": self.required}


COMPATIBLE = Compatibility(True)


def check_compatibility(
    *,
    schema_version: int,
    min_runtime: Optional[str],
    min_app: Optional[str],
    runtime_version: str = RUNTIME_VERSION,
    app_version: Optional[str] = None,
) -> Compatibility:
    """Can a plugin with these requirements run on this runtime / this app?

    ``app_version=None`` (an unknown client) skips the app check rather than failing it, so
    a device that never reported a version is not locked out; the client re-checks locally.
    """
    if not (MANIFEST_SCHEMA_MIN <= schema_version <= MANIFEST_SCHEMA_MAX):
        return Compatibility(
            False, "schema_unsupported",
            f"Manifest schema v{schema_version} is not supported by this server "
            f"(supports v{MANIFEST_SCHEMA_MIN}-v{MANIFEST_SCHEMA_MAX}).",
        )
    need_rt = parse_version(min_runtime)
    if need_rt is not None and parse_version(runtime_version) < need_rt:
        return Compatibility(
            False, "runtime_too_old",
            f"Requires PAI runtime {min_runtime} or newer (server is {runtime_version}).",
            min_runtime,
        )
    need_app, have_app = parse_version(min_app), parse_version(app_version)
    if need_app is not None and have_app is not None and have_app < need_app:
        return Compatibility(
            False, "app_too_old",
            f"Requires PAI app {min_app} or newer (this device has {app_version}).",
            min_app,
        )
    return COMPATIBLE
