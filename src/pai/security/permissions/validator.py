class PermissionValidationError(ValueError):
    pass

class PermissionValidator:
    def __init__(self, registry):
        self.registry = registry

    def validate(self, permission_name: str, plugin_id: str | None, action: str):
        if not permission_name:
            raise PermissionValidationError("Permission name is required")
        declaration = self.registry.get(permission_name)
        if declaration is None:
            raise PermissionValidationError(f"Unknown permission: {permission_name}")
        if action not in declaration.actions:
            raise PermissionValidationError(f"Action '{action}' is not declared for {permission_name}")
        if not plugin_id:
            raise PermissionValidationError("plugin_id is required")
        return declaration