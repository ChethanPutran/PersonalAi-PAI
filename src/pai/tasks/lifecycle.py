from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger

from pai.tasks.models import Task, TaskStatus, TaskResult


class InvalidTaskTransition(Exception):
    """Raised when a task lifecycle transition is invalid."""

    code = "invalid_state"


class TaskControlError(InvalidTaskTransition):
    """A pause/resume/cancel was refused (HTTP 409).

    ``code``: ``invalid_state`` (wrong status), ``control_not_supported`` (the capability
    does not offer it), ``control_not_acknowledged`` (the device did not confirm; the task
    state was NOT changed).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TaskLifecycle:
    """
    Controls valid task state transitions.

    The lifecycle does not execute the task.
    It only controls state and records execution information.
    """

    _TRANSITIONS = {
        TaskStatus.CREATED: {
            TaskStatus.INITIALIZED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.INITIALIZED: {
            TaskStatus.QUEUED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.QUEUED: {
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,          # held before it starts
            TaskStatus.CANCELLED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.PAUSED,
        },
        TaskStatus.PAUSED: {
            TaskStatus.RUNNING,
            TaskStatus.QUEUED,          # resume of a pause taken while queued
            TaskStatus.RETRYING,        # resume of a pause taken while waiting to retry
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.FAILED: {
            TaskStatus.RETRYING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RETRYING: {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,          # held while waiting to retry
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: set(),
        TaskStatus.CANCELLED: set(),
    }

    @classmethod
    def can_transition(
        cls,
        current: TaskStatus,
        target: TaskStatus,
    ) -> bool:
        return target in cls._TRANSITIONS.get(current, set())

    @classmethod
    def transition(
        cls,
        task: Task,
        target: TaskStatus,
        *,
        error: Optional[str] = None,
        result: Optional[TaskResult] = None,
    ) -> Task:
        current = task.status

        if not cls.can_transition(current, target):
            raise InvalidTaskTransition(
                f"Invalid task transition: "
                f"{current.value} -> {target.value} "
                f"(task={task.id})"
            )

        if target == TaskStatus.INITIALIZED:
            task.mark_initialized()

        elif target == TaskStatus.QUEUED:
            task.mark_queued()

        elif target == TaskStatus.RUNNING:
            task.mark_running()

        elif target == TaskStatus.COMPLETED:
            task.mark_completed(result)

        elif target == TaskStatus.FAILED:
            task.mark_failed(
                error or "Task execution failed",
                result,
            )
        elif target == TaskStatus.PAUSED:             
            task.mark_paused()

        elif target == TaskStatus.CANCELLED:
            task.mark_cancelled(error)

        elif target == TaskStatus.RETRYING:
            task.mark_retrying()

        else:
            raise InvalidTaskTransition(
                f"Unsupported transition target: {target}"
            )

        logger.debug(
            "Task {} transitioned: {} -> {}",
            task.id,
            current.value,
            target.value,
        )

        return task

    @classmethod
    def initialize(
        cls,
        task: Task,
        *,
        capability: Optional[str] = None,
        device_id: Optional[str] = None,
        executor_id: Optional[str] = None,
    ) -> Task:
        if task.status != TaskStatus.CREATED:
            raise InvalidTaskTransition(
                f"Task {task.id} cannot be initialized "
                f"from state {task.status.value}"
            )

        task.mark_initialized(
            capability=capability,
            device_id=device_id,
            executor_id=executor_id,
        )

        return task

    @classmethod
    def queue(cls, task: Task) -> Task:
        return cls.transition(task, TaskStatus.QUEUED)

    @classmethod
    def start(cls, task: Task) -> Task:
        return cls.transition(task, TaskStatus.RUNNING)

    @classmethod
    def resume(cls, task: Task) -> Task:
        """PAUSED -> wherever the pause was taken (RUNNING, QUEUED or RETRYING).

        Set directly (not via mark_running/mark_retrying): resuming must not reset
        ``started_at`` or count as another retry.
        """
        if task.status != TaskStatus.PAUSED:
            raise InvalidTaskTransition(
                f"Task {task.id} cannot be resumed "
                f"from state {task.status.value}"
            )
        target = task.paused_from or TaskStatus.RUNNING
        if not cls.can_transition(TaskStatus.PAUSED, target):
            raise InvalidTaskTransition(f"Task {task.id} cannot be resumed to {target.value}")
        task.status = target
        task.paused_from = None
        return task

    @classmethod
    def complete(
        cls,
        task: Task,
        result: Optional[TaskResult] = None,
    ) -> Task:
        return cls.transition(
            task,
            TaskStatus.COMPLETED,
            result=result,
        )

    @classmethod
    def fail(
        cls,
        task: Task,
        error: str,
        result: Optional[TaskResult] = None,
    ) -> Task:
        return cls.transition(
            task,
            TaskStatus.FAILED,
            error=error,
            result=result,
        )

    @classmethod
    def cancel(
        cls,
        task: Task,
        reason: Optional[str] = None,
    ) -> Task:
        if task.terminal:
            return task

        return cls.transition(
            task,
            TaskStatus.CANCELLED,
            error=reason,
        )

    @classmethod
    def retry(cls, task: Task) -> Task:
        if not task.can_retry:
            raise InvalidTaskTransition(
                f"Task {task.id} cannot be retried"
            )

        return cls.transition(task, TaskStatus.RETRYING)

    @classmethod
    def reset(cls, task: Task) -> Task:
        if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise InvalidTaskTransition(
                f"Task {task.id} cannot be reset from state {task.status.value}"
            )

        task.reset()
        return task

    @classmethod
    def pause(cls, task: Task) -> Task:
        came_from = task.status
        cls.transition(task, TaskStatus.PAUSED)
        task.paused_from = came_from          # so resume goes back to the right place
        return task
