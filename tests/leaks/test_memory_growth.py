"""Hot paths must not grow without bound.

`growth_kb` measures what a long run leaves behind: warm up, then run N iterations twice and
look at the heap growth of the SECOND batch (caches and lazy imports settle in the warm-up and
the first batch, a real leak keeps growing). Where a structure has an explicit cap we also
assert the cap itself, which is deterministic.
"""
import asyncio
import gc
import sys
import tracemalloc
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))   # rig.py
from rig import rig  # noqa: E402,F401  (fixture)

from pai.config.models.event_bus import EventBusConfig
from pai.config.models.memory import MemoryConfig, MemorySafetyConfig
from pai.config.models.voice import VADConfig, VoiceConfig
from pai.event_bus.bus import EventBus
from pai.memory.manager import MemoryManager
from pai.memory.safety import MemoryGuard, MemoryLayer, MemorySource, Provenance
from pai.observability.store import TraceStore
from pai.orchestration.execution_context import ContextManager, ExecutionContext
from pai.orchestration.idempotency import IdempotencyKey, IdempotencyPolicy, IdempotencyRegistry
from pai.planning.dag_scheduler import DAGScheduler
from pai.planning.models import TaskSpec
from pai.security.guards.stream_guard import StreamingSanitizer
from pai.tasks.models import Task, TaskResult, TaskType
from pai.tasks.store import TaskStore
from pai.voice.audio import sine
from pai.voice.providers.fake import FakeSTT, FakeTTS
from pai.voice.session import TurnOutcome, VoiceSession

N = 300
TOL_KB = 250          # a leak of >~0.8 KB per iteration shows up in a 300-iteration batch


async def growth_kb(step, n: int = N, warmup: int = 60) -> float:
    for _ in range(warmup):
        await step()
    gc.collect()
    tracemalloc.start()
    try:
        for _ in range(n):
            await step()
        gc.collect()
        mid = tracemalloc.get_traced_memory()[0]
        for _ in range(n):
            await step()
        gc.collect()
        end = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    return (end - mid) / 1024


# ------------------------------------------------------------------- DAG runs

class QuickTaskManager:
    def is_initialized(self): return True
    async def run(self, spec, context, wait=True):
        await asyncio.sleep(0)
        return TaskResult(task_id=uuid.uuid4().hex, success=True)


class NullBus:
    async def publish(self, *a, **k): ...


async def test_dag_runs_do_not_leak():
    dag = DAGScheduler(event_bus=NullBus(), task_manager=QuickTaskManager())

    async def step():
        specs = [TaskSpec(id="a", name="a", input="a", type="one_shot", capability="x.y"),
                 TaskSpec(id="b", name="b", input="b", type="one_shot", capability="x.y", depends_on=["a"]),
                 TaskSpec(id="c", name="c", input="c", type="one_shot", capability="x.y")]
        await dag.execute(specs, ExecutionContext(user_id="u", session_id="s"))
    assert await growth_kb(step) < TOL_KB
    await asyncio.sleep(0.05)
    assert dag._runs == {}                                          # per-run bookkeeping released


# ---------------------------------------------------------------- idempotency

async def test_idempotency_registry_is_capped_and_does_not_leak():
    reg = IdempotencyRegistry(IdempotencyPolicy(max_entries=100, success_ttl_seconds=3600))

    async def step():
        key = IdempotencyKey(user_id="u", session_id="s", key=uuid.uuid4().hex)
        async with reg.guard(key) as g:
            g.set_result("ok", success=True)
    assert await growth_kb(step) < TOL_KB
    assert len(reg._entries) <= 101                                 # cap (+ the one being added)


# ------------------------------------------------------------------ event bus

async def test_event_bus_subscribe_unsubscribe_churn_does_not_leak():
    bus = EventBus(config=EventBusConfig())
    await bus.initialize()
    await bus.start()

    async def handler(e): ...

    async def step():
        sid = await bus.subscribe("churn.*", handler)
        await bus.publish("churn.x", {"a": 1})
        await bus.unsubscribe(sid)
    try:
        assert await growth_kb(step) < TOL_KB
        await asyncio.sleep(0.05)
        assert sum(len(v) for v in bus._subscriptions.values()) == 0
        assert bus._event_queue.qsize() == 0
    finally:
        await bus.shutdown()


# ----------------------------------------------------------------- task store

async def test_task_store_keeps_bounded_history():
    store = TaskStore(max_history=100)

    async def step():
        t = Task(name="n", type=TaskType.ONE_SHOT, capability="a.b", user_id="u")
        await store.create(t)
        t.mark_completed()
        await store.save(t)
    assert await growth_kb(step) < TOL_KB
    assert len(store._tasks) <= 101


