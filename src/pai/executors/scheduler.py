"""
Task scheduler for executors.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from loguru import logger

from pai.executors.device import DeviceExecutor
from pai.executors.manager import ExecutorManager
from pai.executors.server import ServerExecutor
from pai.observability.tracer import get_tracer


class ExecutorScheduler:
    """
        Routes tasks to registered executors.
    
        The scheduler does not know how an executor performs a task.
        It only selects the executor and invokes its common interface.
        """

    def __init__(
        self,
        llm_service: Any,
        plugin_manager: Any,
        *,
        manager: ExecutorManager,
        device_manager: Any,
        register_defaults: bool = True,
    ) -> None:
        self.llm_service = llm_service
        self.plugin_manager = plugin_manager
        self.manager = manager
        self.device_manager = device_manager
        self._initialized = False
        self._running = False

        if register_defaults:
            self._register_default_executors()

    # ------------------------------------------------------------------
    # BUG FIX: normalize task inputs (TaskManager passes Task model, not dict)
    # ------------------------------------------------------------------

    @staticmethod
    def _as_dict(task: Any) -> Dict[str, Any]:
        """Accept dicts, Task models, or anything with to_dict/model_dump."""
        if task is None:
            return {}
        if isinstance(task, dict):
            return task
        if hasattr(task, "model_dump"):
            return task.model_dump()
        if hasattr(task, "to_dict"):
            return task.to_dict()
        if hasattr(task, "__dict__"):
            return dict(task.__dict__)
        return {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _register_default_executors(self) -> None:
        self._safe_register(ServerExecutor(self.device_manager,llm_service=self.llm_service))
        self._safe_register(DeviceExecutor(device_manager=self.device_manager))

    def _safe_register(self, executor) -> None:
        if executor.name in self.manager:
            return
        self.manager.register(executor)

   
    async def unregister_executor(self, executor_id: str) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "executor_scheduler.unregister",
            executor_id=executor_id,
        ):
            executor = self.manager.unregister(executor_id)
            if executor:
                await executor.shutdown()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        if self._initialized:
            return
        tracer = get_tracer()
        async with tracer.span("executor_scheduler.initialize") as span:
            await self.manager.initialize()
            self._initialized = True
            span.set_attribute("executors", self.manager.names())
            logger.info(
                "ExecutorScheduler initialized. Executors: {}",
                self.manager.names(),
            )
        await self.start()  # Start the scheduler after initialization

    def is_initialized(self) -> bool:
        return self._initialized

    async def start(self) -> None:
        if not self.is_initialized():
            raise RuntimeError("ExecutorScheduler must be initialized before starting")
        if self._running:
            return
        tracer = get_tracer()
        async with tracer.span("executor_scheduler.start"):
            await self.manager.start_all()
            self._running = True
            logger.info("ExecutorScheduler started")

    # ------------------------------------------------------------------
    # Scheduling — primary entry point
    # ------------------------------------------------------------------

    async def schedule_task(
        self,
        task: Any,                              # ← widened from Dict to Any
        executor_name: str,
    ) -> Dict[str, Any]:
        tracer = get_tracer()

        # Normalize input regardless of whether a dict or Task model is passed
        task_dict = self._as_dict(task)
        task_id = task_dict.get("id")
        task_type = task_dict.get("type")
        capability = task_dict.get("capability") or task_type

        async with tracer.span(
            "executor_scheduler.schedule_task",
            task_id=task_id,
            task_type=task_type,
            capability=capability,
            executor_name=executor_name,
        ) as span:

            if not self._running:
                span.set_error("scheduler_not_running")
                return {
                    "status": "failed",
                    "error": "ExecutorScheduler is not running",
                    "task_id": task_id,
                }

            if not executor_name:
                span.set_error("missing_executor_name")
                return {
                    "status": "failed",
                    "error": "executor_name is required",
                    "task_id": task_id,
                }

            executor = self.manager.get(executor_name)
            if executor is None:
                span.set_error(f"executor_not_found:{executor_name}")
                return {
                    "status": "failed",
                    "error": f"Executor not found: {executor_name}",
                    "task_id": task_id,
                }

            if not executor.running:
                span.set_error(f"executor_not_running:{executor_name}")
                return {
                    "status": "failed",
                    "error": f"Executor is not running: {executor_name}",
                    "task_id": task_id,
                }

            logger.info(
                "Scheduling task {} ({}) -> {}",
                task_id, task_type, executor_name,
            )

            t0 = time.perf_counter()
            try:
                result = await executor.execute_task(task)

                # Executors already return a structured result.
                # Preserve it rather than wrapping it multiple times.
                duration_ms = (time.perf_counter() - t0) * 1000

                span.set_attribute("duration_ms", round(duration_ms, 1))
                span.set_attribute(
                    "status",
                    (result or {}).get("status", "completed")
                    if isinstance(result, dict)
                    else "completed",
                )
                return result

            except Exception as exc:
                duration_ms = (time.perf_counter() - t0) * 1000
                logger.exception("Executor failed: {}", executor_name)
                span.set_error(f"{type(exc).__name__}: {exc}")
                span.set_attribute("duration_ms", round(duration_ms, 1))
                return {
                    "status": "failed",
                    "executor": executor_name,
                    "task_id": task_id,
                    "error": str(exc),
                }

    async def execute(
        self,
        task: Any,
        executor_name: str,
    ) -> Dict[str, Any]:
        """
                Alias for schedule_task.
        
                Useful when orchestration code thinks in terms of execution
                rather than scheduling.
                """
        return await self.schedule_task(task, executor_name)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_executor(self, executor_name: str):
        return self.manager.get(executor_name)

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
        tracer = get_tracer()
        async with tracer.span("executor_scheduler.stop"):
            await self.manager.stop_all()
            self._running = False
            logger.info("ExecutorScheduler stopped")

    async def shutdown(self) -> None:
        tracer = get_tracer()
        async with tracer.span("executor_scheduler.shutdown"):
            await self.manager.shutdown_all()
            self._running = False
            self._initialized = False
            logger.info("ExecutorScheduler shutdown complete")

    @property
    def running(self) -> bool:
        return self._running