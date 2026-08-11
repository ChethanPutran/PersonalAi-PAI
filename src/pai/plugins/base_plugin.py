from abc import ABC, abstractmethod 
from typing import List,Dict, Any


class BasePlugin(ABC):
    """
    Base class for all plugins. Plugins should inherit from this class and implement the required methods.
    """
    _running = False
    def __init__(self, *args, **kwargs):
        self.kernel = args[0] if args else None  # Will be set by PluginManager
        self.name = self.__class__.__name__
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the plugin. Override this method to perform setup tasks."""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the plugin. Override this method to perform cleanup tasks."""
        pass
    
    @abstractmethod
    def get_capabilities(self) -> List[str]:
        """Return a list of capabilities provided by this plugin."""
        return []
    
    @abstractmethod
    async def check_permissions(self, action: str) -> bool:
        """Check if the plugin has permission to perform the given action."""
        return True
    
    @abstractmethod
    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        """Handle an event published on the event bus."""
        pass
    
    @abstractmethod
    async def start(self) -> None:
        """Start the plugin. Override this method to perform actions when the plugin is started."""
        self._running = True
        
    
    @abstractmethod
    async def execute(self, action: str, params: Dict[str, Any]) -> Dict:
        """Execute a task. Override this method to perform the plugin's main functionality."""
        return {}