from __future__ import annotations
import asyncio
from enum import Enum, StrEnum

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from pai.common.enums.permission import Origin
from pai.common.task_controls import CancelMode, CancelOutcomeKind, TaskControls
from pai.security.authorization.context import ScopeContext
from pai.security.consent.models import ConfirmationInfo
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pai.storage.models import ScheduleModel


ScheduleKind = Literal["one_shot", "cron", "interval"]
CatchUp = Literal["none", "once", "all"]
LastRunStatus = Literal["ok", "skipped_no_consent", "denied", "failed", "device_offline", "missed"]


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
    misfire_grace_seconds: Optional[int] = Field(default=None, ge=0)   # None = a missed fire never expires
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
    misfire_grace_seconds: Optional[int] = Field(default=None, ge=0)
    target_device_id: Optional[str] = None
    enabled: Optional[bool] = None


class ConsentRequest(BaseModel):
    """Body of POST /schedules/{id}/consent."""
    days: int = Field(default=30, ge=1, le=90)


class ConsentOut(BaseModel):
    active: bool = False
    granted_at: Optional[str] = None
    expires_at: Optional[str] = None


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
    misfire_grace_seconds: Optional[int] = None
    enabled: bool
    last_fired_at: Optional[str]
    next_fire_at: Optional[str]
    created_at: str
    updated_at: str
    # --- scheduled plugin calls ---
    requires_consent: bool = False
    consent: ConsentOut = Field(default_factory=ConsentOut)
    last_run_at: Optional[str] = None
    last_run_status: Optional[LastRunStatus] = None
    last_run_error: Optional[str] = None
    last_task_id: Optional[str] = None      # lets the UI link a run to the Tasks screen
    risk: str = "low"
    executor: Optional[str] = None

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

        from pai.scheduler.consent import evaluate     # lazy: keeps the import graph acyclic
        state = evaluate(row)
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
            misfire_grace_seconds=row.misfire_grace_seconds,
            enabled=row.enabled,
            last_fired_at=iso(row.last_fired_at),
            next_fire_at=iso(row.next_fire_at),
            created_at=iso(row.created_at),
            updated_at=iso(row.updated_at),
            requires_consent=bool(row.requires_consent),
            consent=ConsentOut(**state.model_dump(include={"active", "granted_at", "expires_at"})),
            last_run_at=iso(row.last_run_at),
            last_run_status=row.last_run_status,
            last_run_error=row.last_run_error,
            last_task_id=row.last_task_id,
            risk=row.risk or "low",
            executor=row.executor,
        )

    
class TaskStatus(StrEnum):
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


# Defined in pai.common.enums.task (shared with the manifest models); re-exported here.
from pai.common.enums.task import TaskType  # noqa: E402,F401


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskSource(StrEnum):
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
    # False for failures that can never succeed on a retry (e.g. the capability does not
    # exist). The task manager then fails the task at once instead of backing off and retrying.
    retryable: bool = True

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


class RetryInfo(BaseModel):
    """The ``retry`` field of a serialized task: set while it waits for its next attempt."""
    attempt: int                          # the attempt about to start (1 = first retry)
    max_attempts: int                     # retry budget
    next_retry_at: Optional[datetime] = None   # UTC; null while paused (the attempt is held)
    delay_seconds: float = 0.0
    last_error: Optional[str] = None


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
    # Trusted-server-only: a valid standing consent already approved this run (scheduled
    # actions). Excluded from persistence so a task recovered after a restart is re-checked.
    pre_approved: bool = Field(default=False, exclude=True)
    pending: bool = False    # authorize() returned APPROVAL_REQUIRED
    tokens: List[str] = Field(default_factory=list, exclude=True)
    # The standing grant (session / always) that approved this run, if any (counted once).
    grant_id: Optional[str] = None

    # Trusted facts replayed on re-authorization.
    agent_id: Optional[str] = None
    origin: Origin = Origin.AGENT_AUTONOMOUS
    source_device_id: Optional[str] = None
    scope: ScopeContext = Field(default_factory=ScopeContext)


