"""
Task scheduler for executors.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from pai.executors.android import AndroidExecutor
from pai.executors.desktop import DesktopExecutor
from pai.executors.manager import ExecutorManager
from pai.executors.remote import RemoteExecutor
from pai.executors.server import ServerExecutor


class ExecutorScheduler:
    """
    Routes tasks to registered executors.

    The scheduler does not know how an executor performs a task.
    It only selects the executor and invokes its common interface.
    """

    def __init__(
        self,
        kernel: Any = None,
        *,
        manager: Optional[ExecutorManager] = None,
        register_defaults: bool = True,
    ) -> None:

        self.kernel = kernel

        self.manager = (
            manager
            or ExecutorManager()
        )

        self._initialized = False
        self._running = False

        if register_defaults:
            self._register_default_executors()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _register_default_executors(self) -> None:

        self._safe_register(
            ServerExecutor(
                kernel=self.kernel
            )
        )

        self._safe_register(
            DesktopExecutor(
                kernel=self.kernel
            )
        )

        self._safe_register(
            AndroidExecutor()
        )

    def _safe_register(
        self,
        executor,
    ) -> None:

        if executor.name in self.manager:
            return

        self.manager.register(
            executor
        )

    async def register_remote_executor(
        self,
        executor_id: str,
        executor_data: Dict[str, Any],
    ) -> RemoteExecutor:

        if executor_id in self.manager:
            existing = self.manager.require(
                executor_id
            )

            if isinstance(
                existing,
                RemoteExecutor,
            ):
                return existing

            raise ValueError(
                f"Executor name already in use: {executor_id}"
            )

        executor = RemoteExecutor(
            executor_id,
            executor_data,
        )

        self.manager.register(
            executor
        )

        if self._initialized:
            await executor.initialize()

        if self._running:
            await executor.start()

        logger.info(
            "Remote executor registered: {}",
            executor_id,
        )

        return executor

    async def unregister_executor(
        self,
        executor_id: str,
    ) -> None:

        executor = self.manager.unregister(
            executor_id
        )

        if executor:
            await executor.shutdown()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:

        if self._initialized:
            return

        await self.manager.initialize_all()

        self._initialized = True

        logger.info(
            "ExecutorScheduler initialized. Executors: {}",
            self.manager.names(),
        )

    async def start(self) -> None:

        if not self._initialized:
            await self.initialize()

        if self._running:
            return

        await self.manager.start_all()

        self._running = True

        logger.info(
            "ExecutorScheduler started"
        )

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    async def schedule_task(
        self,
        task: Dict[str, Any],
        executor_name: str,
    ) -> Dict[str, Any]:

        if not self._running:
            return {
                "status": "failed",
                "error": "ExecutorScheduler is not running",
                "task_id": task.get("id"),
            }

        if not executor_name:
            return {
                "status": "failed",
                "error": "executor_name is required",
                "task_id": task.get("id"),
            }

        executor = self.manager.get(
            executor_name
        )

        if executor is None:
            return {
                "status": "failed",
                "error": f"Executor not found: {executor_name}",
                "task_id": task.get("id"),
            }

        if not executor.running:
            return {
                "status": "failed",
                "error": f"Executor is not running: {executor_name}",
                "task_id": task.get("id"),
            }

        task_id = task.get("id")

        logger.info(
            "Scheduling task {} ({}) -> {}",
            task_id,
            task.get("type"),
            executor_name,
        )

        try:

            result = await executor.execute_task(
                task
            )

            # Executors already return a structured result.
            # Preserve it rather than wrapping it multiple times.
            return result

        except Exception as exc:

            logger.exception(
                "Executor failed: {}",
                executor_name,
            )

            return {
                "status": "failed",
                "executor": executor_name,
                "task_id": task_id,
                "error": str(exc),
            }

    async def execute(
        self,
        task: Dict[str, Any],
        executor_name: str,
    ) -> Dict[str, Any]:

        """
        Alias for schedule_task.

        Useful when orchestration code thinks in terms of execution
        rather than scheduling.
        """

        return await self.schedule_task(
            task,
            executor_name,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_executor(
        self,
        executor_name: str,
    ):

        return self.manager.get(
            executor_name
        )

    def list_executors(self):

        return [
            {
                "name": executor.name,
                "id": executor.executor_id,
                "running": executor.running,
                "initialized": executor.initialized,
                "type": executor.__class__.__name__,
            }
            for executor in self.manager.list()
        ]

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def stop(self) -> None:

        if not self._running:
            return

        await self.manager.stop_all()

        self._running = False

        logger.info(
            "ExecutorScheduler stopped"
        )

    async def shutdown(self) -> None:

        await self.manager.shutdown_all()

        self._running = False
        self._initialized = False

        logger.info(
            "ExecutorScheduler shutdown complete"
        )

    @property
    def running(self) -> bool:
        return self._running