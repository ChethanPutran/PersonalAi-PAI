"""Typed vocabulary of the memory safety layer (no raw dicts)."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MemorySource(StrEnum):
    """Who authored the content. Drives what it is allowed to do."""
    USER = "user"            # typed/spoken by the authenticated user
    ASSISTANT = "assistant"  # LLM-generated text (can echo attacker content)
    TOOL = "tool"            # output of a device/plugin call
    EXTERNAL = "external"    # web pages, emails, files... anything fetched
    SYSTEM = "system"        # produced by trusted server code


class MemoryLayer(StrEnum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"        # vector index
    LONG_TERM = "long_term"      # preferences / profile facts
    PROCEDURAL = "procedural"
    KNOWLEDGE_GRAPH = "knowledge_graph"


class Trust(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    QUARANTINED = "quarantined"


class Verdict(StrEnum):
    ALLOW = "allow"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)
    tag: str                     # e.g. "instruction_override", "zero_width", "secret:openai_key"
    weight: float                # contribution to the risk score


class ScanReport(BaseModel):
    """Result of scanning one piece of text."""
    findings: List[Finding] = Field(default_factory=list)
    risk: float = 0.0            # 0..1
    sanitized: str = ""          # control chars stripped, boundary tokens + secrets neutralized
    truncated: bool = False

    @property
    def tags(self) -> List[str]:
        return [f.tag for f in self.findings]


class Provenance(BaseModel):
    """Where a memory came from. Stored with every item and checked on recall."""
    model_config = ConfigDict(frozen=True)
    source: MemorySource
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WriteVerdict(BaseModel):
    verdict: Verdict
    text: str = ""                       # what may be stored (sanitized)
    trust: Trust = Trust.UNTRUSTED
    importance: float = 0.5              # possibly clamped
    reason: str = ""
    findings: List[Finding] = Field(default_factory=list)
    content_sha256: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == Verdict.ALLOW


class ToolOutcome(BaseModel):
    """Sanitized summary of one tool/task result (replaces the raw result blob)."""
    capability: str = "task"
    status: str = "completed"
    error: Optional[str] = None          # scanned + truncated
    reply: Optional[str] = None          # assistant reply (chat task), scanned + truncated
    detail: Optional[str] = None         # whitelisted result bits ("path=...") scanned + truncated


class QuarantineRecord(BaseModel):
    """What we keep about a blocked write (never the raw payload)."""
    user_id: Optional[str]
    layer: MemoryLayer
    source: MemorySource
    tags: List[str]
    risk: float
    content_sha256: str
    preview: str                         # sanitized, ≤120 chars
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
