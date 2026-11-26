"""What one speech call cost (seconds transcribed / characters spoken), handed from the provider to the voice session.

The provider that made the request knows the billed units and cost; the session knows which turn it belongs to. A
context variable connects them without the providers knowing about sessions or traces:

    with collect() as usage:          # in the session, around the STT / TTS call
        text = await stt.finish()
    usage -> [{"model": ..., "units": 4.2, "unit": "seconds", "cost_usd": 0.0004}]
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional

_sink: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("voice_usage_sink", default=None)


def report(**usage: Any) -> None:
    """Called by a provider after a billable request; a no-op outside `collect()`."""
    sink = _sink.get()
    if sink is not None:
        sink.append(usage)


@contextlib.contextmanager
def collect() -> Iterator[List[Dict[str, Any]]]:
    sink: List[Dict[str, Any]] = []
    token = _sink.set(sink)
    try:
        yield sink
    finally:
        _sink.reset(token)


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Span attributes from the collected usage: model, units, unit and the summed known cost (None if unknown)."""
    if not items:
        return {}
    costs = [i["cost_usd"] for i in items if isinstance(i.get("cost_usd"), (int, float))]
    return {"model": items[-1].get("model"), "unit": items[-1].get("unit"),
            "units": round(sum(float(i.get("units") or 0) for i in items), 3),
            "cost_usd": round(sum(costs), 8) if costs else None}