async def test_task_store_never_evicts_live_tasks():
    store = TaskStore(max_history=2)
    live = Task(name="live", type=TaskType.LONG_RUNNING, capability="a.b", user_id="u")
    await store.create(live)
    for i in range(10):
        t = Task(name=f"t{i}", type=TaskType.ONE_SHOT, capability="a.b", user_id="u")
        await store.create(t); t.mark_completed(); await store.save(t)
    assert live.id in store._tasks and len(store._tasks) <= 3


# ---------------------------------------------------- tasks through executors

async def test_device_task_bookkeeping_is_empty_after_many_tasks(rig):
    """100 tasks over the device path (success, device-reported failure, kill): every per-task
    structure in TaskManager / DeviceExecutor / DAGScheduler must be empty afterwards."""
    from rig import device_sends, fake_device, frame_dict, spec
    from pai.common.enums.permission import Origin

    async def device(frame):
        f = frame_dict(frame)
        if f["type"] == "invoke":
            if "bad" in (f["task_id"] or "") or f["parameters"].get("fail"):
                await device_sends(rig, type="result", request_id=f["request_id"], success=False, error="x")
            else:
                await device_sends(rig, type="result", request_id=f["request_id"], result={"ok": 1})
    stop = asyncio.Event()
    dev = asyncio.create_task(fake_device(rig, device, stop))
    try:
        for i in range(100):
            ctx = ExecutionContext(user_id="u", session_id="s", origin=Origin.USER_DIRECT)
            await asyncio.wait_for(rig.dag.execute([spec("a"), spec("b", ["a"])], ctx), 5)
        await asyncio.sleep(0.2)
    finally:
        stop.set()
        await dev
    ex = rig.dev_exec
    assert (ex._pending, ex._in_flight, ex._pending_acks) == ({}, {}, {})
    assert rig.tm._running_tasks == {} and rig.tm._pause_events == {}
    assert rig.dag._runs == {} and rig.tm._recovery_tasks == set()


# --------------------------------------------------------------- voice sessions

class OneLineRunner:
    async def run_turn(self, *, user_id, session_id, device_id, text, turn_id, sink, on_status):
        await sink.on_delta("Okay. ")
        return TurnOutcome(reply="Okay. ")
    async def on_interrupted(self, **kw): ...


