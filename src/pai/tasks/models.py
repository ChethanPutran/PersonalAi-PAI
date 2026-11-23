from __future__ import annotations
import asyncio
from enum import Enum

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pai.common.enums.permission import Origin
from pai.security.authorization.context import ScopeContext
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pai.storage.models import ScheduleModel


ScheduleKind = Literal["one_shot", "cron", "interval"]
CatchUp = Literal["none", "once", "all"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _json_safe(value):
    """Recursively convert datetimes (and other non-JSON types) to strings."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value



# ---------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------

class ScheduleBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: Optional[str] = None
    kind: ScheduleKind
    fire_at: Optional[datetime] = None
    cron: Optional[str] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10)
    timezone: str = "UTC"
    capability: str = "system.notifications.show"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    catch_up: CatchUp = "once"
    target_device_id: Optional[str] = None

    @field_validator("timezone")
    @classmethod
    def _tz_valid(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {v}") from exc
        return v

    def model_post_init(self, __context) -> None:
        if self.kind == "one_shot" and self.fire_at is None:
            raise ValueError("one_shot requires fire_at")
        if self.kind == "cron" and not self.cron:
            raise ValueError("cron requires cron expression")
        if self.kind == "interval" and not self.interval_seconds:
            raise ValueError("interval requires interval_seconds")


class ScheduleCreate(ScheduleBase):
    pass


class ScheduleUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    body: Optional[str] = None
    kind: Optional[ScheduleKind] = None
    fire_at: Optional[datetime] = None
    cron: Optional[str] = None
    interval_seconds: Optional[int] = Field(default=None, ge=10)
    timezone: Optional[str] = None
    capability: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    catch_up: Optional[CatchUp] = None
    target_device_id: Optional[str] = None
    enabled: Optional[bool] = None


class ScheduleOut(BaseModel):
    id: str
    user_id: str
    target_device_id: Optional[str]
    title: str
    body: Optional[str]
    kind: str
    fire_at: Optional[str]
    cron: Optional[str]
    interval_seconds: Optional[int]
    timezone: str
    capability: str
    parameters: Dict[str, Any]
    catch_up: str
    enabled: bool
    last_fired_at: Optional[str]
    next_fire_at: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: ScheduleModel) -> "ScheduleOut":
        def iso(v):
            if v is None:
                return None
            # SQLAlchemy `DateTime(timezone=True)` gives back tz-aware
            # datetimes on most backends, but sqlite (and some Postgres
            # configs) return naive. Treat naive as UTC — that's how we
            # wrote it — and re-attach the tzinfo so isoformat() includes
            # the offset.
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()

        return cls(
            id=row.id,
            user_id=row.user_id,
            target_device_id=row.target_device_id,
            title=row.title,
            body=row.body,
            kind=row.kind,
            fire_at=iso(row.fire_at),
            cron=row.cron,
            interval_seconds=row.interval_seconds,
            timezone=row.timezone,
            capability=row.capability,
            parameters=row.parameters or {},
            catch_up=row.catch_up,
            enabled=row.enabled,
            last_fired_at=iso(row.last_fired_at),
            next_fire_at=iso(row.next_fire_at),
            created_at=iso(row.created_at),
            updated_at=iso(row.updated_at),
        )

    
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
    PENDING = "pending"


class TaskType(str, Enum):
    ONE_SHOT = "one_shot"
    LONG_RUNNING = "long_running"
    STREAM = "stream"
    
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
    task_id: str

    model_config = ConfigDict(extra="allow")

    success: bool = False
    output: Any = None
    error: Optional[str] = None

    executor_id: Optional[str] = None
    device_id: Optional[str] = None

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

def get_task_timeout(task: Task) -> float:
    """
    Determine the timeout for a task based on its type.
    """
    if task.type == TaskType.LONG_RUNNING:
        return float("inf")  # No timeout for long-running tasks
    elif task.type == TaskType.STREAM:
        return float("inf")  # No timeout for streaming tasks
    else:
        return task.timeout  # Use the specified timeout for one-shot tasks

class TaskRetryPolicy(BaseModel):
    """How failed tasks are retried (exponential backoff, capped)."""
    default_max_retries: int = 3        # applied when the plan doesn't say
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        return min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt - 1)))


class TaskRetryingEvent(BaseModel):
    """Realtime payload: a failed attempt is being retried (not a final failure)."""
    task_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    attempt: int
    max_attempts: int
    delay_seconds: float = 0.0
    error: Optional[str] = None


class TaskStreamEvent(BaseModel):
    """Realtime payload for a streaming task's start / frame / end."""
    task_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    kind: Literal["start", "frame", "end"]
    sequence: Optional[int] = None
    data: Any = None


class TaskAuthorization(BaseModel):
    """Everything needed to (re-)authorize a task and prove it was authorized."""
    required: bool = False   # executor must be handed valid tokens
    pending: bool = False    # authorize() returned APPROVAL_REQUIRED
    tokens: List[str] = Field(default_factory=list, exclude=True)

    # Trusted facts replayed on re-authorization.
    agent_id: Optional[str] = None
    origin: Origin = Origin.AGENT_AUTONOMOUS
    source_device_id: Optional[str] = None
    scope: ScopeContext = Field(default_factory=ScopeContext)


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
    name: str

    # Human-level information
    user_id: Optional[str] = None
    session_id: Optional[str] = None

    source: TaskSource = TaskSource.USER

    # Normalized task representation
    type: TaskType = Field(default=TaskType.ONE_SHOT)
    action: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # Execution routing
    plugin: Optional[str] = None
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

    requires_confirmation: bool = False  # Whether the task requires user confirmation before execution
    confirmation_reason: str = ""  # The reason for requiring confirmation

    # Authorization state (typed; tokens are runtime-only and never serialized).
    authorization: "TaskAuthorization" = Field(default_factory=lambda: TaskAuthorization())

    # Times this task was re-queued because the server stopped under it (not the task's fault,
    # so it does not consume the retry budget).
    recovery_count: int = 0

    # From the capability manifest. False: never auto-retried, never auto-re-sent after an
    # interruption (the action may already have happened); manual retry still works.
    retryable: bool = True

    # The planner's id for this task (what DAG depends_on refers to).
    spec_id: Optional[str] = None

    # Stable across retries/recovery so a device can de-duplicate re-sent work.
    idempotency_key: Optional[str] = None

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

    def mark_interrupted(self) -> None:
        """The server stopped while this was in flight: make it runnable again.

        Deliberately outside the normal state machine and not counted as a retry.
        """
        self.status = TaskStatus.INITIALIZED
        self.recovery_count += 1
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

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    async def wait_for_completion(self, timeout: Optional[float] = None) -> TaskResult:
        """
        Wait for the task to reach a terminal state (COMPLETED, FAILED, or CANCELLED).
        If the task is already in a terminal state, return immediately.

        Args:
            timeout: Optional timeout in seconds. If None, wait indefinitely.

        Returns:
            The TaskResult if the task completed successfully.

        Raises:
            asyncio.TimeoutError: If the timeout is reached before the task completes.
            RuntimeError: If the task fails or is cancelled.
        """
        if self.terminal:
            if self.status == TaskStatus.COMPLETED:
                return self.result
            else:
                raise RuntimeError(f"Task {self.id} did not complete successfully: {self.error}")

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def check_terminal():
            if self.terminal:
                if self.status == TaskStatus.COMPLETED:
                    future.set_result(self.result)
                else:
                    future.set_exception(RuntimeError(f"Task {self.id} did not complete successfully: {self.error}"))

        # Register a callback to check for terminal state
        # This assumes there's some mechanism to notify when the task state changes
        # For example, an event bus or observer pattern could be used here
        # For simplicity, we'll just poll in this example
        async def poll_task():
            while not self.terminal:
                await asyncio.sleep(0.1)
            check_terminal()

        asyncio.create_task(poll_task())

        return await asyncio.wait_for(future, timeout=timeout)
