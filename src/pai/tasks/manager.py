from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.event_bus.bus import EventBus
from pai.executors.manager import ExecutorManager
from pai.executors.scheduler import ExecutorScheduler
from pai.observability.tracer import get_tracer
from pai.tasks.lifecycle import InvalidTaskTransition, TaskLifecycle
from pai.tasks.models import (
    Task,
    TaskPriority,
    TaskResult,
    TaskSource,
    TaskStatus,
)
from pai.tasks.store import TaskStore
from pai.common.manager import Manager

class TaskManager(Manager):
    name = "task_manager"

    def __init__(
        self,
        *,
        store: TaskStore,
        scheduler: ExecutorScheduler,
        executor_manager: ExecutorManager,
        event_bus: EventBus,
        session_factory: Optional[Callable[[], AsyncSession]],
    ) -> None:
        super().__init__(session_factory=session_factory, event_bus=event_bus)
        self.store = store
        self.scheduler = scheduler
        self.executor_manager = executor_manager
        self.session_factory = session_factory
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._pause_events:  Dict[str, asyncio.Event] = {}   

    async def _on_initialize(self) -> None:
        if self.scheduler is None:
            raise RuntimeError("TaskManager has no ExecutorScheduler")
        await self.scheduler.initialize()


    async def _on_shutdown(self) -> None:
        running = list(self._running_tasks.values())
        for task in running:
            if not task.done():
                task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._running_tasks.clear()


    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            **await super().get_status(user_id=user_id),
            "running_tasks": list(self._running_tasks.keys()),
            "task_count": len(self._running_tasks),
        }


    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        title: str,
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
        device_id: Optional[str] = None,
        executor_id: Optional[str] = None,
        plugin: Optional[str] = None,
    ) -> Task:

        tracer = get_tracer()
        async with tracer.span(
            "task.create",
            task_type=task_type,
            user_id=user_id,
            device_id=device_id,
            executor_id=executor_id,
            plugin=plugin,
        ) as span:

            task = Task(
                title=title,
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
                device_id=device_id,
                executor_id=executor_id,
                plugin=plugin,
            )

            await self.store.create(task)
            await self._publish("task.created", task.to_dict())

            span.set_attribute("task_id", task.id)

            logger.info("Task created: {} ({})", task.id, task.type)
            return task

    # ------------------------------------------------------------------
    # Initialize
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

        tracer = get_tracer()
        async with tracer.span(
            "task.initialize",
            task_id=task_id,
            capability=capability,
            device_id=device_id,
            executor_id=executor_id,
        ):
            task = await self.store.require(task_id)

            if task.status != TaskStatus.CREATED:
                raise InvalidTaskTransition(
                    f"Task {task_id} is already in state {task.status.value}"
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
            await self._publish("task.initialized", task.to_dict())

            logger.info(
                "Task initialized: id={} capability={} device={} executor={}",
                task.id, task.capability, task.device_id, task.executor_id,
            )
            return task

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

        tracer = get_tracer()
        async with tracer.span(
            "task.create_initialized",
            task_type=task_type,
            capability=capability,
            device_id=device_id,
            executor_id=executor_id,
            user_id=user_id,
        ) as span:

            task = await self.create(
                title=f"Task for {task_type}",
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
                device_id=device_id,
                executor_id=executor_id,
            )

            task = await self.initialize_task(
                task.id,
                capability=capability,
                device_id=device_id,
                executor_id=executor_id,
            )

            span.set_attribute("task_id", task.id)
            return task

    # ------------------------------------------------------------------
    # Submit / execute
    # ------------------------------------------------------------------

    async def submit(
        self,
        task_id: str,
        *,
        wait: bool = True,
    ) -> Any:

        tracer = get_tracer()
        async with tracer.span(
            "task.submit",
            task_id=task_id,
            wait=wait,
        ) as span:

            task = await self.store.require(task_id)

            if task.status not in {TaskStatus.INITIALIZED, TaskStatus.RETRYING}:
                raise InvalidTaskTransition(
                    f"Task {task_id} cannot be submitted from "
                    f"state {task.status.value}"
                )

            if not task.executor_id:
                raise ValueError(f"Task {task_id} has no executor_id")

            if self.scheduler is None:
                raise RuntimeError("TaskManager has no ExecutorScheduler")

            TaskLifecycle.queue(task)
            await self.store.save(task)
            await self._publish("task.queued", task.to_dict())

            span.set_attribute("executor_id", task.executor_id)
            span.set_attribute("device_id", task.device_id)

            execution = asyncio.create_task(self._execute(task.id))
            self._running_tasks[task.id] = execution

            if not wait:
                return {
                    "task_id": task.id,
                    "status": task.status.value,
                }

            result = await execution
            return result

    async def _execute(self, task_id: str) -> TaskResult:

        tracer = get_tracer()
        # This runs inside the context captured at create_task time,
        # so spans nest under task.submit in the parent trace.
        async with tracer.span(
            "task.execute",
            task_id=task_id,
        ) as span:

            task = await self.store.require(task_id)
            
            # Pause event for this run. Set = running, cleared = paused.
            # Created *before* start() so a pause arriving the instant
            # we go RUNNING has an event to clear.
            pause_event = asyncio.Event()
            pause_event.set()
            self._pause_events[task_id] = pause_event

            span.set_attribute("executor_id", task.executor_id)
            span.set_attribute("device_id", task.device_id)
            span.set_attribute("capability", task.capability)

            try:
                TaskLifecycle.start(task)
                await self.store.save(task)
                await self._publish("task.started", task.to_dict())

                logger.info(
                    "Executing task {} using {}", task.id, task.executor_id,
                )

                # Checkpoint 1 — pause blocks the tool invocation entirely.
                await pause_event.wait()

                async with tracer.span(
                    "task.scheduler_call",
                    task_id=task.id,
                    executor=task.executor_id,
                ) as sched_span:
                    try:
                        if( task.executor_id is None ):
                            raise ValueError("Task has no executor")
                        
                        result = await asyncio.wait_for(

                            self.scheduler.schedule_task(
                                task=task,
                                executor_name=task.executor_id,
                            ),
                            timeout=task.timeout,
                        )
                        sched_span.set_attribute("result_type", type(result).__name__)
                        sched_span.set_attribute("result", str(result))
                        # ── Treat a failed result as a real failure ──
                        # The scheduler returns dicts like {"status": "failed",
                        # "error": "..."} for problems it can report without raising.
                        # Passing those through to TaskLifecycle.complete() would mark
                        # the task COMPLETED even though it never ran.
                        if isinstance(result, dict):
                            result_status = result.get("status")
                            if result_status in ("failed", "error", "cancelled"):
                                sched_span.set_attribute("success", False)
                                raise RuntimeError(
                                    result.get("error")
                                    or f"Scheduler returned status={result_status}"
                                )

                        sched_span.set_attribute("success", True)
                    except asyncio.TimeoutError:
                        sched_span.set_error(
                            f"timeout after {task.timeout}s"
                        )
                        raise
                    except Exception as exc:
                        sched_span.set_error(f"{type(exc).__name__}: {exc}")
                        raise
                # Checkpoint 2 — pause holds the result so the user can
                # inspect or cancel before the task flips to COMPLETED.
                await pause_event.wait()

                task_result = self._normalize_result(result, task)

                TaskLifecycle.complete(task, task_result)
                await self.store.save(task)
                await self._publish("task.completed", task.to_dict())

                logger.info("Task completed: {}", task.id)
                span.set_attribute("status", "completed")
                return task_result

            except asyncio.TimeoutError:
                span.set_attribute("status", "timeout")
                return await self._handle_failure(
                    task,
                    f"Task timed out after {task.timeout} seconds",
                )

            except asyncio.CancelledError:
                TaskLifecycle.cancel(task, "Task execution cancelled")
                await self.store.save(task)
                await self._publish("task.cancelled", task.to_dict())
                span.set_attribute("status", "cancelled")
                raise

            except Exception as exc:
                logger.exception("Task execution failed: {}", task.id)
                span.set_error(f"{type(exc).__name__}: {exc}")
                return await self._handle_failure(task, str(exc))

            finally:
                self._running_tasks.pop(task_id, None)
                self._pause_events.pop(task_id, None)

    async def _handle_failure(self, task: Task, error: str) -> TaskResult:

        tracer = get_tracer()
        async with tracer.span(
            "task.handle_failure",
            task_id=task.id,
            can_retry=task.can_retry,
        ) as span:

            result = TaskResult(
                success=False,
                error=error,
                executor_id=task.executor_id,
                device_id=task.device_id,
                started_at=task.started_at,
                completed_at=task.completed_at,
            )

            TaskLifecycle.fail(task, error, result)
            await self.store.save(task)
            await self._publish("task.failed", task.to_dict())

            if task.can_retry:
                logger.warning(
                    "Retrying task {} ({}/{})",
                    task.id, task.retry_count + 1, task.max_retries,
                )
                self._emit("task.retrying", {
                    "task_id": task.id,
                    "attempt": task.retry_count + 1,
                    "max_attempts": task.max_retries,
                    "error": error,
                    "user_id": task.user_id,
                    "session_id": task.session_id,
                })
                TaskLifecycle.retry(task)
                await self.store.save(task)
                span.set_attribute("action", "retry")

                return await self.submit(task.id, wait=True)

            span.set_attribute("action", "give_up")
            return result

    # ------------------------------------------------------------------
    # Cancellation / pause / resume
    # ------------------------------------------------------------------

    async def cancel(
        self,
        task_id: str,
        reason: Optional[str] = None,
    ) -> Task:

        tracer = get_tracer()
        async with tracer.span(
            "task.cancel",
            task_id=task_id,
            reason=reason,
        ):
            task = await self.store.require(task_id)

            # 1. Tell the device to stop (best-effort).
            if task.executor_id and self.executor_manager is not None:
                await self.executor_manager.cancel_task(task.executor_id, task_id)

            # 2. Cancel the wrapper — this unwinds wait_for cleanly.
            running = self._running_tasks.get(task_id)
            if running and not running.done():
                running.cancel()

            if not task.terminal:
                TaskLifecycle.cancel(task, reason or "Cancelled by user")
                await self.store.save(task)

            await self._publish("task.cancelled", task.to_dict())
            return task

    async def pause(self, task_id: str) -> Task:
        tracer = get_tracer()
        async with tracer.span("task.pause", task_id=task_id):
            task = await self.store.require(task_id)

            if not task.can_pause:
                raise InvalidTaskTransition(
                    f"Task {task_id} cannot be paused from "
                    f"state {task.status.value}"
                )

            # 1. Ask the executor. If it returns True, the device is now paused
            #    (e.g. camera.record freezes on the device itself).
            executor_paused = False
            if task.executor_id and self.executor_manager is not None:
                executor_paused = await self.executor_manager.pause_task(
                    task.executor_id, task_id,
                )

            # 2. Regardless of the executor's answer, gate the wrapper at the
            #    next checkpoint. Even if the executor said False (atomic op or
            #    protocol limitation), the *result* is still held.
            event = self._pause_events.get(task_id)
            if event is not None:
                event.clear()

            # 3. Flip status last so the state reflects reality only after
            #    both the executor and the wrapper have acknowledged.
            TaskLifecycle.pause(task)          # status → PAUSED
            await self.store.save(task)
            await self._publish("task.paused", task.to_dict())
            return task


    async def resume(self, task_id: str) -> Task:
        tracer = get_tracer()
        async with tracer.span("task.resume", task_id=task_id):
            task = await self.store.require(task_id)

            if not task.can_resume:
                raise InvalidTaskTransition(
                    f"Task {task_id} cannot be resumed from "
                    f"state {task.status.value}"
                )

            # Flip status *first* — the waking _execute coroutine must
            # observe RUNNING, not PAUSED, otherwise its subsequent
            # TaskLifecycle.complete() would fail with InvalidTaskTransition.
            TaskLifecycle.resume(task)          # status → RUNNING
            await self.store.save(task)

            event = self._pause_events.get(task_id)
            if event is not None:
                event.set()                     # wake the blocked coroutine

            await self._publish("task.resumed", task.to_dict())
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

    async def update_task(
        self,
        task_id: str,
        *,
        title: Optional[str] = None,
        input: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        task = await self.store.require(task_id)

        if title is not None:
            task.title = title
        if input is not None:
            task.input = input
        if parameters is not None:
            task.parameters.update(parameters)
        if metadata is not None:
            task.metadata.update(metadata)

        await self.store.save(task)
        return task

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_result(result: Any, task: Task) -> TaskResult:
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
                    k: v for k, v in result.items()
                    if k not in {"success", "status", "output", "error"}
                },
            )

        return TaskResult(
            success=True,
            output=result,
            executor_id=task.executor_id,
            device_id=task.device_id,
            started_at=task.started_at,
        )
