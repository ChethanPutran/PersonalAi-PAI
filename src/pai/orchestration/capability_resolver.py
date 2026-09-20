"""
Capability resolution.

Capability resolution answers:
    "What capability/plugin can perform this task?"

It does not decide which physical device should execute it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class CapabilityResolution:
    capability: str
    plugin_name: str
    provider: Any = None


class CapabilityResolver:

    def __init__(
        self,
        plugin_manager: Any,
        capability_router: Any = None,
    ):
        self.plugin_manager = plugin_manager
        self.capability_router = capability_router

    async def resolve(
        self,
        task: Any,
    ) -> CapabilityResolution:

        task_dict = (
            task.to_dict()
            if hasattr(task, "to_dict")
            else dict(task)
        )

        capability = (
            task_dict.get("capability")
            or task_dict.get("type")
        )

        if not capability:
            raise RuntimeError(
                "Task does not specify a capability"
            )

        plugin_name = await self._find_plugin(
            capability
        )

        if not plugin_name:
            raise RuntimeError(
                f"No plugin provides capability "
                f"'{capability}'"
            )

        return CapabilityResolution(
            capability=capability,
            plugin_name=plugin_name,
        )

    async def _find_plugin(
        self,
        capability: str,
    ) -> Optional[str]:

        registry = getattr(
            self.plugin_manager,
            "registry",
            None,
        )

        if registry:
            method = getattr(
                registry,
                "get_plugin_for_capability",
                None,
            )

            if method:
                return await method(capability)

        method = getattr(
            self.plugin_manager,
            "get_plugin_for_capability",
            None,
        )

        if method:
            return await method(capability)

        plugins = getattr(
            self.plugin_manager,
            "plugins",
            {},
        )

        for plugin in plugins.values():
            if capability in getattr(
                plugin,
                "capabilities",
                [],
            ):
                return getattr(
                    plugin,
                    "name",
                    None,
                )

        return None