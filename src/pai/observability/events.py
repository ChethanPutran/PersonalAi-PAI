from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class EventKind(str, Enum):
    TRACE_START = "trace.start"
    TRACE_END   = "trace.end"
    SPAN_START  = "span.start"
    SPAN_END    = "span.end"
    LOG         = "log"
    LLM_REQUEST  = "llm.request"
    LLM_RESPONSE = "llm.response"
    TOOL_CALL   = "tool.call"
    TOOL_RESULT = "tool.result"
    ERROR       = "error"


@dataclass
class TraceEvent:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    kind: str
    name: str
    timestamp: float
    duration_ms: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TraceSummary:
    trace_id: str
    name: str
    user_id: Optional[str]
    started_at: float
    ended_at: Optional[float]
    duration_ms: Optional[float]
    status: str
    event_count: int
    error_count: int
    has_llm: bool
    has_tool: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)