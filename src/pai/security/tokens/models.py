from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class SecurityToken:
    token_id: str
    subject: str
    plugin_id: str
    permission: str
    action: str
    resource: str
    issued_at: datetime
    expires_at: datetime
    scope: dict[str, Any]
    risk: str
    signature: str