from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol


@dataclass
class PluginResult:
    ok: bool
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False


class Plugin(Protocol):
    name: str
    capabilities: List[str]

    def matches(self, text: str) -> bool: ...
    def execute(self, **kwargs: Any) -> PluginResult: ...

