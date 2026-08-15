from abc import ABC
from typing import Any, Dict, List


class BasePlugin(ABC):
    """
    Base class for all plugins. Plugins should inherit from this class and implement
    the methods they need, but the lifecycle methods default to safe no-ops so the
    plugin manager can discover and initialize valid plugins without forcing every
    plugin to implement every optional hook.
    """

    _running = False

    def __init__(self, *args, **kwargs):
        self.kernel = args[0] if args else None
        self.name = getattr(self, "name", self.__class__.__name__)
        self._running = False

    async def initialize(self) -> None:
        """Initialize the plugin. Override if custom setup is needed."""
        self._running = True

    async def shutdown(self) -> None:
        """Shutdown the plugin. Override if custom cleanup is needed."""
        self._running = False

    def get_capabilities(self) -> List[str]:
        """Return a list of capabilities provided by this plugin."""
        return []

    async def check_permissions(self, action: str) -> bool:
        """Default permission check allows the action."""
        return True

    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        """Handle an event published on the event bus."""
        return None

    async def apply_config(self, config: Dict[str, Any]) -> None:
        """Apply configuration to the plugin instance. Override in plugins that support runtime config."""
        return None

    async def start(self) -> None:
        """Start the plugin. Override to perform startup work."""
        self._running = True

    async def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a task. Override this method for plugin functionality."""
        return {}