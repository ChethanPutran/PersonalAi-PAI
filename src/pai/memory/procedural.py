"""Procedural memory: reusable workflows (capability sequences) learned per user.

Per-user on purpose (one user's learned procedures must never steer another's
plans) and typed: a procedure is a list of capability ids taken from a plan that
the server validated and ran, never free text from the user, the LLM or a tool.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from pai.memory.utils import keyword_tokens
from pai.observability.tracer import get_tracer

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_MAX_STEPS = 20
_MAX_WORKFLOWS_PER_USER = 200


class ProcedureStep(BaseModel):
    capability: str = Field(pattern=_CAPABILITY_ID.pattern)


class Workflow(BaseModel):
    goal_pattern: str
    steps: List[ProcedureStep]
    successes: int = 0
    failures: int = 0

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total else 0.0


class WorkflowFile(BaseModel):
    """On-disk format: {"version": 2, "users": {user_id: {goal_pattern: Workflow}}}."""
    version: int = 2
    users: Dict[str, Dict[str, Workflow]] = Field(default_factory=dict)


class ProceduralMemory:
    def __init__(self, path: str = "./data/procedural.json"):
        self.path = Path(path)
        self.data = WorkflowFile()
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span("procedural.initialize", path=str(self.path)) as span:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data = self._load()
            span.set_attribute("user_count", len(self.data.users))

    def _load(self) -> WorkflowFile:
        if not self.path.exists():
            return WorkflowFile()
        try:
            return WorkflowFile.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (ValidationError, json.JSONDecodeError, OSError):
            # Legacy (v1, global/ownerless) or corrupt: we cannot attribute it to a user,
            # so it is dropped rather than shared with everyone.
            logger.warning("procedural memory file unreadable or legacy format — starting empty")
            return WorkflowFile()

    async def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.data.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)

    async def add_workflow(
        self, user_id: str, goal_pattern: str, steps: List[ProcedureStep], *, success: bool = True,
    ) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        goal_pattern = goal_pattern.strip().lower()
        if not goal_pattern:
            raise ValueError("goal_pattern cannot be empty")
        if not steps or len(steps) > _MAX_STEPS:
            raise ValueError(f"a workflow needs 1..{_MAX_STEPS} steps")

        tracer = get_tracer()
        async with tracer.span("procedural.add_workflow", step_count=len(steps), success=success) as span:
            t0 = time.perf_counter()
            async with self._lock:
                mine = self.data.users.setdefault(user_id, {})
                if goal_pattern not in mine and len(mine) >= _MAX_WORKFLOWS_PER_USER:
                    oldest = next(iter(mine))             # insertion order = age
                    del mine[oldest]
                wf = mine.setdefault(goal_pattern, Workflow(goal_pattern=goal_pattern, steps=steps))
                wf.steps = steps
                if success:
                    wf.successes += 1
                else:
                    wf.failures += 1
                await self._save()
            span.set_attribute("persist_ms", round((time.perf_counter() - t0) * 1000, 1))

    async def get_workflow(self, user_id: str, goal: str) -> Optional[Workflow]:
        """Best workflow of this user whose pattern appears in ``goal``."""
        if not user_id:
            raise ValueError("user_id is required")
        goal_tokens = set(keyword_tokens(goal, max_tokens=24))
        best, best_key = None, None
        for pattern, wf in self.data.users.get(user_id, {}).items():
            tokens = set(keyword_tokens(pattern, max_tokens=24))
            shared = len(tokens & goal_tokens)
            # Same task if they share >=2 content words covering >=80% of the shorter one.
            if shared >= 2 and shared / max(1, min(len(tokens), len(goal_tokens))) >= 0.8:
                key = (shared, wf.success_rate)
                if best_key is None or key > best_key:
                    best, best_key = wf, key
        return best

    async def shutdown(self) -> None:
        async with self._lock:
            try:
                await self._save()
            except Exception as exc:
                logger.warning("Failed to persist procedural memory on shutdown: {}", exc)
