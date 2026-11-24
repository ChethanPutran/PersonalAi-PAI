"""Planning domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional
import uuid

from pai.tasks.models import TaskType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlanStatus(StrEnum):
    CREATED = "created"
    VERIFIED = "verified"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ADAPTED = "adapted"


class PlanTaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    BLOCKED = "blocked"


@dataclass
class TaskSpec:
    """
    Task description produced by the planner.

    This is deliberately NOT the runtime Task model from pai.tasks.
    The planning layer only describes what needs to be done.
    """

    name: str = ""          # defaults to the capability / id (see __post_init__)
    input: str = ""
    type: TaskType = TaskType.ONE_SHOT
    parameters: Dict[str, Any] = field(default_factory=dict)

    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    
    # Executor/capability requirements.
    capability: Optional[str] = None
    preferred_executor: Optional[str] = None
    preferred_device: Optional[str] = None

    # Task dependencies.
    depends_on: List[str] = field(default_factory=list)

    # Planning metadata.
    timeout: float = field(default=10) #10s
    retry_count: int = 3
    metadata: Dict[str, Any] = field(default_factory=dict)

    status: PlanTaskStatus = PlanTaskStatus.PENDING

    # Populated from the capability schema at decompose time
    requires_confirmation: bool = False
    confirmation_reason: str = ""
    # Populated from the capability manifest (never from LLM output); the TaskManager
    # re-resolves it authoritatively. False => no automatic retry / re-send.
    retryable: bool = True

    def __post_init__(self) -> None:
        # The planner often omits these; sensible defaults beat a TypeError mid-plan.
        if not self.name:
            self.name = self.capability or self.id
        if self.timeout is None:
            self.timeout = 10

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "parameters": self.parameters,
            "capability": self.capability,
            "preferred_executor": self.preferred_executor,
            "preferred_device": self.preferred_device,
            "depends_on": self.depends_on,
            "timeout": self.timeout,
            "retry_count": self.retry_count,
            "metadata": self.metadata,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskSpec":
        status = data.get("status", PlanTaskStatus.PENDING)

        if not isinstance(status, PlanTaskStatus):
            status = PlanTaskStatus(status)

        name = data.get("name", f"task_{uuid.uuid4().hex[:12]}")


        return cls(
            id=data.get("id", f"task_{uuid.uuid4().hex[:12]}"),
            input=data.get("input", ""),
            type=data["type"],
            name=name,
            parameters=data.get(
                "parameters",
                data.get("params", {}),
            ),
            capability=data.get("capability"),
            preferred_executor=data.get("preferred_executor"),
            preferred_device=data.get("preferred_device"),
            depends_on=list(data.get("depends_on", [])),
            timeout=data.get("timeout", 10),
            retry_count=int(data.get("retry_count", 0)),
            metadata=dict(data.get("metadata", {})),
            status=status,
        )


@dataclass
class Plan:
    """
    Planner output.

    A Plan contains TaskSpec objects but does not execute them.
    """

    goal: str
    tasks: List[TaskSpec]

    id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}")
    status: PlanStatus = PlanStatus.CREATED

    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    verification_issues: List[str] = field(default_factory=list)

    def task_map(self) -> Dict[str, TaskSpec]:
        return {task.id: task for task in self.tasks}

    def dependencies(self) -> Dict[str, List[str]]:
        return {
            task.id: list(task.depends_on)
            for task in self.tasks
            if task.depends_on
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "tasks": [task.to_dict() for task in self.tasks],
            "status": self.status.value,
            "context": self.context,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "verification_issues": self.verification_issues,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        status = data.get("status", PlanStatus.CREATED)

        if not isinstance(status, PlanStatus):
            status = PlanStatus(status)

        return cls(
            id=data["id"],
            goal=data["goal"],
            tasks=[
                TaskSpec.from_dict(task)
                for task in data.get("tasks", [])
            ],
            status=status,
            context=dict(data.get("context", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=datetime.fromisoformat(
                data["created_at"]
            ) if data.get("created_at") else utc_now(),
            updated_at=datetime.fromisoformat(
                data["updated_at"]
            ) if data.get("updated_at") else utc_now(),
            verification_issues=list(
                data.get("verification_issues", [])
            ),
        )