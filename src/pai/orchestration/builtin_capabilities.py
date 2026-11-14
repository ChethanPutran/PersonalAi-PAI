"""Registry for capabilities implemented natively by the backend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass(frozen=True)
class BuiltinCapability:
    id: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Awaitable[Any]]


class BuiltinCapabilityRegistry:
    """Holds every system-provided capability. Parallel to PluginManager's
    catalog but never depends on a user installing/enabling anything."""

    def __init__(self) -> None:
        self._caps: Dict[str, BuiltinCapability] = {}

    def register(self, cap: BuiltinCapability) -> None:
        self._caps[cap.id] = cap

    def get(self, cap_id: str) -> Optional[BuiltinCapability]:
        return self._caps.get(cap_id)

    def has(self, cap_id: str) -> bool:
        return cap_id in self._caps

    def list_all(self) -> List[BuiltinCapability]:
        return list(self._caps.values())

    def describe_with_schema(self) -> Dict[str, Dict[str, Any]]:
        """Same shape as CapabilityRouter.describe_capabilities_with_schema()."""
        return {
            c.id: {
                "id": c.id,
                "description": c.description,
                "parameters": c.parameters,
                "targets": [
                    {
                        "plugin_id": "builtin",
                        "device_id": "self",       # sentinel — no real device
                        "executor_id": "builtin_executor",
                        "device_type": "server",
                    },
                ],
            }
            for c in self._caps.values()
        }