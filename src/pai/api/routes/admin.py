"""Admin API: /api/v1/admin/* — run & test things, watch DAGs live, kill tasks.

Everything here is admin-only (``get_current_admin`` re-checks ``is_admin`` against the DB on
every request) and every mutating action is written to the audit log with the admin's id.
"""
from __future__ import annotations

import io
import wave
from typing import Any, AsyncIterator, Dict, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from loguru import logger

from pai.api.admin.events import AdminEventStream, admin_sse
from pai.api.admin.models import (
    AdminTask, CapabilityList, DagListResponse, DemoKind, ErrorDetail, KillTaskResponse, LlmTestRequest,
    NodeKillResponse, OverviewResponse, RunAccepted, RunDagRequest, RunGoalRequest, SttResponse,
    TaskActionResponse, TaskPage, TtsRequest, VoiceStatus,
)
from pai.api.admin.service import AdminError, AdminService
from pai.api.dependencies import get_current_admin
from pai.app_context import PAIAppContext, get_app_context
from pai.llm.models import LLMMessage, LLMRequest
from pai.planning.dag_models import CancelOutcome, DagSnapshot, KillRunResult
from pai.tasks.lifecycle import InvalidTaskTransition
from pai.tasks.models import TaskStatus
from pai.tasks.store import TaskNotFoundError
from pai.voice.audio import resample

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])

MAX_TTS_SECONDS = 60.0
STT_TARGET_RATE = 16000


def get_admin_service(context: PAIAppContext = Depends(get_app_context)) -> AdminService:
    svc = getattr(context, "admin", None)
    if svc is None:
        raise HTTPException(503, detail=ErrorDetail(message="admin panel is disabled").model_dump())
    return svc


def _fail(exc: AdminError) -> HTTPException:
    return HTTPException(exc.status, detail=ErrorDetail(message=exc.message, issues=exc.issues).model_dump())


# ------------------------------------------------------------------ overview

@router.get("/overview", response_model=OverviewResponse)
async def overview(svc: AdminService = Depends(get_admin_service)) -> OverviewResponse:
    return await svc.overview()


@router.get("/capabilities", response_model=CapabilityList)
async def capabilities(admin_id: str = Depends(get_current_admin),
                       svc: AdminService = Depends(get_admin_service)) -> CapabilityList:
    return await svc.capabilities(admin_id)


# --------------------------------------------------------------------- tasks

@router.get("/tasks/active", response_model=TaskPage)
async def active_tasks(
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
    user_id: Optional[str] = None, capability: Optional[str] = None,
    svc: AdminService = Depends(get_admin_service),
) -> TaskPage:
    """ALL users' tasks that are not finished (queued, running, paused, retrying...)."""
    return await svc.active_tasks(offset=offset, limit=limit, user_id=user_id, capability=capability)


# NOTE: listing/inspecting arbitrary tasks is served by the existing GET /system/tasks[/{id}]
# (api/routes/system.py); only the active-across-users view and audited actions live here.


