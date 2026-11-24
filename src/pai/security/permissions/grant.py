from dataclasses import dataclass, field
from datetime import datetime, timezone

from pai.common.enums.permission import PermissionAction, PermissionResource, PolicySubject

@dataclass
class PermissionGrant:
    subject: PolicySubject
    plugin_id: str
    device_id: str
    resource: PermissionResource
    actions: set[PermissionAction]
    granted: bool = True
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    constraints: dict[str, object] = field(default_factory=dict)
    
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_valid(self):
        return self.granted and (self.expires_at is None or datetime.now(timezone.utc) < self.expires_at)