class TaskPhase(StrEnum):
    """Where a task is relative to the device/plugin call — what decides which controls are real."""
    PENDING = "pending"          # created / initialized / queued: nothing has started
    STARTING = "starting"        # running its pre-flight (authorization, confirmation): not dispatched
    BACKOFF = "backoff"          # waiting to retry: the previous attempt is over
    DISPATCHED = "dispatched"    # handed to the executor/device: the call is in flight
    FINISHED = "finished"        # completed / failed for good / cancelled


class ControlDecision(BaseModel):
    """Whether a control (pause / resume / cancel) is available, and if not, why."""
    allowed: bool
    code: Optional[str] = None      # "control_not_supported" | "invalid_state"
    reason: Optional[str] = None


_ALLOW = ControlDecision(allowed=True)


def _deny(code: str, reason: str) -> ControlDecision:
    return ControlDecision(allowed=False, code=code, reason=reason)


class TaskControlView(BaseModel):
    """The control fields every API/SSE task payload carries (computed in ONE place: Task)."""
    controls: TaskControls
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    cancel_outcome: Optional[CancelOutcomeKind] = None
    control_blocked_reason: Optional[str] = None


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
    # Set while the task waits for the user: what they are asked to approve and which scopes
    # (once / session / always) the SERVER allows. Cleared when the wait ends.
    confirmation: Optional[ConfirmationInfo] = None

    # Authorization state (typed; tokens are runtime-only and never serialized).
    authorization: "TaskAuthorization" = Field(default_factory=lambda: TaskAuthorization())

    # Times this task was re-queued because the server stopped under it (not the task's fault,
    # so it does not consume the retry budget).
    recovery_count: int = 0

    # Pause/cancel support, from the capability manifest (or the built-in's definition) — NEVER from
    # LLM/plan output. See pai/common/task_controls.py.
    controls: TaskControls = Field(default_factory=TaskControls)

    # Set when the call is handed to the executor/device. Before it, pause/cancel are always real
    # (nothing ran yet); after it, they depend on `controls` and on the device acknowledging.
    dispatched_at: Optional[datetime] = None

    # What a cancel achieved: not_started | acked | abandoned (device may have completed it).
    cancel_outcome: Optional[CancelOutcomeKind] = None

    # Status to return to on resume (RUNNING, QUEUED or RETRYING).
    paused_from: Optional[TaskStatus] = None

    # Error of the last failed attempt (the `error` field is cleared while retrying).
    last_error: Optional[str] = None

    # While waiting to retry: when the next attempt starts and how long the backoff is.
    next_retry_at: Optional[datetime] = None
    retry_delay_seconds: Optional[float] = None

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
    def retry_info(self) -> Optional[RetryInfo]:
        """Set only while the task waits for its next attempt (RETRYING, or paused during that
        wait); None otherwise — so ``retry`` and ``status`` can never disagree."""
        waiting = self.status == TaskStatus.RETRYING
        held = self.status == TaskStatus.PAUSED and self.paused_from == TaskStatus.RETRYING
        if not (waiting or held):
            return None
        return RetryInfo(
            attempt=self.retry_count, max_attempts=self.max_retries,
            next_retry_at=None if held else self.next_retry_at,
            delay_seconds=self.retry_delay_seconds or 0.0, last_error=self.last_error,
        )

    def schedule_retry(self, delay_seconds: float) -> None:
        """Record when the next attempt starts (call right after moving to RETRYING)."""
        self.retry_delay_seconds = delay_seconds
        self.next_retry_at = utc_now() + timedelta(seconds=delay_seconds)

    def retrying_payload(self) -> Dict[str, Any]:
        """Bus payload of ``task.retrying``: the FULL task plus ``retry``."""
        info = self.retry_info
        return {**self.to_dict(), "retry": info.model_dump(mode="json") if info else None}

    @property
    def can_retry(self) -> bool:
        return (
            self.status == TaskStatus.FAILED
            and self.retry_count < self.max_retries
        )
    # ------------------------------------------------------------------
    # Controls — the ONE place that decides what the user may do (API, UI and admin all use it)
    # ------------------------------------------------------------------

    @property
    def phase(self) -> TaskPhase:
        s = self.status
        if s in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return TaskPhase.FINISHED
        if s == TaskStatus.FAILED:
            return TaskPhase.FINISHED
        if s == TaskStatus.RETRYING or (s == TaskStatus.PAUSED and self.paused_from == TaskStatus.RETRYING):
            return TaskPhase.BACKOFF
        if s in (TaskStatus.CREATED, TaskStatus.INITIALIZED, TaskStatus.QUEUED) or (
            s == TaskStatus.PAUSED and self.paused_from == TaskStatus.QUEUED
        ):
            return TaskPhase.PENDING
        return TaskPhase.DISPATCHED if self.dispatched_at is not None else TaskPhase.STARTING

    def pause_decision(self) -> ControlDecision:
        s, ph = self.status, self.phase
        if ph == TaskPhase.FINISHED:
            return _deny("invalid_state", "The task has already finished.")
        if s == TaskStatus.PAUSED:
            return _deny("invalid_state", "The task is already paused.")
        if s in (TaskStatus.CREATED, TaskStatus.INITIALIZED):
            return _deny("invalid_state", "The task has not started yet.")
        if ph == TaskPhase.DISPATCHED and not self.controls.pause:
            return _deny("control_not_supported",
                         "This action can't be paused once it has started.")
        return _ALLOW

    def resume_decision(self) -> ControlDecision:
        if self.status != TaskStatus.PAUSED:
            return _deny("invalid_state", "The task is not paused.")
        return _ALLOW

    def cancel_decision(self) -> ControlDecision:
        ph = self.phase
        if ph == TaskPhase.FINISHED:
            return _deny("invalid_state", "The task has already finished.")
        if ph == TaskPhase.DISPATCHED and self.controls.cancel == CancelMode.NONE:
            return _deny("control_not_supported",
                         "This action can't be cancelled once it has started.")
        return _ALLOW

    def control_view(self) -> TaskControlView:
        return TaskControlView(
            controls=self.controls, can_pause=self.can_pause, can_resume=self.can_resume,
            can_cancel=self.can_cancel, cancel_outcome=self.cancel_outcome,
            control_blocked_reason=self.control_blocked_reason,
        )

    @property
    def can_pause(self) -> bool:
        return self.pause_decision().allowed

    @property
    def can_resume(self) -> bool:
        return self.resume_decision().allowed

    @property
    def can_cancel(self) -> bool:
        return self.cancel_decision().allowed

    @property
    def control_blocked_reason(self) -> Optional[str]:
        """Why pause/cancel is unavailable, in words a user can read (None if both are)."""
        reasons: List[str] = []
        if self.phase == TaskPhase.FINISHED:
            return None                      # nothing to control: not "blocked", just done
        for d in (self.pause_decision(), self.cancel_decision()):
            if not d.allowed and d.reason and d.reason not in reasons:
                reasons.append(d.reason)
        if self.status == TaskStatus.PAUSED:
            reasons = [r for r in reasons if r != "The task is already paused."]
        return " ".join(reasons) or None

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
        self.next_retry_at = None            # the attempt started: no longer waiting
        self.retry_delay_seconds = None

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
        self.last_error = error
        self.next_retry_at = None
        self.retry_delay_seconds = None

        if result is not None:
            self.result = result

    def mark_cancelled(self, reason: Optional[str] = None) -> None:
        self.status = TaskStatus.CANCELLED
        self.completed_at = utc_now()
        self.error = reason
        self.next_retry_at = None
        self.retry_delay_seconds = None

    def mark_retrying(self) -> None:
        self.retry_count += 1
        self.status = TaskStatus.RETRYING
        self.error = None
        self.result = None
        self.started_at = None
        self.completed_at = None
        self.dispatched_at = None        # the next attempt has not reached the device yet
        self.paused_from = None

    def mark_interrupted(self) -> None:
        """The server stopped while this was in flight: make it runnable again.

        Deliberately outside the normal state machine and not counted as a retry.
        """
        self.status = TaskStatus.INITIALIZED
        self.recovery_count += 1
        self.dispatched_at = None
        self.paused_from = None
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

        # Poll inside the awaited call itself: on timeout / cancellation the poll stops with it
        # (a detached poller task used to keep running forever after a timed-out wait).
        async def _until_terminal() -> None:
            while not self.terminal:
                await asyncio.sleep(0.1)

        await asyncio.wait_for(_until_terminal(), timeout=timeout)
        if self.status == TaskStatus.COMPLETED:
            return self.result
        raise RuntimeError(f"Task {self.id} did not complete successfully: {self.error}")
