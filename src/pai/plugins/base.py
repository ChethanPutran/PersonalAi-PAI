from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional


class PluginState(str, Enum):
    """Runtime lifecycle state of a plugin instance."""

    CREATED = "created"
    INITIALIZED = "initialized"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class BasePlugin(ABC):
    """
    Base class for all PAI plugins.

    Important:
        Plugin runtime state is NOT user authorization state.

        enabled/disabled for a user belongs to the user-plugin authorization
        layer, not to this class.
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.config: Dict[str, Any] = config or {}
        self.state = PluginState.CREATED

    @property
    def name(self) -> str:
        """Human-readable plugin name."""
        return self.plugin_id

    @property
    def is_running(self) -> bool:
        return self.state == PluginState.RUNNING

    async def initialize(self) -> None:
        """
        Initialize resources required by the plugin.

        Example:
            BrowserPlugin starts Playwright here.
        """
        self.state = PluginState.INITIALIZED

    async def start(self) -> None:
        """
        Start the plugin runtime.

        Usually called after initialize().
        """
        if self.state not in {
            PluginState.INITIALIZED,
            PluginState.STOPPED,
        }:
            raise RuntimeError(
                f"Cannot start plugin '{self.plugin_id}' "
                f"from state '{self.state.value}'"
            )

        self.state = PluginState.RUNNING

    async def shutdown(self) -> None:
        """Release plugin resources."""
        self.state = PluginState.STOPPED

    async def apply_config(self, config: Dict[str, Any]) -> None:
        """
        Apply runtime configuration.

        Plugins may override this when configuration changes require
        resource reinitialization.
        """
        self.config = config

    async def handle_event(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """
        Optional event-bus hook.

        Plugins that do not consume events do nothing.
        """
        return None

    @abstractmethod
    async def execute(
        self,
        action: str,
        params: Dict[str, Any],
    ) -> Any:
        """
        Execute a plugin capability.

        Authorization should NOT be implemented here.

        The orchestration/security layer decides whether the user is
        allowed to execute the capability. The plugin only performs it.
        """
        raise NotImplementedError

    def supports(self, action: str) -> bool:
        """Return whether this plugin implements the requested action."""
        return action in self.get_capabilities()

    @abstractmethod
    def get_capabilities(self) -> list[str]:
        """Return canonical capability IDs implemented by this plugin."""
        raise NotImplementedError