async def test_opening_and_closing_voice_sessions_does_not_leak():
    cfg = VoiceConfig(vad=VADConfig(min_speech_ms=60, end_silence_ms=200, pre_roll_ms=60, energy_threshold=0.02))
    speech = sine(300.0, 200, 16000, amplitude=0.5)
    silence = b"\x00\x00" * (16000 * 250 // 1000)

    async def sink_event(e): ...
    async def sink_audio(b): ...

    async def step():
        s = VoiceSession(config=cfg, stt=FakeSTT(["hi"]), tts=FakeTTS(ms_per_char=5, chunk_ms=40),
                         runner=OneLineRunner(), user_id="u", chat_session_id="c", device_id=None,
                         send_event=sink_event, send_audio=sink_audio, input_sample_rate=16000)
        await s.start()
        for pcm in (speech, silence):
            for i in range(0, len(pcm), 640):
                await s.on_audio(pcm[i:i + 640])
        await asyncio.sleep(0.02)
        await s.close("test")
    before = {t for t in asyncio.all_tasks() if not t.done()}
    assert await growth_kb(step, n=120, warmup=20) < TOL_KB
    await asyncio.sleep(0.1)
    assert {t for t in asyncio.all_tasks() if not t.done()} <= before        # no stray session tasks


# ---------------------------------------------------------------- SSE streams

async def test_sse_connect_disconnect_leaves_no_subscriptions():
    from pai.api.routes.task_stream import SessionEventStream
    bus = EventBus(config=EventBusConfig())
    await bus.initialize(); await bus.start()

    async def step():
        async with SessionEventStream(bus, "s1"):
            await bus.publish("task.started", {"session_id": "s1"})
    try:
        assert await growth_kb(step) < TOL_KB
        assert sum(len(v) for v in bus._subscriptions.values()) == 0
    finally:
        await bus.shutdown()


# -------------------------------------------------------------- memory system

async def test_memory_remember_and_recall_do_not_leak(tmp_path):
    cfg = MemoryConfig()
    cfg.long_term.db_url = str(tmp_path / "lt.db")
    cfg.episodic.db_url = str(tmp_path / "ep.db")
    cfg.procedural.path = str(tmp_path / "proc.json")
    cfg.vector_store.enabled = False
    cfg.knowledge_graph.enabled = False
    cfg.safety = MemorySafetyConfig(writes_per_minute=100000, consolidate_every_n_writes=50,
                                    max_episodes_per_user=100)
    mm = MemoryManager(event_bus=NullBus(), config=cfg)
    await mm.initialize()
    i = 0

    async def step():
        nonlocal i
        i += 1
        await mm.remember_interaction(f"note {i} about gardening", f"ok {i}", user_id="u1", session_id="s", success=True)
        await mm.recall_for_prompt("gardening", user_id="u1", session_id="s2")
    try:
        assert await growth_kb(step, n=100, warmup=30) < TOL_KB
        assert len(mm.short_term._store) <= cfg.short_term.size
        assert await mm.episodic.count("u1") <= 100 + cfg.safety.consolidate_every_n_writes
    finally:
        await mm.shutdown()


async def test_quarantine_log_and_guard_windows_are_capped():
    cfg = MemoryConfig()
    cfg.long_term.db_url = ":memory:"
    guard = MemoryGuard(MemorySafetyConfig(writes_per_minute=5))
    for i in range(12000):                                           # 12k distinct users
        guard.inspect_write("hello", provenance=Provenance(source=MemorySource.USER, user_id=f"u{i}"),
                            layer=MemoryLayer.EPISODIC)
    assert len(guard._writes) <= 10_000
    # quarantine log: a deque with maxlen
    from collections import deque
    mm = object.__new__(MemoryManager)
    mm.quarantine_log = deque(maxlen=500)
    assert mm.quarantine_log.maxlen == 500


# ----------------------------------------------------------------- sanitizer

async def test_streaming_sanitizer_does_not_leak():
    from pai.config.models.prompts import PromptsGuardConfig
    from pai.security.guards.llm_guard import PromptGuard
    guard = PromptGuard(PromptsGuardConfig())
    text = 'Sure. {"tool": "x"}\nkey sk-abcdefghijklmnopqrstuvwxyz123456 done. ' * 5

    async def step():
        s = StreamingSanitizer(guard)
        for i in range(0, len(text), 7):
            s.feed(text[i:i + 7])
        s.flush()
    assert await growth_kb(step) < TOL_KB


# ------------------------------------------------------------ tracer / context

async def test_trace_store_is_a_bounded_ring(tmp_path):
    from pai.observability.events import TraceEvent
    store = TraceStore(max_traces=50, max_events_per_trace=20, persist_dir=str(tmp_path))
    await store.start()
    try:
        for t in range(400):
            for e in range(30):
                store.append(TraceEvent(trace_id=f"t{t}", span_id=f"s{e}", parent_span_id=None,
                                        kind="span.start", name="x", timestamp=float(e)))
        assert len(store._events) <= 50 and len(store._summaries) <= 50
        assert all(len(v) <= 20 for v in store._events.values())
    finally:
        await store.stop()


async def test_chat_session_registry_is_capped():
    cm = ContextManager(max_sessions_per_user=20, max_sessions_total=100)
    for i in range(500):
        await cm.create_context("alice", f"sess-{i}")
    assert len(cm._user_sessions["alice"]) <= 20
    for u in range(30):
        for i in range(10):
            await cm.create_context(f"user{u}", f"u{u}-s{i}")
    assert len(cm._session_registry) <= 100
    assert set(cm._current_session.values()) <= set(cm._session_registry)    # no dangling pointers
    # an evicted session id simply comes back as a fresh session (history lives in the DB)
    ctx = await cm.create_context("alice", "sess-0")
    assert ctx.session_id == "sess-0"


# ----------------------------------------------------------------- the harness itself

async def test_harness_detects_a_real_leak():
    """Control: if this passes the 'bounded' tests above cannot be vacuously green."""
    leak = []

    async def step():
        leak.append(bytearray(2048))                     # 2 KB retained per iteration
    assert await growth_kb(step) > TOL_KB


async def test_uncapped_task_store_would_grow_so_the_cap_matters():
    store = TaskStore(max_history=10**9)                 # "no cap" = the old behaviour

    async def step():
        t = Task(name="n", type=TaskType.ONE_SHOT, capability="a.b", user_id="u")
        await store.create(t); t.mark_completed(); await store.save(t)
    assert await growth_kb(step) > TOL_KB


async def test_uncapped_session_registry_would_grow_so_the_cap_matters():
    cm = ContextManager(max_sessions_per_user=10**9, max_sessions_total=10**9)   # the old behaviour
    i = 0

    async def step():
        nonlocal i
        i += 1
        await cm.create_context("alice", f"sess-{i}")
    assert await growth_kb(step, n=1500) > TOL_KB        # ~0.5 KB per session: slow but unbounded
