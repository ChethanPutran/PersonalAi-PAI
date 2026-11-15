"""PAI device agent package."""
from __future__ import annotations

from .agent.agent import DeviceAgent
from .config import DeviceConfig
from ._entry import main

__all__ = ["main", "DeviceAgent", "DeviceConfig"]