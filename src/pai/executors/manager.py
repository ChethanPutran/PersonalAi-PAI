"""
Executor registry and lifecycle manager.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from pai.executors.base import BaseExecutor


class ExecutorManager:
    """
    Manages registered executors.

    Responsibilities:
    - register
    - unregister
    - lookup
    - initialize
    - start
    - stop
    - shutdown
    """

    def __init__(self) -> None:
        self._executors: Dict[str, BaseExecutor] = {}

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register(
        self,
        executor: BaseExecutor,
    ) -> None:

        if executor.name in self._executors:
            raise ValueError(
                f"Executor already registered: {executor.name}"
            )

        self._executors[
            executor.name
        ] = executor

        logger.info(
            "Executor registered: {}",
            executor.name,
        )

    def unregister(
        self,
        executor_name: str,
    ) -> Optional[BaseExecutor]:

        executor = self._executors.pop(
            executor_name,
            None,
        )

        if executor:
            logger.info(
                "Executor unregistered: {}",
                executor_name,
            )

        return executor

    def get(
        self,
        executor_name: str,
    ) -> Optional[BaseExecutor]:

        return self._executors.get(
            executor_name
        )

    def require(
        self,
        executor_name: str,
    ) -> BaseExecutor:

        executor = self.get(
            executor_name
        )

        if executor is None:
            raise KeyError(
                f"Executor not found: {executor_name}"
            )

        return executor

    def list(self) -> List[BaseExecutor]:

        return list(
            self._executors.values()
        )

    def names(self) -> List[str]:

        return list(
            self._executors.keys()
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize_all(self) -> None:

        for executor in self._executors.values():
            try:
                await executor.initialize()

            except Exception:
                logger.exception(
                    "Failed to initialize executor: {}",
                    executor.name,
                )

    async def start_all(self) -> None:

        for executor in self._executors.values():
            try:
                await executor.start()

            except Exception:
                logger.exception(
                    "Failed to start executor: {}",
                    executor.name,
                )

    async def stop_all(self) -> None:

        for executor in self._executors.values():
            try:
                await executor.stop()

            except Exception:
                logger.exception(
                    "Failed to stop executor: {}",
                    executor.name,
                )

    async def shutdown_all(self) -> None:

        for executor in self._executors.values():
            try:
                await executor.shutdown()

            except Exception:
                logger.exception(
                    "Failed to shutdown executor: {}",
                    executor.name,
                )

    def __contains__(
        self,
        executor_name: str,
    ) -> bool:

        return executor_name in self._executors