"""
Base executor interface.

All local and remote executors implement the same lifecycle and
task execution interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from loguru import logger


class BaseExecutor(ABC):
    """
    Abstract base class for all executors.

    Executors can represent:
    - local services
    - desktop automation
    - Android devices
    - remote machines
    - server-side AI execution
    """

    def __init__(
        self,
        name: str,
        *,
        executor_id: Optional[str] = None,
        kernel: Any = None,
    ) -> None:
        self.name = name
        self.executor_id = executor_id or name
        self.kernel = kernel

        self._initialized = False
        self._running = False

    def get_status(self) -> str:
        """Get the status of the executor."""
        if self._running:
            return "running"
        elif self._initialized:
            return "initialized"
        else:
            return "stopped"
        
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Initialize resources required by the executor.

        Subclasses should override this when initialization is required.
        """
        self._initialized = True
        logger.info("Executor initialized: {}", self.name)

    async def start(self) -> None:
        """
        Start accepting tasks.
        """
        if not self._initialized:
            await self.initialize()

        self._running = True
        logger.info("Executor started: {}", self.name)

    async def stop(self) -> None:
        """
        Stop accepting new tasks.
        """
        self._running = False
        logger.info("Executor stopped: {}", self.name)

    async def shutdown(self) -> None:
        """
        Release executor resources.
        """
        if self._running:
            await self.stop()

        self._initialized = False
        logger.info("Executor shutdown: {}", self.name)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute_task(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute a task.

        Returns a dictionary containing the execution result.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def initialized(self) -> bool:
        return self._initialized

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name!r}, "
            f"running={self.running})"
        )