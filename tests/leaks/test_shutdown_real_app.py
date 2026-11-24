"""Boot the REAL app, use every kind of long-lived resource, shut it down with work in flight,
then prove nothing was left behind and in-flight work stayed recoverable."""
import asyncio
import contextlib
import gc
import json
import sqlite3
import tracemalloc

import httpx
import pytest

from pai.api.routes import voice as voice_routes
from pai.api.routes.task_stream import SessionEventStream, sse_events
from pai.config.models.voice import VADConfig, VoiceConfig
from pai.orchestration.builtin_capabilities import BuiltinCapability
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.models import TaskSpec
from pai.security.tokens.auth import create_access_token
from pai.server.factory import create_application
from pai.tasks.models import TaskStatus, TaskType
from pai.voice.audio import sine
from pai.voice.providers.fake import FakeSTT, FakeTTS
from pai.voice.service import VoiceService
from pai.voice.session import TurnOutcome

from helpers import FakeWebSocket, isolated_config, leftover_tasks, open_fd_targets, settle, thread_snapshot

pytestmark = pytest.mark.asyncio


class EchoRunner:
    async def ensure_chat_session(self, user_id, session_id): ...
    async def run_turn(self, *, user_id, session_id, device_id, text, turn_id, sink, on_status):
        await sink.on_delta("Hello there. ")
        return TurnOutcome(reply="Hello there. ")
    async def on_interrupted(self, **kw): ...


async def _sleep(params, *, user_id=None, task=None):
    await asyncio.sleep(float(params.get("seconds", 0)))
    return {"slept": params.get("seconds", 0)}


def _spec(i, seconds, deps=(), kind=TaskType.ONE_SHOT):
    return TaskSpec(id=i, name=i, input=i, type=kind, capability="test.sleep",
                    parameters={"seconds": seconds}, depends_on=list(deps), timeout=30)


