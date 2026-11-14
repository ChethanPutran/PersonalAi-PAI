from __future__ import annotations
from enum import Enum

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
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

    def to_dict(self) -> Dict[str, Any]:
        def _iso(dt):
            return dt.isoformat() if isinstance(dt, datetime) else dt

        return {
            "success": self.success,
            "output": _json_safe(self.output),
            "metadata": _json_safe(self.metadata),
            "error": self.error,
            "executor_id": self.executor_id,
            "device_id": self.device_id,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at)
        }


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

    def to_dict(self) -> Dict[str, Any]:
        def _iso(dt):
            return dt.isoformat() if isinstance(dt, datetime) else dt

        def _enum(v):
            return v.value if isinstance(v, Enum) else v

        return {
            "id": self.id,
            "title": self.title,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "source": _enum(self.source),
            "input": self.input,
            "type": self.type,
            "action": self.action,
            "parameters": _json_safe(self.parameters),
            "plugin": self.plugin,
            "capability": self.capability,
            "device_id": self.device_id,
            "executor_id": self.executor_id,
            "status": _enum(self.status),
            "priority": _enum(self.priority),
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "created_at": _iso(self.created_at),
            "initialized_at": _iso(self.initialized_at),
            "queued_at": _iso(self.queued_at),
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "metadata": _json_safe(self.metadata),
            "parent_task_id": self.parent_task_id,
            "child_task_ids": list(self.child_task_ids),
        }