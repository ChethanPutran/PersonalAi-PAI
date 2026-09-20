from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.tasks.lifecycle import (
    InvalidTaskTransition,
    TaskLifecycle,
)
from pai.tasks.models import (
    Task,
    TaskPriority,
    TaskResult,
    TaskSource,
    TaskStatus,
)
from pai.tasks.store import TaskStore


class TaskManager:
    """
    High-level task management service.

    Responsibilities:
        - Create tasks from orchestrator output
        - Initialize execution metadata
        - Persist task state
        - Submit tasks to the executor scheduler
        - Track execution results
        - Handle retries
        - Cancel tasks

    It does NOT:
        - Select devices
        - Execute device operations
        - Implement browser/Android/desktop behavior

    Those responsibilities belong to orchestration and executors.
    """

    def __init__(
        self,
        *,
        store: Optional[TaskStore] = None,
        scheduler: Any = None,
        event_bus: Any = None,
    ) -> None:
        self.store = store or TaskStore()
        self.scheduler = scheduler
        self.event_bus = event_bus

        self._running_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        input: str,
        task_type: str,
        parameters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source: TaskSource = TaskSource.USER,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: float = 30.0,
        max_retries: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        parent_task_id: Optional[str] = None,
    ) -> Task:
        """
        Create a raw task.

        At this point the task has not yet been assigned to a device
        or executor.
        """

        task = Task(
            input=input,
            type=task_type,
            parameters=parameters or {},
            user_id=user_id,
            session_id=session_id,
            source=source,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            metadata=metadata or {},
            parent_task_id=parent_task_id,
        )

        await self.store.create(task)

        await self._publish(
            "task.created",
            task,
        )

        logger.info(
            "Task created: {} ({})",
            task.id,
            task.type,
        )

        return task

    # ------------------------------------------------------------------
    # Orchestrator initialization
    # ------------------------------------------------------------------

    async def initialize_task(
        self,
        task_id: str,
        *,
        capability: Optional[str] = None,
        device_id: Optional[str] = None,
        executor_id: Optional[str] = None,
        action: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Initialize a task after the orchestrator has resolved execution.

        This is where user intent becomes an executable task.

        Example:

            capability = "browser.navigate"
            device_id = "laptop"
            executor_id = "desktop_executor"
        """

        task = await self.store.require(task_id)

        if task.status != TaskStatus.CREATED:
            raise InvalidTaskTransition(
                f"Task {task_id} is already "
                f"in state {task.status.value}"
            )

        if action is not None:
            task.action = action

        if parameters:
            task.parameters.update(parameters)

        TaskLifecycle.initialize(
            task,
            capability=capability,
            device_id=device_id,
            executor_id=executor_id,
        )

        await self.store.save(task)

        await self._publish(
            "task.initialized",
            task,
        )

        logger.info(
            "Task initialized: id={} capability={} "
            "device={} executor={}",
            task.id,
            task.capability,
            task.device_id,
            task.executor_id,
        )

        return task

    # ------------------------------------------------------------------
    # Direct initialization helper
    # ------------------------------------------------------------------

    async def create_initialized(
        self,
        *,
        input: str,
        task_type: str,
        capability: str,
        device_id: Optional[str],
        executor_id: str,
        parameters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source: TaskSource = TaskSource.USER,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: float = 30.0,
        max_retries: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Convenience method for the orchestrator.

        Creates and initializes a task in one operation.
        """

        task = await self.create(
            input=input,
            task_type=task_type,
            parameters=parameters,
            user_id=user_id,
            session_id=session_id,
            source=source,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            metadata=metadata,
        )

        return await self.initialize_task(
            task.id,
            capability=capability,
            device_id=device_id,
            executor_id=executor_id,
        )

    # ------------------------------------------------------------------
    # Queue / execution
    # ------------------------------------------------------------------

    async def submit(
        self,
        task_id: str,
        *,
        wait: bool = True,
    ) -> Any:
        """
        Submit an initialized task to ExecutorScheduler.

        The task must already have an executor assigned.
        """

        task = await self.store.require(task_id)

        if task.status not in {
            TaskStatus.INITIALIZED,
            TaskStatus.RETRYING,
        }:
            raise InvalidTaskTransition(
                f"Task {task_id} cannot be submitted from "
                f"state {task.status.value}"
            )

        if not task.executor_id:
            raise ValueError(
                f"Task {task_id} has no executor_id"
            )

        if self.scheduler is None:
            raise RuntimeError(
                "TaskManager has no ExecutorScheduler"
            )

        TaskLifecycle.queue(task)
        await self.store.save(task)

        await self._publish(
            "task.queued",
            task,
        )

        execution = asyncio.create_task(
            self._execute(task.id)
        )

        self._running_tasks[task.id] = execution

        if not wait:
            return {
                "task_id": task.id,
                "status": task.status.value,
            }

        return await execution

    async def _execute(self, task_id: str) -> TaskResult:
        task = await self.store.require(task_id)

        try:
            TaskLifecycle.start(task)
            await self.store.save(task)

            await self._publish(
                "task.started",
                task,
            )

            logger.info(
                "Executing task {} using {}",
                task.id,
                task.executor_id,
            )

            result = await asyncio.wait_for(
                self.scheduler.schedule_task(
                    task=task,
                    executor_name=task.executor_id,
                ),
                timeout=task.timeout,
            )

            task_result = self._normalize_result(
                result,
                task,
            )

            TaskLifecycle.complete(
                task,
                task_result,
            )

            await self.store.save(task)

            await self._publish(
                "task.completed",
                task,
            )

            logger.info(
                "Task completed: {}",
                task.id,
            )

            return task_result

        except asyncio.TimeoutError:
            return await self._handle_failure(
                task,
                f"Task timed out after {task.timeout} seconds",
            )

        except asyncio.CancelledError:
            TaskLifecycle.cancel(
                task,
                "Task execution cancelled",
            )

            await self.store.save(task)

            await self._publish(
                "task.cancelled",
                task,
            )

            raise

        except Exception as exc:
            logger.exception(
                "Task execution failed: {}",
                task.id,
            )

            return await self._handle_failure(
                task,
                str(exc),
            )

        finally:
            self._running_tasks.pop(task_id, None)

    async def _handle_failure(
        self,
        task: Task,
        error: str,
    ) -> TaskResult:
        result = TaskResult(
            success=False,
            error=error,
            executor_id=task.executor_id,
            device_id=task.device_id,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

        TaskLifecycle.fail(
            task,
            error,
            result,
        )

        await self.store.save(task)

        await self._publish(
            "task.failed",
            task,
        )

        # Retry only after the failed state has been persisted.
        if task.can_retry:
            logger.warning(
                "Retrying task {} ({}/{})",
                task.id,
                task.retry_count + 1,
                task.max_retries,
            )

            TaskLifecycle.retry(task)
            await self.store.save(task)

            return await self.submit(
                task.id,
                wait=True,
            )

        return result

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel(
        self,
        task_id: str,
        reason: Optional[str] = None,
    ) -> Task:
        task = await self.store.require(task_id)

        running = self._running_tasks.get(task_id)

        if running and not running.done():
            running.cancel()

        if not task.terminal:
            TaskLifecycle.cancel(
                task,
                reason or "Cancelled by user",
            )

            await self.store.save(task)

        await self._publish(
            "task.cancelled",
            task,
        )

        return task

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get(self, task_id: str) -> Task:
        return await self.store.require(task_id)

    async def list(
        self,
        *,
        status: Optional[TaskStatus] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Task]:
        return await self.store.list(
            status=status,
            user_id=user_id,
            session_id=session_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_result(
        result: Any,
        task: Task,
    ) -> TaskResult:
        if isinstance(result, TaskResult):
            return result

        if isinstance(result, dict):
            success = result.get(
                "success",
                result.get("status") not in {"error", "failed"},
            )

            return TaskResult(
                success=bool(success),
                output=result.get("output", result),
                error=result.get("error"),
                executor_id=task.executor_id,
                device_id=task.device_id,
                started_at=task.started_at,
                completed_at=None,
                metadata={
                    k: v
                    for k, v in result.items()
                    if k not in {
                        "success",
                        "status",
                        "output",
                        "error",
                    }
                },
            )

        return TaskResult(
            success=True,
            output=result,
            executor_id=task.executor_id,
            device_id=task.device_id,
            started_at=task.started_at,
        )

    async def _publish(
        self,
        event_type: str,
        task: Task,
    ) -> None:
        if self.event_bus is None:
            return

        try:
            event = {
                "type": event_type,
                "task_id": task.id,
                "status": task.status.value,
                "user_id": task.user_id,
                "session_id": task.session_id,
            }

            publish = getattr(
                self.event_bus,
                "publish",
                None,
            )

            if publish is not None:
                await publish(
                    event_type,
                    event,
                )

        except Exception:
            # Event publishing must never break task execution.
            logger.exception(
                "Failed to publish task event: {}",
                event_type,
            )

    async def shutdown(self) -> None:
        """
        Cancel active task coroutines.

        Executor shutdown is handled by ExecutorManager.
        """

        running = list(
            self._running_tasks.values()
        )

        for task in running:
            if not task.done():
                task.cancel()

        if running:
            await asyncio.gather(
                *running,
                return_exceptions=True,
            )

        self._running_tasks.clear()