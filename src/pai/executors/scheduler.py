"""
Task scheduler for executors.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from loguru import logger

from pai.executors.device import DeviceExecutor
from pai.executors.manager import ExecutorManager
from pai.executors.server import ServerExecutor
from pai.tasks.models import Task, TaskResult
from pai.observability.tracer import get_tracer

class SchedulerError(Exception):
    pass

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
        self.queue: List[Tuple[Task,str]] = []  

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
        task: Task,                             
        executor_id: str,
    ) -> TaskResult:
        tracer = get_tracer()

        task_id = task.id
        task_type = task.type
        capability = task.capability

        async with tracer.span(
            "executor_scheduler.schedule_task",
            task_id=task_id,
            task_type=task_type,
            capability=capability,
            executor_id=executor_id,
        ) as span:

            if not self._running:
                span.set_error("scheduler_not_running")
                raise RuntimeError("ExecutorScheduler is not running")

            if not executor_id:
                span.set_error("missing_executor_id")
                raise SchedulerError(f"Missing executor_id for task {task_id} ({task_type})")

            executor = self.manager.get(executor_id)
            if executor is None:
                span.set_error(f"executor_not_found:{executor_id}")
                raise SchedulerError(
                    f"Executor not found: {executor_id}"
                )

            if not executor.running:
                span.set_error(f"executor_not_running:{executor_id}")
                raise SchedulerError(f"Executor is not running: {executor_id}")

            if not executor.is_available():
                raise RuntimeError(f"Executor is busy: {executor_id}")

            logger.info(
                        "Scheduling task {} ({}) -> {}",
                        task_id, task_type, executor_id,
                        )

            # This awaits the actual execution, not just dispatch.
            result = await executor.execute_task(task)

            return self._as_task_result(task, executor_id, result)

    @staticmethod
    def _as_task_result(task: Task, executor_id: str, result: Any) -> TaskResult:
        """Executors historically return either a TaskResult or a status dict
        ({"status": "success"|"completed"|"failed", "output"/"result", "error"}).
        TaskManager only understands TaskResult, so normalize at this boundary."""
        if isinstance(result, TaskResult):
            return result
        data = result if isinstance(result, dict) else {"output": result, "status": "success"}
        ok = str(data.get("status", "success")).lower() in {"success", "completed"}
        return TaskResult(
            task_id=task.id,
            success=ok,
            output=data.get("output", data.get("result")),
            error=None if ok else str(data.get("error") or "Execution failed"),
            executor_id=executor_id,
            device_id=task.device_id,
        )

           
    def queue_task(self, task: Any, executor_id: str) -> None:
        """
        Queue a task for later execution.
        """
        self.queue.append((task, executor_id))
        logger.info("Task {} queued for executor {}", task.id, executor_id)


    async def run_queued_tasks(self) -> None:
        """
        Run all queued tasks.
        """
        while True:
            while self.queue:
                task, executor_id = self.queue.pop(0)
                logger.info("Running queued task {} for executor {}", task.id, executor_id)
                # Here you would typically call schedule_task or execute_task
                # For simplicity, we just log the action
                # In a real implementation, you might want to await schedule_task

                # try to check if the executor is available
                # If executor is available, schedule the task; otherwise, re-queue it
                executor = self.manager.get(executor_id)
                if executor and executor.is_available():
                    async with get_tracer().span(
                        "executor_scheduler.execute_task",
                        task_id=task.id,
                        executor_id=executor_id,
                    ) as span:
                        t0 = time.perf_counter()
                        try:
                            task.mark_running()
                            result = await executor.execute_task(task)
                            task.mark_completed(result=result)

                            logger.info(
                                "Task {} executed by executor {}: {}",
                                task.id, executor_id, result
                            )

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
            
                        except Exception as exc:
                            duration_ms = (time.perf_counter() - t0) * 1000
                            logger.exception("Executor failed: {}", executor_id)
                            span.set_error(f"{type(exc).__name__}: {exc}")
                            span.set_attribute("duration_ms", round(duration_ms, 1))
                            task.mark_failed(error=str(exc))  # Mark the task as failed
                    
                else:
                    self.queue.append((task, executor_id))
                    logger.info("Re-queuing task {} for executor {}", task.id, executor_id)

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

        # TODO: Consider whether we should wait for in-flight tasks to complete or cancel them.
        tracer = get_tracer()
        async with tracer.span("executor_scheduler.stop"):
            await self.manager.stop_all()
            self._running = False
            logger.info("ExecutorScheduler stopped")

    async def shutdown(self) -> None:
        # TODO: Save state of queued tasks to disk or database for recovery on restart
        tracer = get_tracer()
        async with tracer.span("executor_scheduler.shutdown"):
            await self.manager.shutdown_all()
            self._running = False
            self._initialized = False
            logger.info("ExecutorScheduler shutdown complete")

    async def save_state(self) -> Dict[str, Any]:
        """
        Save the state of the scheduler, including queued tasks.
        """
        # TODO: Implement actual persistence logic (e.g., save to a database or file)
        with get_tracer().span("executor_scheduler.save_state") as span:
            span.set_attribute("queue_length", len(self.queue))
            return {
                "initialized": self._initialized,
                "running": self._running,
                "queue": [
                    {
                        "task": self._as_dict(task),
                        "executor_id": executor_id,
                    }
                    for task, executor_id in self.queue
                ],
            }
    
    @property
    def running(self) -> bool:
        return self._running