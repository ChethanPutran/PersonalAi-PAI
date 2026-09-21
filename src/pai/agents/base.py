"""Base Agent class for all autonomous agents."""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from loguru import logger


class BaseAgent(ABC):
    """
    Base class for all agents.
    
    Agents are autonomous decision-makers that use plugins and memory
    to accomplish goals.
    """
    
    def __init__(self, name: str):
        self.name = name
        self._running = False
        self._capabilities: List[str] = []
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the agent."""
        pass
    
    @abstractmethod
    async def process_goal(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a goal and return result.
        
        Args:
            goal: User goal or task
            context: Additional context
            
        Returns:
            Processing result
        """
        pass
    
    @abstractmethod
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Handle an event from the event bus.
        
        Args:
            event_type: Type of event
            data: Event data
        """
        pass
    
    def get_capabilities(self) -> List[str]:
        """Get agent capabilities."""
        return self._capabilities
    
    async def start(self) -> None:
        """Start the agent."""
        self._running = True
        logger.info(f"Agent {self.name} started")
    
    async def stop(self) -> None:
        """Stop the agent."""
        self._running = False
        logger.info(f"Agent {self.name} stopped")
    
    async def use_plugin(self, plugin_name: str, action: str, params: Dict[str, Any]) -> Any:
        """Use a plugin through the kernel."""
        if not self.kernel:
            raise RuntimeError("Agent not connected to kernel")
        
        return await self.kernel.plugin_manager.execute_plugin(plugin_name, action, params)
    
    async def store_memory(self, memory_type: str, data: Dict[str, Any]) -> None:
        """Store data in memory."""
        if not self.kernel:
            raise RuntimeError("Agent not connected to kernel")
        
        await self.kernel.memory_manager.remember(memory_type, data)
    
    async def recall_memory(self, memory_type: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Recall data from memory."""
        if not self.kernel:
            raise RuntimeError("Agent not connected to kernel")
        
        return await self.kernel.memory_manager.recall(memory_type, query)