"""Text chat streams: sanitizer, orchestrator reply streaming, SSE endpoint."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from pai.api.routes import chat as chat_routes
from pai.config.models.prompts import PromptRegistryConfig, PromptsGuardConfig
from pai.llm.models import LLMStreamChunk
from pai.orchestration.execution_context import ExecutionContext
from pai.orchestration.orchestrator import TaskOrchestrator
from pai.orchestration.idempotency import IdempotencyRegistry
from pai.orchestration.streaming import QueueReplySink
from pai.planning.models import TaskSpec
from pai.prompts.registry import PromptRegistry
from pai.security.guards.llm_guard import PromptGuard
from pai.security.guards.stream_guard import StreamingSanitizer


# ----------------------------- sanitizer ------------------------------------

def run_sanitizer(text, step):
    s = StreamingSanitizer(PromptGuard(PromptsGuardConfig()))
    out = [s.feed(text[i:i + step]) for i in range(0, len(text), step)] + [s.flush()]
    return "".join(out), s


@pytest.mark.parametrize("step", [1, 2, 3, 7, 50])
def test_secrets_and_tool_envelopes_never_leak_at_any_chunking(step):
    text = ('Sure.\n{"tool": "camera.capture", "args": {}}\nHere is your key sk-abcdefghijklmnopqrstuvwxyz123456 '
            'and Bearer abcdefghijklmnopqrstuvwxyz0123456789 ok.\n```json\n{"capability": "x"}\n```\nDone.')
    out, stats = run_sanitizer(text, step)
    assert "sk-" not in out and "camera.capture" not in out and '"capability"' not in out
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "Sure." in out and "Done." in out and "[REDACTED:openai_key]" in out
    assert stats.stats.secrets_redacted == 2 and stats.stats.tool_calls_stripped >= 2


def test_plain_text_is_released_incrementally_and_unchanged():
    s = StreamingSanitizer(PromptGuard(PromptsGuardConfig()))
    first = s.feed("Hello there, ")
    assert first.startswith("Hello")             # words are released as they complete
    rest = s.feed("how are you doing today?") + s.flush()
    assert first + rest == "Hello there, how are you doing today?"


def test_a_reply_that_is_only_a_tool_envelope_needs_fallback():
    out, s = run_sanitizer('{"tool": "x", "args": {}}', 4)
    assert out.strip() == "" and s.needs_fallback()


# ------------------------- orchestrator streaming ---------------------------

class FakeLLM:
    def __init__(self, deltas): self.deltas, self.cancelled = deltas, False
    async def stream_request(self, request, route=None):
        try:
            for d in self.deltas:
                await asyncio.sleep(0)
                yield LLMStreamChunk(delta=d)
            yield LLMStreamChunk(finish_reason="stop")
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Tracker:
    async def record(self, **kw): ...


def make_orchestrator(deltas):
    o = object.__new__(TaskOrchestrator)
    o.capability_router = None
    o.chat_repo = None
    o.memory_manager = None
    o.prompt_guard = PromptGuard(PromptsGuardConfig())
    o.prompts = PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True))
    o.prompt_tracker = Tracker()
    o.planner = SimpleNamespace(llm=FakeLLM(deltas))
    o.goal_tokens_trace_size, o.memory_recall_limit, o.memory_block_max_size = 300, 5, 4000
    o.tool_summary_block_max_size, o.prompt_trace_max_size = 4000, 4000
    o.chat_reply_max_tokens, o.chat_reply_temperature = 500, 0.7
    o.published = []

    async def _publish(event_type, data=None, **kw):
        o.published.append((getattr(event_type, "value", event_type), data))
    o._publish = _publish
    return o


async def test_chat_reply_streams_sanitized_deltas_to_sink_and_bus():
    o = make_orchestrator(["Hi! ", "Your key is sk-abcdefghijklmnopqrstuvwxyz123456 ", "- keep it safe."])
    sink = QueueReplySink()
    ctx = ExecutionContext(user_id="u", session_id="s")
    task = TaskSpec(id="t", name="chat", input="hi", type="one_shot", capability="chat.reply")
    reply = await o._chat_reply(goal="hello", task=task, context=ctx, prior_results=[], reply_sink=sink)

    items = []
    while not sink._queue.empty():
        items.append(sink._queue.get_nowait())
    deltas = [i.text for i in items if i.kind == "delta"]
    assert len(deltas) >= 2                                   # genuinely incremental
    assert "".join(deltas) == reply
    assert "sk-" not in reply and "[REDACTED:openai_key]" in reply
    assert [i.kind for i in items][-1] == "done" and items[-1].text == reply
    events = [e for e, _ in o.published]
    assert events.count("chat.delta") == len(deltas) and events[-1] == "chat.reply_done"
    assert [d["seq"] for e, d in o.published if e == "chat.delta"] == list(range(1, len(deltas) + 1))


async def test_chat_reply_falls_back_when_everything_is_stripped():
    o = make_orchestrator(['{"tool": "x", "args": {}}'])
    ctx = ExecutionContext(user_id="u", session_id="s")
    task = TaskSpec(id="t", name="chat", input="hi", type="one_shot", capability="chat.reply")
    reply = await o._chat_reply(goal="hello", task=task, context=ctx, prior_results=[])
    assert reply == o.prompt_guard.fallback_reply


async def test_voice_mode_uses_voice_prompt_and_cancel_stops_the_llm():
    o = make_orchestrator(["a "] * 1000)
    seen = {}
    real_stream = o.planner.llm.stream_request

    async def spy(request, **kw):
        seen["prompt"] = request.messages[0].content
        seen["route"] = kw.get("route")
        async for c in real_stream(request, **kw):
            yield c
    o.planner.llm.stream_request = spy
    ctx = ExecutionContext(user_id="u", session_id="s", metadata={"mode": "voice"})
    task = TaskSpec(id="t", name="chat", input="hi", type="one_shot", capability="chat.reply")
    run = asyncio.create_task(o._chat_reply(goal="hello", task=task, context=ctx, prior_results=[]))
    await asyncio.sleep(0.01)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert "VOICE OUTPUT" in seen["prompt"]
    assert o.planner.llm.cancelled


# ------------------------------- SSE endpoint --------------------------------

class FakeBus:
    def __init__(self): self.subs = {}
    async def subscribe(self, pattern, handler): self.subs[len(self.subs)] = handler; return len(self.subs) - 1
    async def unsubscribe(self, sid): self.subs.pop(sid, None)


class FakeDB:
    def session(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): ...


async def collect(response):
    frames = []
    async for chunk in response.body_iterator:
        frames.append(chunk)
    return "".join(frames)


def parse(sse_text):
    events = []
    for block in sse_text.split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "): ev = line[7:]
            if line.startswith("data: "): data = json.loads(line[6:])
        if ev:
            events.append((ev, data))
    return events


async def test_sse_endpoint_streams_status_deltas_then_done(monkeypatch):
    bus = FakeBus()
    ctx = SimpleNamespace(database=FakeDB(), orchestrator=SimpleNamespace(
        event_bus=bus, idempotency=IdempotencyRegistry()))

    async def fake_run_chat(request, user_id, orchestrator, db, *, request_id, reply_sink=None):
        for h in list(bus.subs.values()):                       # a status event for THIS request
            await h(SimpleNamespace(data={"request_id": request_id, "stage": "planning", "message": "…"}))
            await h(SimpleNamespace(data={"request_id": "someone-else", "stage": "x"}))
        for piece in ("Hel", "lo ", "there"):
            await reply_sink.on_delta(piece)
        await reply_sink.on_done("Hello there")
        return chat_routes.ChatResponse(message="Hello there", status="completed", session_id="s1", tasks=[])
    monkeypatch.setattr(chat_routes, "_run_chat", fake_run_chat)

    http = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    body = chat_routes.ChatRequest(device_id="d", message="hi")
    resp = await chat_routes.chat_stream(body, http, user_id="u", context=ctx, idempotency_key=None)
    assert resp.media_type == "text/event-stream"
    events = parse(await collect(resp))

    assert [e for e, _ in events] == ["status", "delta", "delta", "delta", "done"]
    assert "".join(d["delta"] for e, d in events if e == "delta") == "Hello there"
    assert events[0][1]["stage"] == "planning"                   # other requests' status filtered out
    assert events[-1][1]["message"] == "Hello there" and events[-1][1]["status"] == "completed"
    assert bus.subs == {}                                        # unsubscribed


async def test_sse_client_disconnect_cancels_the_run(monkeypatch):
    bus = FakeBus()
    ctx = SimpleNamespace(database=FakeDB(), orchestrator=SimpleNamespace(
        event_bus=bus, idempotency=IdempotencyRegistry()))
    state = {"cancelled": False}

    async def slow_run_chat(request, user_id, orchestrator, db, *, request_id, reply_sink=None):
        try:
            await reply_sink.on_delta("partial ")
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
    monkeypatch.setattr(chat_routes, "_run_chat", slow_run_chat)

    http = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    resp = await chat_routes.chat_stream(chat_routes.ChatRequest(device_id="d", message="hi"),
                                         http, user_id="u", context=ctx, idempotency_key=None)
    it = resp.body_iterator
    await it.__anext__()                      # retry:
    first = await it.__anext__()              # the delta
    assert "partial" in first
    await it.aclose()                         # client disconnects
    await asyncio.sleep(0.05)
    assert state["cancelled"]


async def test_sse_idempotency_key_runs_once_and_replays_done(monkeypatch):
    from pai.orchestration.idempotency import IdempotencyRegistry
    bus = FakeBus()
    ctx = SimpleNamespace(database=FakeDB(), orchestrator=SimpleNamespace(
        event_bus=bus, idempotency=IdempotencyRegistry()))
    runs = {"n": 0}

    async def run_chat(request, user_id, orchestrator, db, *, request_id, reply_sink=None):
        runs["n"] += 1
        await reply_sink.on_delta("once")
        return chat_routes.ChatResponse(message="once", status="completed", session_id="s1", tasks=[])
    monkeypatch.setattr(chat_routes, "_run_chat", run_chat)
    http = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    body = chat_routes.ChatRequest(device_id="d", message="hi", session_id="s1")

    first = parse(await collect(await chat_routes.chat_stream(body, http, user_id="u", context=ctx, idempotency_key="k-1")))
    second = parse(await collect(await chat_routes.chat_stream(body, http, user_id="u", context=ctx, idempotency_key="k-1")))
    assert runs["n"] == 1
    assert [e for e, _ in first] == ["delta", "done"]
    assert [e for e, _ in second] == ["done"] and second[0][1]["message"] == "once"   # replay: final only
