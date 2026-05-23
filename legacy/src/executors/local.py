from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from ..plugins.base import PluginResult
from ..plugins.registry import PluginRegistry


@dataclass
class LocalExecutor:
    registry: PluginRegistry

    def run(self, plugin_name: str, **kwargs: Any) -> PluginResult:
        plugin = self.registry.get(plugin_name)
        if not plugin:
            return PluginResult(ok=False, content=f"Unknown plugin: {plugin_name}")
        return plugin.execute(**kwargs)

