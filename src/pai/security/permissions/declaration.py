from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import PermissionSet

from pai.common.enums.permission import PermissionResource


# Permissions that are considered sensitive and should normally
# require explicit authorization.
SENSITIVE_PERMISSIONS: frozenset[PermissionResource] = frozenset(
    {
        PermissionResource.FILESYSTEM,
        PermissionResource.CAMERA,
        PermissionResource.MICROPHONE,
        PermissionResource.SHELL,
        PermissionResource.PROCESS,
        PermissionResource.CLIPBOARD,
        PermissionResource.SCREEN,
        PermissionResource.INPUT,
    }
)

# Maps manifest permission string → Permission enum value
# Manifest files use lowercase keys like "camera", "shell", "process"
MANIFEST_PERMISSION_MAP: dict[str, PermissionResource] = {
    "camera": PermissionResource.CAMERA,
    "microphone": PermissionResource.MICROPHONE,
    "notifications": PermissionResource.NOTIFICATIONS,
    "calendar": PermissionResource.CALENDAR,
    "network": PermissionResource.NETWORK,
    "filesystem": PermissionResource.FILESYSTEM,
    "shell": PermissionResource.SHELL,
    "process": PermissionResource.PROCESS,
    "clipboard": PermissionResource.CLIPBOARD,
    "screen": PermissionResource.SCREEN,
    "input": PermissionResource.INPUT,
}

def permissions_from_manifest_strings(strings: list[str]) -> frozenset[PermissionResource]:
    """Map manifest permission string list to Permission enum values.

    Unknown strings are silently ignored (they may be future permissions).
    """
    result = set()
    for s in strings:
        perm = MANIFEST_PERMISSION_MAP.get(s.lower())
        if perm is not None:
            result.add(perm)
    return frozenset(result)





_system_permission_cache: "PermissionSet | None" = None


def load_system_permissions(config_path: str | None = None) -> "PermissionSet":
    from .registry import PermissionSet
    """Load user-granted permissions from ~/.config/pai/permissions.json.

    The file format is: {"camera": "allow", "shell": "allow", ...}
    Only entries with value "allow" are included.

    Result is cached for the process lifetime.
    """
    global _system_permission_cache
    if _system_permission_cache is not None:
        return _system_permission_cache

    import json
    import os

    path = config_path or os.path.expanduser("~/.config/pai/permissions.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # No config — grant nothing
        _system_permission_cache = PermissionSet()
        return _system_permission_cache

    granted: set[PermissionResource] = set()
    for key, value in data.items():
        if str(value).lower() in ("allow", "true", "yes", "1"):
            perm = MANIFEST_PERMISSION_MAP.get(key.lower())
            if perm is not None:
                granted.add(perm)

    _system_permission_cache = PermissionSet(permissions=frozenset(granted))
    return _system_permission_cache


def clear_system_permission_cache() -> None:
    """Force a reload on next call to load_system_permissions()."""
    global _system_permission_cache
    _system_permission_cache = None
    
@dataclass(frozen=True)
class PermissionDeclaration:
    name: str
    description: str = ""
    actions: tuple[str, ...] = ()
    risk: str = "medium"
    scopes: tuple[str, ...] = ("generic",)
    requires_consent: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)