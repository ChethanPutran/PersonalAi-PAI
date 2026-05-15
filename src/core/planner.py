from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..memory.store import MemoryStore
from ..plugins.registry import PluginRegistry


@dataclass
class RuleBasedPlanner:
    registry: PluginRegistry

    def plan(self, message: str, memory: MemoryStore) -> Dict[str, object]:
        text = message.lower().strip()
        if not text:
            return {"type": "direct", "answer": "Say something and I’ll help."}

        if self.registry.match(text) is None and not re.search(r"\d+\s*[\+\-\*/]\s*\d+", text):
            return {"type": "direct", "answer": f"I can help, but I need a matching capability for: {message}"}

        plugin = self.registry.match(text)
        if plugin is None and re.search(r"\d+\s*[\+\-\*/]\s*\d+", text):
            plugin = self.registry.get("calculator")
        params: Dict[str, object] = {}
        if plugin.name == "calculator":
            params["expression"] = message.split("=", 1)[-1].strip() if "=" in message else message
        elif plugin.name == "web-search":
            params["query"] = message
        elif plugin.name == "weather":
            params["location"] = message.split("in", 1)[-1].strip() if " in " in text else "New York"
        elif plugin.name == "file":
            params["action"] = "read" if "read" in text or "open" in text else "write" if "write" in text or "save" in text else "exists"
            params["path"] = message.split()[-1]
            if params["action"] == "write":
                params["content"] = message
        return {"type": "plugin", "plugin": plugin.name, "params": params}

import re