async def test_real_app_shutdown_leaves_nothing_behind_and_inflight_tasks_stay_recoverable(tmp_path):
    config = isolated_config(tmp_path)
    db_file = tmp_path / "pai.db"

    me = asyncio.current_task()
    threads_before = thread_snapshot()
    app = create_application(config)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    live_connections = {}                      # DB connections currently checked out -> where from
    try:
        ctx = app.state.context
        orch = ctx.orchestrator

        from sqlalchemy import event
        import traceback
        engine = ctx.database.engine.sync_engine

        @event.listens_for(engine, "checkout")
        def _checkout(dbapi, record, proxy):
            live_connections[id(record)] = "".join(traceback.format_stack(limit=18)[:-1])

        @event.listens_for(engine, "checkin")
        def _checkin(dbapi, record):
            live_connections.pop(id(record), None)

        # a controllable capability, registered like any builtin
        orch.executor_manager.get("builtin_executor").registry.register(BuiltinCapability(
            id="test.sleep", description="sleep", handler=_sleep,
            parameters={"type": "object", "properties": {"seconds": {"type": "number"}}}))
        orch.capability_router.invalidate()

        token = create_access_token("leak-user")
        auth = {"Authorization": f"Bearer {token}"}

        # ---- HTTP: list, chat (command path, no LLM), streaming chat, idempotent replay ----
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            assert (await http.get("/api/v1/tasks", headers=auth)).status_code == 200
            body = {"device_id": "d1", "message": "/help", "mode": "text"}
            assert (await http.post("/api/v1/chat", json=body, headers=auth)).status_code == 200
            keyed = {**auth, "Idempotency-Key": "leak-key-1"}
            for _ in range(2):                                  # second one is a replay
                assert (await http.post("/api/v1/chat", json=body, headers=keyed)).status_code == 200
            stream = await http.post("/api/v1/chat/stream", json=body, headers=auth)
            assert stream.status_code == 200 and "event: done" in stream.text

            # ---- the real request path must not grow with traffic ----
            from pai.observability.tracer import get_tracer
            store = get_tracer().store
            store.max_traces = 40                      # small ring: it fills in a few requests
            sess = 0

            async def hit():
                nonlocal sess
                sess += 1                                                 # a NEW session every time
                b = {"device_id": "d1", "message": "/help", "mode": "text", "session_id": f"g-{sess}"}
                await http.get("/api/v1/tasks", headers=auth)
                await http.post("/api/v1/chat", json=b, headers=auth)
                await http.post("/api/v1/chat/stream", json=b, headers=auth)

            for _ in range(100):                                         # fill the trace ring buffer first
                if len(store._events) >= store.max_traces:
                    break
                await hit()
            gc.collect()
            tracemalloc.start()
            try:
                for _ in range(40):
                    await hit()
                gc.collect()
                mid = tracemalloc.get_traced_memory()[0]
                for _ in range(40):
                    await hit()
                gc.collect()
                growth_kb = (tracemalloc.get_traced_memory()[0] - mid) / 1024
            finally:
                tracemalloc.stop()
            assert growth_kb < 800, f"real request path grew {growth_kb:.0f} KB over 120 requests"
            assert len(store._events) <= store.max_traces
            assert len(orch.context_manager._session_registry) <= orch.context_manager.max_sessions_total

        # ---- a DAG with a fast part, and a kill ----
        ectx = ExecutionContext(user_id="leak-user", session_id="leak-session")
        done = await orch.dag_scheduler.execute(
            [_spec("a", 0.01), _spec("b", 0.01, ["a"]), _spec("c", 0.01)], ectx)
        assert done.status.value == "completed", done

        kctx = ExecutionContext(user_id="leak-user", session_id="leak-session")
        killed = asyncio.create_task(orch.dag_scheduler.execute([_spec("k", 30)], kctx))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any(t.capability == "test.sleep" for t in await orch.task_manager.list(status=TaskStatus.RUNNING)):
                break
        from pai.planning.dag_models import CancelOutcome
        assert await orch.dag_scheduler.cancel(kctx.request_id, "k") == CancelOutcome.CANCELLED_RUNNING
        assert (await asyncio.wait_for(killed, 5)).status.value == "failed"
        assert orch.dag_scheduler._runs == {}                   # run bookkeeping released

        # ---- an SSE stream that is open when the server stops ----
        sse = SessionEventStream(orch.event_bus, "leak-session")
        await sse.__aenter__()

        async def never_disconnected(): return False

        async def drain():
            async for _ in sse_events(sse, session_id="leak-session", is_disconnected=never_disconnected,
                                      heartbeat_seconds=0.2, should_stop=ctx.shutting_down.is_set):
                pass
            await sse.__aexit__(None, None, None)
        sse_task = asyncio.create_task(drain())

        # ---- a live voice session (fakes) that is open when the server stops ----
        vcfg = VoiceConfig(vad=VADConfig(min_speech_ms=60, end_silence_ms=200, pre_roll_ms=60,
                                         energy_threshold=0.02))
        ctx.voice = VoiceService(vcfg, stt=FakeSTT(["hello"]), tts=FakeTTS(), runner=EchoRunner())
        ws = FakeWebSocket(app, {"token": token})
        voice_task = asyncio.create_task(voice_routes.voice_stream(ws))
        ws.client_sends_text(json.dumps({"type": "session.start"}))
        ws.client_sends_audio(sine(300.0, 300, 16000, amplitude=0.5))
        await settle(0.3)
        assert ctx.voice.active_sessions == 1 and any('"session.ready"' in t for t in ws.sent_text)

        # ---- work still running when shutdown begins ----
        inflight_ctx = ExecutionContext(user_id="leak-user", session_id="leak-session")
        inflight = asyncio.create_task(orch.dag_scheduler.execute(
            [_spec("long", 60, kind=TaskType.LONG_RUNNING)], inflight_ctx))
        for _ in range(100):
            await asyncio.sleep(0.05)
            running = [t for t in await orch.task_manager.list(status=TaskStatus.RUNNING)
                       if t.spec_id == "long"]
            if running:
                break
        assert running, "the long task never started"
        long_task_id = running[0].id
    finally:
        await lifespan.__aexit__(None, None, None)            # ---- SHUTDOWN ----

    await settle(0.5)
    report = app.state.shutdown_report

    # shutdown itself: every step ok, within budget
    assert report.clean, report.summary()
    assert {s.name for s in report.steps} >= {"voice", "scheduler", "orchestrator", "database"}

    # the long-lived streams ended by themselves, and told their clients why
    await asyncio.wait_for(sse_task, 2)
    await asyncio.wait_for(voice_task, 2)
    assert ws.closed.is_set()
    ended = [json.loads(t) for t in ws.sent_text if t.startswith("{")]
    assert any(e.get("type") == "session.ended" and e.get("reason") == "server_shutdown" for e in ended)
    assert (await asyncio.wait_for(inflight, 5)).status.value == "failed"      # DAG ended, did not hang

    # in-flight work stayed RECOVERABLE (initialized, interrupted), not user-cancelled
    with contextlib.closing(sqlite3.connect(db_file)) as conn:
        row = conn.execute("select status, payload from tasks where id=?", (long_task_id,)).fetchone()
    payload = json.loads(row[1])
    assert row[0] == "initialized" and payload["recovery_count"] == 1, row[0]

    # nothing left behind
    assert orch.event_bus._subscriptions == {} or all(not v for v in orch.event_bus._subscriptions.values())
    assert leftover_tasks(me) == [], [t.get_name() for t in leftover_tasks(me)]
    new_threads = {t for t in thread_snapshot() - threads_before
                   if t.endswith("|False") and not t.startswith(("AnyIO worker", "asyncio_"))}
    assert not new_threads, new_threads
    assert not live_connections, "DB connection(s) never returned:\n" + "\n----\n".join(
        v[-2500:] for v in live_connections.values())
    held = [p for p in open_fd_targets()
            if str(tmp_path) in p or p.endswith("chroma.sqlite3")]
    assert not held, f"files still open after shutdown: {held}"
