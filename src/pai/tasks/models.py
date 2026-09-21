from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    CREATED = "created"
    INITIALIZED = "initialized"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskSource(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    PLUGIN = "plugin"
    SCHEDULED = "scheduled"


class TaskResult(BaseModel):
    """
    Result produced by an executor.

    Executors operate devices/resources.
    TaskResult stores the normalized outcome of that execution.
    """

    model_config = ConfigDict(extra="allow")

    success: bool
    output: Any = None
    error: Optional[str] = None

    executor_id: Optional[str] = None
    device_id: Optional[str] = None

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    """
    Canonical task representation flowing through the system.

    Lifecycle:

        CREATED
          ↓
        INITIALIZED
          ↓
        QUEUED
          ↓
        RUNNING
          ↓
      COMPLETED / FAILED / CANCELLED

    The orchestrator is responsible for initializing execution-specific
    fields such as capability, device and executor selection.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str

    # Human-level information
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    source: TaskSource = TaskSource.USER
    input: str = ""

    # Normalized task representation
    type: str
    action: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # Execution routing
    capability: Optional[str] = None
    device_id: Optional[str] = None
    executor_id: Optional[str] = None

    # Lifecycle
    status: TaskStatus = TaskStatus.CREATED
    priority: TaskPriority = TaskPriority.NORMAL

    # Execution controls
    timeout: float = 30.0
    max_retries: int = 0
    retry_count: int = 0

    # Timestamps
    created_at: datetime = Field(default_factory=utc_now)
    initialized_at: Optional[datetime] = None
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Result/error
    result: Optional[TaskResult] = None
    error: Optional[str] = None

    # Arbitrary metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Planning relationship
    parent_task_id: Optional[str] = None
    child_task_ids: List[str] = Field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

    @property
    def can_retry(self) -> bool:
        return (
            self.status == TaskStatus.FAILED
            and self.retry_count < self.max_retries
        )
    @property
    def can_cancel(self) -> bool:
        return not self.terminal


    @property
    def can_resume(self) -> bool:
        return self.status == TaskStatus.PAUSED
    
    @property
    def can_pause(self) -> bool:
        return self.status == TaskStatus.RUNNING

    def mark_initialized(
        self,
        *,
        capability: Optional[str] = None,
        device_id: Optional[str] = None,
        executor_id: Optional[str] = None,
    ) -> None:
        self.status = TaskStatus.INITIALIZED
        self.initialized_at = utc_now()

        if capability is not None:
            self.capability = capability

        if device_id is not None:
            self.device_id = device_id

        if executor_id is not None:
            self.executor_id = executor_id

    def mark_queued(self) -> None:
        self.status = TaskStatus.QUEUED
        self.queued_at = utc_now()

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = utc_now()

    def mark_completed(
        self,
        result: Optional[TaskResult] = None,
    ) -> None:
        self.status = TaskStatus.COMPLETED
        self.completed_at = utc_now()
        self.error = None

        if result is not None:
            self.result = result

    def mark_paused(self) -> None:
        self.status = TaskStatus.PAUSED
        self.error = None

    def mark_failed(
        self,
        error: str,
        result: Optional[TaskResult] = None,
    ) -> None:
        self.status = TaskStatus.FAILED
        self.completed_at = utc_now()
        self.error = error

        if result is not None:
            self.result = result

    def mark_cancelled(self, reason: Optional[str] = None) -> None:
        self.status = TaskStatus.CANCELLED
        self.completed_at = utc_now()
        self.error = reason

    def mark_retrying(self) -> None:
        self.retry_count += 1
        self.status = TaskStatus.RETRYING
        self.error = None
        self.result = None
        self.started_at = None
        self.completed_at = None

    def add_child(self, task_id: str) -> None:
        if task_id not in self.child_task_ids:
            self.child_task_ids.append(task_id)

    def reset(self) -> None:
        """Reset the task to its initial state for retrying."""
        self.status = TaskStatus.CREATED
        self.retry_count = 0
        self.error = None
        self.result = None
        self.started_at = None
        self.completed_at = None