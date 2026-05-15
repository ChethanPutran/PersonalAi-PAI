from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .local import LocalExecutor
from ..plugins.base import PluginResult


@dataclass
class ExecutorManager:
    local: LocalExecutor

    def execute(self, plugin_name: str, **kwargs: Any) -> PluginResult:
        return self.local.run(plugin_name, **kwargs)

