from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .base import Plugin
from .builtins import CalculatorPlugin, FilePlugin, TimePlugin, WeatherPlugin, WebSearchPlugin


@dataclass
class PluginRegistry:
    workspace_root: object
    plugins: Dict[str, Plugin] = field(default_factory=dict)

    def __post_init__(self):
        self.register(TimePlugin())
        self.register(CalculatorPlugin())
        self.register(WebSearchPlugin())
        self.register(WeatherPlugin())
        self.register(FilePlugin(self.workspace_root))

    def register(self, plugin: Plugin) -> None:
        self.plugins[plugin.name] = plugin

    def get(self, name: str) -> Optional[Plugin]:
        return self.plugins.get(name)

    def list(self) -> List[str]:
        return sorted(self.plugins.keys())

    def match(self, text: str) -> Optional[Plugin]:
        for plugin in self.plugins.values():
            if plugin.matches(text):
                return plugin
        return None

