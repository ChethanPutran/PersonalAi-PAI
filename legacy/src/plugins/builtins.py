from __future__ import annotations

import ast
import operator as op
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .base import PluginResult


@dataclass
class TimePlugin:
    name: str = "time"
    capabilities: List[str] = None

    def __post_init__(self):
        self.capabilities = ["time", "clock", "timezone"]

    def matches(self, text: str) -> bool:
        return any(token in text.lower() for token in self.capabilities)

    def execute(self, **kwargs: Any) -> PluginResult:
        return PluginResult(ok=True, content=datetime.now().isoformat(timespec="seconds"))


@dataclass
class CalculatorPlugin:
    name: str = "calculator"
    capabilities: List[str] = None

    def __post_init__(self):
        self.capabilities = ["calculate", "math", "sum", "minus", "multiply", "divide"]

    def matches(self, text: str) -> bool:
        return any(token in text.lower() for token in self.capabilities)

    def execute(self, **kwargs: Any) -> PluginResult:
        expr = str(kwargs.get("expression", ""))
        ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow}

        def _eval(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in ops:
                return ops[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return +_eval(node.operand) if isinstance(node.op, ast.UAdd) else -_eval(node.operand)
            raise ValueError("Unsupported expression")

        return PluginResult(ok=True, content=str(_eval(ast.parse(expr, mode="eval").body)))


@dataclass
class WebSearchPlugin:
    name: str = "web-search"
    capabilities: List[str] = None

    def __post_init__(self):
        self.capabilities = ["search", "web", "find"]

    def matches(self, text: str) -> bool:
        return any(token in text.lower() for token in self.capabilities)

    def execute(self, **kwargs: Any) -> PluginResult:
        query = str(kwargs.get("query", ""))
        url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                html = response.read().decode("utf-8", errors="ignore")
            title = html.split("<title>", 1)[1].split("</title>", 1)[0] if "<title>" in html else "Search results"
            return PluginResult(ok=True, content=title, metadata={"url": url})
        except Exception as exc:
            return PluginResult(ok=False, content=f"Search unavailable: {exc}", metadata={"url": url})


@dataclass
class FilePlugin:
    workspace_root: Path
    name: str = "file"
    capabilities: List[str] = None

    def __post_init__(self):
        self.capabilities = ["file", "read", "write", "open", "save"]

    def matches(self, text: str) -> bool:
        return any(token in text.lower() for token in self.capabilities)

    def execute(self, **kwargs: Any) -> PluginResult:
        action = kwargs.get("action", "read")
        path = (self.workspace_root / kwargs["path"]).resolve()
        if self.workspace_root not in path.parents and path != self.workspace_root:
            return PluginResult(ok=False, content="Path is outside the workspace", requires_approval=True)

        if action == "read":
            return PluginResult(ok=True, content=path.read_text())
        if action == "write":
            content = str(kwargs.get("content", ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return PluginResult(ok=True, content=f"Wrote {len(content)} characters to {path}", requires_approval=True)
        if action == "exists":
            return PluginResult(ok=True, content=str(path.exists()))
        return PluginResult(ok=False, content=f"Unsupported action: {action}")


@dataclass
class WeatherPlugin:
    name: str = "weather"
    capabilities: List[str] = None

    def __post_init__(self):
        self.capabilities = ["weather", "forecast", "temperature"]

    def matches(self, text: str) -> bool:
        return any(token in text.lower() for token in self.capabilities)

    def execute(self, **kwargs: Any) -> PluginResult:
        location = kwargs.get("location", "your location")
        url = f"https://wttr.in/{urllib.parse.quote(str(location))}?format=%C+%t"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return PluginResult(ok=True, content=response.read().decode("utf-8").strip())
        except Exception as exc:
            return PluginResult(ok=False, content=f"Weather unavailable: {exc}")

