"""Typed models for the agent runtime (no behaviour, no I/O)."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_USER = "waiting_user"     # blocked in ask_user until the user answers
    PAUSED = "paused"                 # user paused it; resumes at the next step boundary
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED)


class AgentEventKind(StrEnum):
    STATE = "state"                   # lifecycle change
    THOUGHT = "thought"               # the model's reasoning for this step
    ACTION = "action"                 # a tool call about to run
    OBSERVATION = "observation"       # what the tool returned (preview)
    MESSAGE = "message"               # the agent speaking to the user (say)
    QUESTION = "question"             # the agent needs an answer (ask_user)
    USER = "user"                     # the user speaking to the agent
    FINAL = "final"                   # the result
    ERROR = "error"                   # a recoverable problem (bad format, tool error)


class AgentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    ts: datetime
    kind: AgentEventKind
    data: Dict[str, Any] = Field(default_factory=dict)

    def wire(self) -> Dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts.isoformat(), "kind": self.kind.value, **self.data}


class AgentLimits(BaseModel):
    """Budgets that bound a run. Every one has a hard ceiling the caller cannot raise."""
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=25, ge=1, le=100)
    max_runtime_seconds: float = Field(default=1800, gt=0, le=4 * 3600)
    tool_timeout_seconds: float = Field(default=300, gt=0, le=1800)   # includes waiting for an on-screen confirmation
    ask_timeout_seconds: float = Field(default=900, gt=0, le=3600)     # how long to wait for the user
    observation_max_chars: int = Field(default=4000, ge=200, le=20000)
    history_full_observations: int = Field(default=6, ge=1, le=30)     # older results are collapsed
    max_format_errors: int = Field(default=3, ge=1, le=10)             # consecutive unparseable replies
    llm_retries: int = Field(default=2, ge=0, le=5)
    temperature: float = Field(default=0.2, ge=0, le=1.5)
    max_tokens: int = Field(default=1200, ge=100, le=8000)


class AgentSpec(BaseModel):
    """What to run: a goal, and the capabilities the agent may use to reach it."""
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    # Capability ids ("browser.open") or prefix globs ("browser.*"). Resolved against what the
    # user really has at spawn time; the agent can use nothing else.
    tools: List[str] = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, max_length=80)
    device_id: Optional[str] = None            # preferred device for tool calls
    limits: AgentLimits = Field(default_factory=AgentLimits)


class AgentInfo(BaseModel):
    """Snapshot of one run, safe to return to its owner."""
    id: str
    name: str
    goal: str
    state: AgentState
    tools: List[str]
    steps: int
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    pending_question: Optional[str] = None
    session_id: Optional[str] = None
    last_seq: int = 0
