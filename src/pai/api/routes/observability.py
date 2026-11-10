"""Observability API — traces + SSE stream."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from pai.api.dependencies import get_current_admin
from pai.observability import get_tracer


router = APIRouter(prefix="/system/traces", tags=["observability"])


# ============================================================
# ROUTE ORDER — static paths MUST come before "/{trace_id}"
#   1. ""            (list)
#   2. "/stream"     (SSE)
#   3. "/{trace_id}" (detail — matches everything else)
# ============================================================


# ============================================================
# Trace listing
# ============================================================

@router.get("", dependencies=[Depends(get_current_admin)])
async def list_traces(
    user_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    has_error: Optional[bool] = Query(None),
    has_llm: Optional[bool] = Query(None),
    has_tool: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    tracer = get_tracer()
    if tracer.store is None:
        return {"traces": [], "total": 0, "offset": offset}

    traces = tracer.store.list_traces(
        user_id=user_id, status=status, has_error=has_error,
        has_llm=has_llm, has_tool=has_tool, limit=limit, offset=offset,
    )
    return {
        "traces": [t.to_dict() for t in traces],
        "total": len(traces),
        "offset": offset,
    }


# ============================================================
# SSE stream
# ============================================================

@router.get("/stream", dependencies=[Depends(get_current_admin)])
async def stream_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of new trace events."""
    tracer = get_tracer()

    if tracer.store is None:
        async def _empty() -> AsyncIterator[bytes]:
            yield b": tracing disabled\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    async def event_gen() -> AsyncIterator[bytes]:
        q = tracer.store.subscribe()
        try:
            yield b": connected\n\n"

            while True:
                # Three exit conditions, checked on every tick:
                #   1. Store shutting down (graceful)
                #   2. Client disconnected (via uvicorn's http.disconnect)
                #   3. Sentinel from TraceStore.stop()
                if tracer.store.is_shutting_down:
                    break
                if await request.is_disconnected():
                    break

                try:
                    ev = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue

                if ev is None:
                    break

                payload = json.dumps(ev.to_dict(), default=str)
                yield f"data: {payload}\n\n".encode()

        except asyncio.CancelledError:
            # uvicorn cancelled us during shutdown — propagate so
            # StreamingResponse runs its cleanup.
            raise
        finally:
            tracer.store.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "close",       # ← prevents HTTP keep-alive reuse
        },
    )

# ============================================================
# Trace detail
# ============================================================

@router.get("/{trace_id}", dependencies=[Depends(get_current_admin)])
async def get_trace(trace_id: str) -> Dict[str, Any]:
    tracer = get_tracer()
    if tracer.store is None:
        raise HTTPException(503, "tracing disabled")

    summary = tracer.store.get_summary(trace_id)
    if summary is None:
        raise HTTPException(404, f"trace '{trace_id}' not found")

    events = tracer.store.get_trace(trace_id)
    return {
        "summary": summary.to_dict(),
        "events": [e.to_dict() for e in events],
    }