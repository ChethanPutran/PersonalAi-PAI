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





_system_permission_cache: "dict[tuple[str, str | None], PermissionSet]" = {}


def load_system_permissions(config_path: str | None = None, plugin: str | None = None) -> "PermissionSet":
    from .registry import PermissionSet
    """Load what the user granted ONE plugin from ~/.config/pai/permissions.json.

    The device agent stores every decision per plugin: {"pai.camera/camera": "allow", ...} (key =
    "<native module>/<permission>"). Only "allow" entries of the named ``plugin`` are included, so one
    plugin's grant is never another's. Without a plugin, or for the flat entries older releases wrote
    ("camera": "allow", shared by everyone), nothing is granted: fail closed.

    Result is cached per (file, plugin) for the process lifetime.
    """
    import json
    import os

    path = config_path or os.path.expanduser("~/.config/pai/permissions.json")
    key = (path, plugin)
    cached = _system_permission_cache.get(key)
    if cached is not None:
        return cached
    if not plugin:
        _system_permission_cache[key] = PermissionSet()
        return _system_permission_cache[key]
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # No config — grant nothing
        _system_permission_cache[key] = PermissionSet()
        return _system_permission_cache[key]

    prefix = f"{plugin}/"
    granted: set[PermissionResource] = set()
    for name, value in data.items():
        if not str(name).startswith(prefix):
            continue                                   # another plugin's decision, or a legacy shared one
        if str(value).lower() in ("allow", "true", "yes", "1"):
            perm = MANIFEST_PERMISSION_MAP.get(str(name)[len(prefix):].lower())
            if perm is not None:
                granted.add(perm)

    _system_permission_cache[key] = PermissionSet(permissions=frozenset(granted))
    return _system_permission_cache[key]


def clear_system_permission_cache() -> None:
    """Force a reload on next call to load_system_permissions()."""
    _system_permission_cache.clear()
    
@dataclass(frozen=True)
class PermissionDeclaration:
    name: str
    description: str = ""
    actions: tuple[str, ...] = ()
    risk: str = "medium"
    scopes: tuple[str, ...] = ("generic",)
    requires_consent: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)