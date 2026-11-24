"""Typed results of MemoryManager.recall (replaces the untyped dict of lists)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from pai.memory.safety.models import MemoryLayer, ToolOutcome, Trust


class RecalledMemory(BaseModel):
    """One remembered interaction. ``text`` is already sanitized for prompt use."""
    id: Optional[str] = None
    layer: MemoryLayer
    text: str
    created_at: Optional[float] = None       # unix seconds
    trust: Trust = Trust.UNTRUSTED
    failed: bool = False
    outcomes: List[ToolOutcome] = Field(default_factory=list)


class RecalledPreference(BaseModel):
    key: str
    value: str
    importance: float = 0.5
    trust: Trust = Trust.UNTRUSTED


class RecalledProcedure(BaseModel):
    goal_pattern: str
    steps: List[str]
    success_rate: float


class RecallResult(BaseModel):
    short_term: List[RecalledMemory] = Field(default_factory=list)
    episodic: List[RecalledMemory] = Field(default_factory=list)
    semantic: List[RecalledMemory] = Field(default_factory=list)
    long_term: List[RecalledPreference] = Field(default_factory=list)
    procedural: Optional[RecalledProcedure] = None

    def counts(self) -> dict:
        return {
            "short_term": len(self.short_term), "episodic": len(self.episodic),
            "semantic": len(self.semantic), "long_term": len(self.long_term),
            "procedural": 1 if self.procedural else 0,
        }
