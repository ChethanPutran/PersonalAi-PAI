from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class Permission(str, Enum):
    """
    Permissions represent access to underlying resources.

    Permissions are different from plugin capabilities.

    Example:
        Permission.NETWORK
            -> allows network access

        browser.navigate
            -> is a plugin capability
    """

    NETWORK = "network"

    FILE_READ = "file_read"
    FILE_WRITE = "file_write"

    CAMERA = "camera"
    MICROPHONE = "microphone"

    CALENDAR = "calendar"
    NOTIFICATIONS = "notifications"

    TERMINAL = "terminal"

    BROWSER = "browser"


@dataclass(frozen=True)
class PermissionSet:
    """
    Immutable collection of permissions.
    """

    permissions: FrozenSet[Permission] = frozenset()

    def contains(self, permission: Permission | str) -> bool:
        """Check whether a permission is present."""
        if isinstance(permission, str):
            try:
                permission = Permission(permission)
            except ValueError:
                return False

        return permission in self.permissions

    def contains_all(
        self,
        permissions: set[Permission] | list[Permission],
    ) -> bool:
        """Check whether all requested permissions are present."""
        return all(self.contains(permission) for permission in permissions)

    def contains_any(
        self,
        permissions: set[Permission] | list[Permission],
    ) -> bool:
        """Check whether at least one requested permission is present."""
        return any(self.contains(permission) for permission in permissions)

    def add(self, permission: Permission) -> "PermissionSet":
        """Return a new set with an additional permission."""
        return PermissionSet(
            permissions=self.permissions | {permission}
        )

    def remove(self, permission: Permission) -> "PermissionSet":
        """Return a new set without the given permission."""
        return PermissionSet(
            permissions=self.permissions - {permission}
        )


# Permissions that are considered sensitive and should normally
# require explicit authorization.
SENSITIVE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.FILE_WRITE,
        Permission.CAMERA,
        Permission.MICROPHONE,
        Permission.TERMINAL,
        Permission.BROWSER,
    }
)