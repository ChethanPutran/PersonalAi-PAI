"""Typed results and event payloads for DAG execution (no raw dicts)."""
from __future__ import annotations

from enum import StrEnum
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from pai.tasks.models import TaskResult


class DagStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class SkippedTask(BaseModel):
    """A task that never ran because a dependency failed (or was unknown)."""
    task_id: str                       # spec id
    status: Literal["skipped"] = "skipped"
    error: str


class DagResult(BaseModel):
    """Outcome of DAGScheduler.execute. ids are *spec* ids (what depends_on uses)."""
    status: DagStatus
    results: List[Union[TaskResult, SkippedTask]] = Field(default_factory=list)
    completed: List[str] = Field(default_factory=list)
    failed: List[str] = Field(default_factory=list)
    skipped: List[str] = Field(default_factory=list)


class CancelOutcome(StrEnum):
    CANCELLED_RUNNING = "cancelled_running"      # was executing; the task was killed
    CANCELLED_PENDING = "cancelled_pending"      # hadn't started; it (and its dependents) won't run
    ALREADY_FINISHED = "already_finished"
    NOT_FOUND = "not_found"


# ---- event payloads ------------------------------------------------------

class DagEvent(BaseModel):
    """Common routing fields; `session_id` lets the status stream filter per user session."""
    model_config = ConfigDict(frozen=True)
    request_id: str
    user_id: str
    session_id: Optional[str] = None


class DagStartedEvent(DagEvent):
    task_count: int
    max_concurrency: int
    task_ids: List[str]


class DagTaskStartedEvent(DagEvent):
    task_id: str
    capability: Optional[str] = None


class DagTaskFinishedEvent(DagEvent):
    task_id: str
    status: Literal["completed", "failed", "skipped", "submitted"]
    error: Optional[str] = None
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0


class DagFinishedEvent(DagEvent):
    status: DagStatus
    completed: int
    failed: int
    skipped: int
    duration_ms: float


class DagFailedEvent(DagEvent):
    """Emitted when the DAG is rejected before running (e.g. a cycle)."""
    error: str
