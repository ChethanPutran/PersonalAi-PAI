from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from pai.observability.events import EventKind, TraceEvent
from pai.observability.redact import redact, truncate
from pai.observability.store import TraceStore


_current: ContextVar[Optional["_Ctx"]] = ContextVar("pai_trace", default=None)


class _Ctx:
    __slots__ = ("trace_id", "span_id")
    def __init__(self, trace_id: str, span_id: str):
        self.trace_id = trace_id
        self.span_id = span_id


class NoOpSpan:
    def set_attribute(self, _k, _v): pass
    def set_error(self, _e): pass
    def __enter__(self): return self
    def __exit__(self, *_): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


class Span:
    """Span within an active trace. Silently no-ops if no trace is active."""

    def __init__(self, store: TraceStore, name: str, attributes: Dict[str, Any]):
        self.store = store
        self.name = name
        self.attributes = dict(attributes)
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id: Optional[str] = None
        self._t0 = 0.0
        self._token = None
        self._error: Optional[str] = None
        self._noop = False

    def __enter__(self) -> "Span":
        parent = _current.get()
        if parent is None:
            self._noop = True
            self._t0 = time.time()
            return self

        self.parent_span_id = parent.span_id
        self._t0 = time.time()
        self._token = _current.set(_Ctx(parent.trace_id, self.span_id))

        self.store.append(TraceEvent(
            trace_id=parent.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            kind=EventKind.SPAN_START.value,
            name=self.name,
            timestamp=self._t0,
            attributes=truncate(redact(self.attributes)),
        ))
        return self

    def __exit__(self, exc_type, exc_val, _tb) -> None:
        if self._noop:
            return
        parent = _current.get()
        duration_ms = (time.time() - self._t0) * 1000
        if exc_val is not None:
            self._error = f"{exc_type.__name__}: {exc_val}"

        self.store.append(TraceEvent(
            trace_id=parent.trace_id if parent else "?",
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            kind=EventKind.SPAN_END.value,
            name=self.name,
            timestamp=time.time(),
            duration_ms=duration_ms,
            attributes=truncate(redact(self.attributes)),
            error=self._error,
        ))
        if self._token is not None:
            _current.reset(self._token)

    def set_attribute(self, k: str, v: Any) -> None:
        self.attributes[k] = truncate(redact(v))

    def set_error(self, error: str) -> None:
        self._error = error

    async def __aenter__(self) -> "Span":
        return self.__enter__()

    async def __aexit__(self, et, ev, tb) -> None:
        self.__exit__(et, ev, tb)


class TraceSession:
    def __init__(self, store: TraceStore, trace_id: str, name: str, attributes: Dict[str, Any]):
        self.store = store
        self.trace_id = trace_id
        self.name = name
        self.attributes = dict(attributes)
        self._t0 = 0.0
        self._token = None
        self._status = "completed"
        self._error: Optional[str] = None

    def __enter__(self) -> "TraceSession":
        self._t0 = time.time()
        self._token = _current.set(_Ctx(self.trace_id, "root"))
        self.store.append(TraceEvent(
            trace_id=self.trace_id,
            span_id="root",
            parent_span_id=None,
            kind=EventKind.TRACE_START.value,
            name=self.name,
            timestamp=self._t0,
            attributes=truncate(redact(self.attributes)),
        ))
        return self

    def __exit__(self, exc_type, exc_val, _tb) -> None:
        duration_ms = (time.time() - self._t0) * 1000
        if exc_val is not None:
            self._status = "failed"
            self._error = f"{exc_type.__name__}: {exc_val}"
            self.store.append(TraceEvent(
                trace_id=self.trace_id, span_id="root", parent_span_id=None,
                kind=EventKind.ERROR.value, name="trace.error",
                timestamp=time.time(), error=self._error,
            ))
        self.store.append(TraceEvent(
            trace_id=self.trace_id, span_id="root", parent_span_id=None,
            kind=EventKind.TRACE_END.value, name=self.name,
            timestamp=time.time(), duration_ms=duration_ms,
            attributes={"status": self._status},
            error=self._error,
        ))
        if self._token is not None:
            _current.reset(self._token)

    def set_status(self, status: str) -> None:
        self._status = status

    async def __aenter__(self): return self.__enter__()
    async def __aexit__(self, et, ev, tb): self.__exit__(et, ev, tb)


class Tracer:
    def __init__(self, store: Optional[TraceStore] = None):
        self.store = store

    # -------- trace --------

    def start_trace(
        self,
        name: str,
        *,
        trace_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> TraceSession | NoOpSpan:
        if self.store is None:
            return NoOpSpan()
        return TraceSession(
            self.store, trace_id or uuid.uuid4().hex, name, attributes or {},
        )

    # -------- span --------

    def span(self, name: str, **attributes: Any) -> Span | NoOpSpan:
        if self.store is None:
            return NoOpSpan()
        return Span(self.store, name, attributes)

    # -------- llm --------

    def log_llm_call(
        self,
        *,
        provider: str,
        model: str,
        prompt: str,
        response: Optional[str] = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.store is None:
            return
        ctx = _current.get()
        if ctx is None:
            return

        attrs: Dict[str, Any] = {
            "provider": provider,
            "model": model,
            "prompt": truncate(redact(prompt), max_len=6000),
        }
        if extra:
            attrs.update(truncate(redact(extra), max_len=2000))
        if response is not None:
            attrs["response"] = truncate(redact(response), max_len=6000)

        kind = (
            EventKind.LLM_RESPONSE.value
            if response is not None or error is not None
            else EventKind.LLM_REQUEST.value
        )
        self.store.append(TraceEvent(
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            parent_span_id=None,
            kind=kind,
            name=f"llm:{provider}:{model}",
            timestamp=time.time(),
            duration_ms=duration_ms,
            attributes=attrs,
            error=error,
        ))

    # -------- tool --------

    def log_tool_call(
        self,
        *,
        capability: str,
        plugin: str,
        device_id: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        result: Any = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> None:
        if self.store is None:
            return
        ctx = _current.get()
        if ctx is None:
            return

        attrs: Dict[str, Any] = {
            "capability": capability,
            "plugin": plugin,
            "device_id": device_id,
            "parameters": truncate(redact(parameters or {}), max_len=4000),
        }
        if result is not None:
            attrs["result"] = truncate(redact(result), max_len=4000)

        kind = (
            EventKind.TOOL_RESULT.value
            if result is not None or error is not None
            else EventKind.TOOL_CALL.value
        )
        self.store.append(TraceEvent(
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            parent_span_id=None,
            kind=kind,
            name=f"tool:{capability}",
            timestamp=time.time(),
            duration_ms=duration_ms,
            attributes=attrs,
            error=error,
        ))


# ---------------- module-level singleton ----------------

_tracer: Tracer = Tracer(store=None)


def set_tracer(t: Tracer) -> None:
    global _tracer
    _tracer = t


def get_tracer() -> Tracer:
    return _tracer