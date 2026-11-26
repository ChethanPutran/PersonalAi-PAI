"""PAI device agent: runs on a device and talks to a PAI backend over its public HTTP/WS API.

Standalone: it imports nothing from the ``pai`` backend package."""
from __future__ import annotations

from .agent.agent import DeviceAgent
from .config import DeviceConfig
from ._entry import main

__all__ = ["main", "DeviceAgent", "DeviceConfig"]
