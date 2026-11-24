"""Registry for capabilities implemented natively by the backend."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pai.common.enums.task import TaskType
from pai.common.task_controls import CancelMode, TaskControls


@dataclass(frozen=True)
class BuiltinCapability:
    id: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    # What the user may do while it runs. Built-ins run in-process and are cooperative about
    # cancellation (the await they sit in is interrupted); none can pause unless it opts in AND
    # its handler checks the pause gate (see PauseGate).
    controls: TaskControls = field(
        default_factory=lambda: TaskControls(pause=False, cancel=CancelMode.COOPERATIVE))
    # How it runs (same meaning as the manifest `taskType`). Declared by the capability, never by the LLM.
    task_type: TaskType = TaskType.ONE_SHOT
    timeout_seconds: Optional[float] = None


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
                "task_type": c.task_type.value,
                "timeout_seconds": c.timeout_seconds,
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