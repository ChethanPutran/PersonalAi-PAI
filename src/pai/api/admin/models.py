"""Typed request/response models of the admin API (no raw dicts in handlers)."""
from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from pai.planning.dag_models import CancelOutcome, DagSnapshot
from pai.tasks.models import Task, TaskStatus, TaskType

_SPEC_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


# ------------------------------------------------------------------ tasks

class AdminTask(BaseModel):
    """One task, as the admin sees it (all users)."""
    id: str
    name: str
    status: TaskStatus
    type: str
    capability: Optional[str] = None
    plugin: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    device_id: Optional[str] = None
    executor_id: Optional[str] = None
    request_id: Optional[str] = None       # the DAG run it belongs to
    spec_id: Optional[str] = None          # its node id inside that run
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 0
    error: Optional[str] = None
    requires_confirmation: bool = False

    @classmethod
    def from_task(cls, t: Task) -> "AdminTask":
        end = t.completed_at
        dur = None
        if t.started_at is not None:
            dur = round(((end or datetime.now(t.started_at.tzinfo)) - t.started_at).total_seconds() * 1000, 1)
        # idempotency_key is "<request_id>:<spec_id>"; strip the known spec_id suffix
        key, request_id = t.idempotency_key, None
        if key and t.spec_id and key.endswith(f":{t.spec_id}"):
            request_id = key[: -(len(t.spec_id) + 1)] or None
        return cls(
            id=t.id, name=t.name, status=t.status, type=t.type.value, capability=t.capability,
            plugin=t.plugin, user_id=t.user_id, session_id=t.session_id, device_id=t.device_id,
            executor_id=t.executor_id, request_id=request_id, spec_id=t.spec_id,
            created_at=t.created_at, started_at=t.started_at, completed_at=end, duration_ms=dur,
            retry_count=t.retry_count, max_retries=t.max_retries, error=t.error,
            requires_confirmation=t.requires_confirmation)


class TaskPage(BaseModel):
    items: List[AdminTask]
    total: int
    offset: int
    limit: int


class TaskActionResponse(BaseModel):
    task: AdminTask


# --------------------------------------------------------------- overview

class TaskCounts(BaseModel):
    created: int = 0
    pending: int = 0
    initialized: int = 0
    queued: int = 0
    running: int = 0
    paused: int = 0
    retrying: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0

    @property
    def active(self) -> int:
        return self.created + self.pending + self.initialized + self.queued + self.running + self.paused + self.retrying


class ExecutorInfo(BaseModel):
    name: str
    running: bool
    kind: str


class DeviceCounts(BaseModel):
    total: int = 0
    connected: int = 0


class VoiceInfo(BaseModel):
    available: bool
    stt: Optional[str] = None
    tts: Optional[str] = None
    active_sessions: int = 0
    reason: str = ""


class ProcessResources(BaseModel):
    """Always-available process vitals (works without the observability resources module)."""
    rss_mb: float
    threads: int
    asyncio_tasks: int
    open_fds: Optional[int] = None
    uptime_seconds: float
    gc_objects: int


class OverviewResponse(BaseModel):
    tasks: TaskCounts
    active_tasks: int
    active_dags: int
    dags_in_history: int
    devices: DeviceCounts
    executors: List[ExecutorInfo]
    voice: VoiceInfo
    process: ProcessResources
    # Extra resource figures when pai.observability.resources is available (pass-through).
    resources: Optional[Dict[str, Any]] = None
    test_capabilities_enabled: bool = False
    server_time: datetime


# ------------------------------------------------------------------- DAGs

class DagListResponse(BaseModel):
    runs: List[DagSnapshot]
    retention_days: Optional[int] = None      # how far back finished runs are kept (None = in memory only)


class KillTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus


# -------------------------------------------------------------------- run

class NodeSpec(BaseModel):
    id: str
    capability: str = Field(min_length=1, max_length=120)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list, max_length=50)
    type: TaskType = TaskType.ONE_SHOT
    timeout: Optional[float] = Field(default=None, gt=0, le=3600)
    name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _SPEC_ID.match(v):
            raise ValueError("id must be 1-64 chars of letters, digits, _ . : -")
        return v


class RunDagRequest(BaseModel):
    tasks: List[NodeSpec] = Field(min_length=1, max_length=50)
    label: str = Field(default="admin dag", max_length=120)


class RunGoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None


class DemoKind(StrEnum):
    FANOUT = "fanout"
    CHAIN = "chain"
    LONG_RUNNING = "long_running"
    FAILURE_SKIP = "failure_skip"


class RunAccepted(BaseModel):
    request_id: str
    kind: str                       # "goal" | "dag" | "demo:<kind>"
    status: str = "started"
    task_count: Optional[int] = None


class DemoInfo(BaseModel):
    kind: DemoKind
    title: str
    description: str


class CapabilityInfo(BaseModel):
    id: str
    description: str = ""
    test: bool = False
    parameters: Optional[Dict[str, Any]] = None    # JSON Schema (inherently free-form)


class CapabilityList(BaseModel):
    capabilities: List[CapabilityInfo]
    test_capabilities_enabled: bool
    demos: List[DemoInfo]


# ---------------------------------------------------------------- test tools

class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: Optional[str] = None


class SttResponse(BaseModel):
    text: str
    audio_seconds: float
    elapsed_ms: float
    provider: str


class VoiceStatus(BaseModel):
    available: bool
    reason: str = ""
    stt: Optional[str] = None
    tts: Optional[str] = None
    partial_transcripts: bool = False
    input_sample_rate: int
    output_sample_rate: int


class LlmTestRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class NodeKillResponse(BaseModel):
    request_id: str
    spec_id: str
    outcome: CancelOutcome


class ErrorDetail(BaseModel):
    message: str
    issues: List[str] = Field(default_factory=list)
    code: Optional[str] = None      # e.g. control_not_supported | control_not_acknowledged | invalid_state
