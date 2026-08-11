from abc import ABC, abstractmethod
from typing import Dict, Any
from loguru import logger

class BaseExecutor(ABC):
    """Base class for all distributed executors."""
    
    def __init__(self, name: str):
        self.name = name
        self._running = False
    
    @abstractmethod
    async def initialize(self) -> None:
        pass
    
    @abstractmethod
    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        pass
    
    async def start(self) -> None:
        self._running = True
        logger.info(f"Executor {self.name} started")
    
    async def stop(self) -> None:
        self._running = False