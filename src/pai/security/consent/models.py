from dataclasses import dataclass, field
from datetime import datetime, timezone
from pai.utils.helpers import get_date

@dataclass
class ConsentRecord:
    subject: str
    plugin_id: str
    permission: str
    granted: bool
    expires_at: datetime | None = None
    approval_required: bool = False
    created_at: datetime = field(default_factory=get_date)
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
    def active(self):
        return self.granted and (self.expires_at is None or datetime.now(timezone.utc) < self.expires_at)