async def _task_action(svc: AdminService, admin_id: str, task_id: str, action: str) -> TaskActionResponse:
    tm = svc.tm
    try:
        if action == "kill":
            current = await tm.store.require(task_id)
            rid, _, spec = (current.idempotency_key or "").partition(":")
            if rid and spec and not current.terminal:
                # A DAG node: kill through the scheduler so the node's own future is unwound too
                # (cancelling only the runtime task can lose a race with its start-up).
                await svc.dag.cancel(rid, spec)
            task = await tm.cancel(task_id, reason=f"Killed by admin {admin_id}")
        else:
            task = await getattr(tm, action)(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(404, detail=ErrorDetail(message="Task not found").model_dump()) from exc
    except InvalidTaskTransition as exc:
        # Same rules as the user API: a capability that can't be cancelled/paused is refused here too.
        raise HTTPException(409, detail=ErrorDetail(
            message=getattr(exc, "message", None) or str(exc),
            code=getattr(exc, "code", "invalid_state")).model_dump()) from exc
    svc.audit(admin_id, f"task_{action}", task_id=task_id)
    return TaskActionResponse(task=AdminTask.from_task(task))


@router.post("/tasks/{task_id}/kill", response_model=TaskActionResponse)
async def kill_task(task_id: str, admin_id: str = Depends(get_current_admin),
                    svc: AdminService = Depends(get_admin_service)) -> TaskActionResponse:
    """Cancel a task wherever it is running (the device is told to stop). If it belongs to a DAG,
    its dependents are skipped and independent nodes are untouched."""
    return await _task_action(svc, admin_id, task_id, "kill")


@router.post("/tasks/{task_id}/pause", response_model=TaskActionResponse)
async def pause_task(task_id: str, admin_id: str = Depends(get_current_admin),
                     svc: AdminService = Depends(get_admin_service)) -> TaskActionResponse:
    return await _task_action(svc, admin_id, task_id, "pause")


@router.post("/tasks/{task_id}/resume", response_model=TaskActionResponse)
async def resume_task(task_id: str, admin_id: str = Depends(get_current_admin),
                      svc: AdminService = Depends(get_admin_service)) -> TaskActionResponse:
    return await _task_action(svc, admin_id, task_id, "resume")


@router.post("/tasks/{task_id}/retry", response_model=TaskActionResponse)
async def retry_task(task_id: str, admin_id: str = Depends(get_current_admin),
                     svc: AdminService = Depends(get_admin_service)) -> TaskActionResponse:
    return await _task_action(svc, admin_id, task_id, "retry")


# ---------------------------------------------------------------------- DAGs

@router.get("/dags", response_model=DagListResponse)
async def list_dags(active_only: bool = False, days: Optional[float] = Query(None, gt=0),
                    limit: int = Query(500, ge=1, le=5000),
                    svc: AdminService = Depends(get_admin_service)) -> DagListResponse:
    """Live runs, then finished runs newest first. `days` limits how far back (default: the whole retained
    history, i.e. the task cleanup window); older runs than that are deleted."""
    return DagListResponse(runs=await svc.dag.list_runs(active_only=active_only, days=days, limit=limit),
                           retention_days=svc.history.retention_days if svc.history else None)


@router.get("/dags/{request_id}", response_model=DagSnapshot)
async def get_dag(request_id: str, svc: AdminService = Depends(get_admin_service)) -> DagSnapshot:
    snap = await svc.dag.snapshot(request_id)
    if snap is None:
        raise HTTPException(404, detail=ErrorDetail(message="No such DAG run (it may have aged out of history)").model_dump())
    return snap


@router.post("/dags/{request_id}/tasks/{spec_id}/kill", response_model=NodeKillResponse)
async def kill_dag_node(request_id: str, spec_id: str, admin_id: str = Depends(get_current_admin),
                        svc: AdminService = Depends(get_admin_service)) -> NodeKillResponse:
    """Kill ONE node (running or still pending). Independent nodes keep running."""
    outcome = await svc.dag.cancel(request_id, spec_id)
    if outcome == CancelOutcome.NOT_FOUND:
        raise HTTPException(404, detail=ErrorDetail(message="No such live DAG node").model_dump())
    svc.audit(admin_id, "dag_node_kill", request_id=request_id, spec_id=spec_id, outcome=outcome.value)
    return NodeKillResponse(request_id=request_id, spec_id=spec_id, outcome=outcome)


@router.post("/dags/{request_id}/kill", response_model=KillRunResult)
async def kill_dag(request_id: str, admin_id: str = Depends(get_current_admin),
                   svc: AdminService = Depends(get_admin_service)) -> KillRunResult:
    """Kill every unfinished node of a run."""
    result = await svc.dag.kill_run(request_id)
    if result is None:
        raise HTTPException(404, detail=ErrorDetail(message="No such live DAG run").model_dump())
    svc.audit(admin_id, "dag_kill", request_id=request_id, running=result.cancelled_running,
              pending=result.cancelled_pending)
    return result


# ----------------------------------------------------------------------- run

@router.post("/run/goal", response_model=RunAccepted, status_code=202)
async def run_goal(req: RunGoalRequest, admin_id: str = Depends(get_current_admin),
                   svc: AdminService = Depends(get_admin_service)) -> RunAccepted:
    """Run a natural-language goal through the orchestrator in the background. The plan's DAG
    appears under /dags/{request_id} once planning is done."""
    return await svc.run_goal(admin_id, req)


@router.post("/run/dag", response_model=RunAccepted, status_code=202)
async def run_dag(req: RunDagRequest, admin_id: str = Depends(get_current_admin),
                  svc: AdminService = Depends(get_admin_service)) -> RunAccepted:
    try:
        return await svc.run_dag(admin_id, req)
    except AdminError as exc:
        raise _fail(exc) from exc


@router.post("/run/demo/{kind}", response_model=RunAccepted, status_code=202)
async def run_demo(kind: DemoKind, scale: float = Query(1.0, gt=0, le=1.0, description="shrink all durations (0.1 = 10x faster)"),
                   admin_id: str = Depends(get_current_admin),
                   svc: AdminService = Depends(get_admin_service)) -> RunAccepted:
    try:
        return await svc.run_demo(admin_id, kind, scale)
    except AdminError as exc:
        raise _fail(exc) from exc


# -------------------------------------------------------------------- events

@router.get("/events/history")
async def events_history(days: Optional[float] = Query(None, gt=0), since: Optional[float] = None,
                         until: Optional[float] = None, text: str = Query("", max_length=200),
                         limit: int = Query(500, ge=1, le=5000),
                         svc: AdminService = Depends(get_admin_service)) -> Dict[str, Any]:
    """Recorded task.* / dag.* events, oldest first (the newest `limit` in the window). The same events the
    live stream sends, kept for the retention window so the Events tab is not empty when opened later."""
    if svc.history is None:
        return {"retention_days": None, "events": [], "enabled": False}
    return {"retention_days": svc.history.retention_days, "enabled": True,
            "events": svc.history.read_events(days=days, since=since, until=until, text=text, limit=limit)}


@router.get("/history/info")
async def history_info(svc: AdminService = Depends(get_admin_service)) -> Dict[str, Any]:
    return svc.history.info() if svc.history else {"enabled": False}


@router.get("/events")
async def events(request: Request, svc: AdminService = Depends(get_admin_service)) -> StreamingResponse:
    """SSE of task.* and dag.* events from ALL sessions. The client authenticates with the
    Authorization header (fetch streaming) — no token in the URL."""
    stream = AdminEventStream(svc.orchestrator.event_bus)

    async def body() -> AsyncIterator[str]:
        async with stream:
            async for frame in admin_sse(stream, is_disconnected=request.is_disconnected):
                yield frame

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------- test helpers

def _voice(svc: AdminService):
    v = svc.voice
    if v is None or not v.available:
        raise HTTPException(503, detail=ErrorDetail(
            message="voice is unavailable: " + (v.unavailable_reason if v else "not configured")).model_dump())
    return v


@router.get("/test/voice-status", response_model=VoiceStatus)
async def voice_status(svc: AdminService = Depends(get_admin_service)) -> VoiceStatus:
    v = svc.voice
    cfg = v.config if v else None
    ok = bool(v and v.available)
    return VoiceStatus(
        available=ok, reason="" if ok else (v.unavailable_reason if v else "voice is not configured"),
        stt=v.stt.name if ok else None, tts=v.tts.name if ok else None,
        partial_transcripts=bool(ok and v.stt.supports_partials),
        input_sample_rate=cfg.input_sample_rate if cfg else 16000,
        output_sample_rate=(v.tts.sample_rate if ok else (cfg.output_sample_rate if cfg else 24000)))


@router.post("/test/tts", responses={200: {"content": {"audio/wav": {}}}})
async def test_tts(req: TtsRequest, admin_id: str = Depends(get_current_admin),
                   svc: AdminService = Depends(get_admin_service)) -> Response:
    """Speak ``text`` with the configured TTS provider; returns a WAV file."""
    if len(req.text) > svc.config.max_test_text_chars:
        raise HTTPException(413, detail=ErrorDetail(
            message=f"text too long (max {svc.config.max_test_text_chars} characters)").model_dump())
    v = _voice(svc)
    chunks, total = [], 0
    max_bytes = int(MAX_TTS_SECONDS * v.tts.sample_rate * 2)
    try:
        import contextlib
        async with contextlib.aclosing(v.tts.synthesize(req.text, voice=req.voice, language=None)) as audio:
            async for pcm in audio:
                total += len(pcm)
                if total > max_bytes:
                    break
                chunks.append(pcm)
    except Exception as exc:
        logger.warning("admin tts test failed: {}", exc)
        raise HTTPException(502, detail=ErrorDetail(message=f"TTS failed: {exc}").model_dump()) from exc
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(v.tts.sample_rate)
        w.writeframes(b"".join(chunks))
    svc.audit(admin_id, "test_tts", chars=len(req.text), provider=v.tts.name)
    return Response(content=buf.getvalue(), media_type="audio/wav")


async def _read_capped(request: Request, limit: int) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(413, detail=ErrorDetail(message=f"upload too large (max {limit} bytes)").model_dump())
    return bytes(data)


def _to_mono_pcm16(raw: bytes, content_type: str, sample_rate: int) -> tuple[bytes, int]:
    """WAV (any rate, mono/stereo, 16-bit) or raw PCM16 mono -> (PCM16 mono, rate)."""
    if "wav" in content_type or raw[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(raw)) as w:
                if w.getsampwidth() != 2:
                    raise ValueError("only 16-bit WAV is supported")
                rate, ch, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
        except (wave.Error, EOFError, ValueError) as exc:
            raise HTTPException(415, detail=ErrorDetail(message=f"unreadable WAV: {exc}").model_dump()) from exc
        if ch == 2:
            x = np.frombuffer(frames[: len(frames) // 4 * 4], dtype="<i2").reshape(-1, 2).mean(axis=1)
            frames = x.astype("<i2").tobytes()
        elif ch != 1:
            raise HTTPException(415, detail=ErrorDetail(message="only mono or stereo audio is supported").model_dump())
        return frames, rate
    return raw[: len(raw) // 2 * 2], sample_rate


@router.post("/test/stt", response_model=SttResponse)
async def test_stt(request: Request, sample_rate: int = Query(16000, ge=8000, le=48000),
                   admin_id: str = Depends(get_current_admin),
                   svc: AdminService = Depends(get_admin_service)) -> SttResponse:
    """Transcribe an uploaded clip. Body = raw bytes: a WAV file (audio/wav) or raw PCM16 mono
    (application/octet-stream, rate from ?sample_rate=)."""
    import time
    v = _voice(svc)
    raw = await _read_capped(request, svc.config.max_test_upload_bytes)
    if not raw:
        raise HTTPException(400, detail=ErrorDetail(message="empty body").model_dump())
    pcm, rate = _to_mono_pcm16(raw, request.headers.get("content-type", ""), sample_rate)
    pcm = resample(pcm, rate, STT_TARGET_RATE)
    t0 = time.perf_counter()
    stream = await v.stt.open(sample_rate=STT_TARGET_RATE, language=None)
    try:
        for i in range(0, len(pcm), 3200):                      # 100 ms frames, like a live mic
            await stream.push(pcm[i:i + 3200])
        text = await stream.finish()
    finally:
        await stream.aclose()
    svc.audit(admin_id, "test_stt", bytes=len(raw), provider=v.stt.name)
    return SttResponse(text=text, audio_seconds=round(len(pcm) / 2 / STT_TARGET_RATE, 2),
                       elapsed_ms=round((time.perf_counter() - t0) * 1000, 1), provider=v.stt.name)


@router.post("/test/llm")
async def test_llm(req: LlmTestRequest, request: Request, admin_id: str = Depends(get_current_admin),
                   svc: AdminService = Depends(get_admin_service)) -> StreamingResponse:
    """Stream a raw LLM completion as SSE (`delta`, `done`, `error`) — proves token streaming works."""
    import json
    svc.audit(admin_id, "test_llm", chars=len(req.prompt))
    llm = svc.orchestrator.planner.llm

    async def body() -> AsyncIterator[str]:
        try:
            async for chunk in llm.stream_request(LLMRequest(
                    messages=[LLMMessage(role="user", content=req.prompt)], max_tokens=256, temperature=0.7)):
                if chunk.delta:
                    yield f"event: delta\ndata: {json.dumps({'delta': chunk.delta})}\n\n"
                if chunk.finish_reason:
                    yield f"event: done\ndata: {json.dumps({'finish_reason': chunk.finish_reason})}\n\n"
                if await request.is_disconnected():
                    return
        except Exception as exc:
            logger.warning("admin llm test failed: {}", exc)
            yield f"event: error\ndata: {json.dumps({'message': str(exc)[:300]})}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
