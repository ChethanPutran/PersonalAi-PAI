from dataclasses import dataclass
from typing import FrozenSet

from pai.common.enums.permission import PermissionAction, PermissionResource


from .declaration import PermissionDeclaration

@dataclass(frozen=True)
class PermissionSet:
    """
    Immutable collection of permissions.
    """

    permissions: FrozenSet[PermissionResource] = frozenset()

    def contains(self, permission: PermissionResource | str) -> bool:
        """Check whether a permission is present."""
        if isinstance(permission, str):
            try:
                permission = PermissionResource(permission)
            except ValueError:
                return False

        return permission in self.permissions

    def contains_all(
        self,
        permissions: set[PermissionResource] | list[PermissionResource],
    ) -> bool:
        """Check whether all requested permissions are present."""
        return all(self.contains(permission) for permission in permissions)

    def contains_any(
        self,
        permissions: set[PermissionResource] | list[PermissionResource],
    ) -> bool:
        """Check whether at least one requested permission is present."""
        return any(self.contains(permission) for permission in permissions)

    def add(self, permission: PermissionResource) -> "PermissionSet":
        """Return a new set with an additional permission."""
        return PermissionSet(
            permissions=self.permissions | {permission}
        )

    def remove(self, permission: PermissionResource) -> "PermissionSet":
        """Return a new set without the given permission."""
        return PermissionSet(
            permissions=self.permissions - {permission}
        )


class PermissionRegistry:
    def __init__(self):
        self._items: dict[str, PermissionDeclaration] = {}
        self.register_defaults()

    def register(self, declaration: PermissionDeclaration):
        # Names are "<PermissionResource>.<PermissionAction>"; keep them consistent
        # with AuthorizationRequest.permission, which is derived the same way.
        resource, _, action = declaration.name.partition(".")
        PermissionResource(resource)
        if declaration.actions != (action,):
            raise ValueError(f"{declaration.name}: actions must be ('{action}',)")
        PermissionAction(action)
        if declaration.name in self._items:
            raise ValueError(f"Permission already registered: {declaration.name}")
        self._items[declaration.name] = declaration

    def get(self, name: str) -> PermissionDeclaration | None:
        return self._items.get(name)

    def all(self):
        return tuple(self._items.values())

    def register_defaults(self):
        defaults = [
            PermissionDeclaration("filesystem.read", "Read files", ("read",), "low", ("filesystem",)),
            PermissionDeclaration("filesystem.write", "Write files", ("write",), "high", ("filesystem",)),
            PermissionDeclaration("network.request", "Make network requests", ("request",), "medium", ("network",)),
            PermissionDeclaration("device.control", "Control connected devices", ("control",), "high", ("device",)),
            PermissionDeclaration("system.execute", "Execute system commands", ("execute",), "critical", ("generic",)),
            PermissionDeclaration("shell.execute", "Run shell commands", ("execute",), "critical", ("generic",)),
            PermissionDeclaration("camera.control", "Control the camera", ("control",), "medium", ("device",)),
            PermissionDeclaration("microphone.control", "Record from the microphone", ("control",), "high", ("device",)),
            PermissionDeclaration("screen.read", "Capture the screen", ("read",), "high", ("device",)),
            PermissionDeclaration("input.control", "Type and click on the user's behalf", ("control",), "high", ("device",)),
            PermissionDeclaration("clipboard.read", "Access the clipboard", ("read",), "medium", ("generic",)),
            PermissionDeclaration("notifications.send", "Send notifications", ("send",), "medium", ("generic",)),
        ]
        for item in defaults:
            self.register(item)