from dataclasses import dataclass

@dataclass(frozen=True)
class PermissionRequirement:
    permission: str
    action: str
    plugin_id: str | None = None
    optional: bool = False