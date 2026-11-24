"""The real /voice/stream WebSocket route over a real (Starlette) websocket."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pai.api.routes import voice as voice_routes
from pai.config.models.voice import VADConfig, VoiceConfig, VoiceLimits
from pai.security.tokens.auth import create_access_token
from pai.voice.audio import sine
from pai.voice.providers.fake import FakeSTT, FakeTTS
from pai.voice.service import VoiceService
from pai.voice.session import TurnOutcome

RATE = 16000


class Runner:
    def __init__(self): self.sessions, self.calls = [], []
    async def ensure_chat_session(self, user_id, session_id):
        if session_id == "not-yours":
            raise PermissionError("nope")
        self.sessions.append((user_id, session_id))
    async def run_turn(self, *, user_id, session_id, device_id, text, turn_id, sink, on_status):
        self.calls.append((user_id, session_id, text))
        await on_status("planning", "Understanding your request...")
        for piece in ("Hello there. ", "How can I help?"):
            await sink.on_delta(piece)
        return TurnOutcome(reply="Hello there. How can I help?")
    async def on_interrupted(self, **kw): ...


def make_app(**cfg_kw):
    cfg = VoiceConfig(
        vad=VADConfig(min_speech_ms=60, end_silence_ms=200, pre_roll_ms=60, energy_threshold=0.02),
        limits=VoiceLimits(max_sessions_per_user=1), **cfg_kw)
    runner = Runner()
    svc = VoiceService(cfg, stt=FakeSTT(["turn the lights on"]), tts=FakeTTS(ms_per_char=10, chunk_ms=40),
                       runner=runner)
    app = FastAPI()
    app.include_router(voice_routes.router)
    app.state.context = SimpleNamespace(voice=svc)
    return app, svc, runner


def url(token=None): return "/voice/stream" + (f"?token={token}" if token else "")
def start_msg(**kw): return json.dumps({"type": "session.start", "client": {"name": "test"}, **kw})


def recv_until(ws, predicate, limit=200):
    """Collect frames until predicate(event) is true. Returns (events, audio_bytes)."""
    events, audio = [], 0
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            audio += len(msg["bytes"])
            continue
        if msg.get("text") is not None:
            ev = json.loads(msg["text"])
            events.append(ev)
            if predicate(ev):
                return events, audio
        if msg["type"] == "websocket.close":
            break
    raise AssertionError(f"predicate never satisfied; got {[e['type'] for e in events]}")


def test_rejects_missing_or_bad_token():
    app, *_ = make_app()
    with TestClient(app) as c:
        for u in (url(), url("garbage")):
            with pytest.raises(Exception):
                with c.websocket_connect(u) as ws:
                    ws.receive()


def test_full_voice_conversation_over_the_wire():
    app, svc, runner = make_app()
    token = create_access_token("alice")
    with TestClient(app) as c, c.websocket_connect(url(token)) as ws:
        ws.send_text(start_msg(session_id="chat-1", device_id="phone"))
        ready, _ = recv_until(ws, lambda e: e["type"] == "session.ready")
        r = ready[-1]
        assert r["chat_session_id"] == "chat-1" and r["output_sample_rate"] == 24000
        assert r["capabilities"]["tts"] == "fake" and r["capabilities"]["barge_in"] is True

        # stream microphone audio as binary frames: 0.5s of speech then silence
        speech = sine(300.0, 500, RATE, amplitude=0.5)
        for i in range(0, len(speech), 640):
            ws.send_bytes(speech[i:i + 640])
        for _ in range(20):
            ws.send_bytes(b"\x00" * 640)

        events, audio = recv_until(ws, lambda e: e["type"] == "assistant.text.done")
        types = [e["type"] for e in events]
        for needed in ("speech.started", "speech.stopped", "transcript.final", "assistant.status",
                       "assistant.text.delta", "assistant.audio.start"):
            assert needed in types, (needed, types)
        assert next(e for e in events if e["type"] == "transcript.final")["text"] == "turn the lights on"
        assert types.index("assistant.audio.start") < types.index("assistant.text.delta") + 6
        done = events[-1]
        assert done["text"] == "Hello there. How can I help?"
        ws.send_text(json.dumps({"type": "ping", "id": "p1"}))
        pong, _ = recv_until(ws, lambda e: e["type"] == "pong")
        assert pong[-1]["id"] == "p1"
        ws.send_text(json.dumps({"type": "session.end"}))
        ended, _ = recv_until(ws, lambda e: e["type"] == "session.ended")
        assert ended[-1]["reason"] == "client_ended"
    assert runner.calls == [("alice", "chat-1", "turn the lights on")]
    assert svc._active == {}                       # slot released


def test_binary_audio_is_returned_at_the_announced_rate_and_sample_aligned():
    app, *_ = make_app()
    with TestClient(app) as c, c.websocket_connect(url(create_access_token("bob"))) as ws:
        ws.send_text(start_msg())
        recv_until(ws, lambda e: e["type"] == "session.ready")
        ws.send_text(json.dumps({"type": "input.text", "text": "say something"}))
        events, audio = recv_until(ws, lambda e: e["type"] == "assistant.audio.end")
        assert audio > 0 and audio % 2 == 0
        assert next(e for e in events if e["type"] == "assistant.audio.start")["sample_rate"] == 24000


def test_second_concurrent_session_for_a_user_is_refused_and_slot_is_reusable():
    app, svc, _ = make_app()
    token = create_access_token("carol")
    with TestClient(app) as c:
        with c.websocket_connect(url(token)) as first:
            first.send_text(start_msg())
            recv_until(first, lambda e: e["type"] == "session.ready")
            with c.websocket_connect(url(token)) as second:
                err = json.loads(second.receive_text())
                assert err["type"] == "error" and err["code"] == "too_many_sessions" and err["fatal"]
        time.sleep(0.2)
        with c.websocket_connect(url(token)) as again:       # first one closed -> slot free
            again.send_text(start_msg())
            recv_until(again, lambda e: e["type"] == "session.ready")


def test_protocol_errors_are_reported_without_killing_the_session():
    app, *_ = make_app()
    with TestClient(app) as c, c.websocket_connect(url(create_access_token("dan"))) as ws:
        ws.send_text(start_msg())
        recv_until(ws, lambda e: e["type"] == "session.ready")
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "bogus"}))
        ws.send_text(start_msg())                              # second start
        errs, _ = recv_until(ws, lambda e: e["type"] == "error" and e["code"] == "already_started")
        assert [e["code"] for e in errs if e["type"] == "error"] == ["bad_message", "bad_message", "already_started"]
        ws.send_text(json.dumps({"type": "ping"}))
        recv_until(ws, lambda e: e["type"] == "pong")          # still alive


def test_first_frame_must_be_session_start_and_foreign_sessions_are_forbidden():
    app, *_ = make_app()
    token = create_access_token("erin")
    with TestClient(app) as c:
        with c.websocket_connect(url(token)) as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            assert json.loads(ws.receive_text())["code"] == "bad_start"
        with c.websocket_connect(url(token)) as ws:
            ws.send_text(start_msg(session_id="not-yours"))
            assert json.loads(ws.receive_text())["code"] == "forbidden"


def test_voice_unavailable_is_reported_cleanly():
    app, *_ = make_app()
    app.state.context = SimpleNamespace(voice=VoiceService(
        VoiceConfig(), stt=None, tts=None, runner=None, unavailable_reason="no API key"))
    with TestClient(app) as c, c.websocket_connect(url(create_access_token("fay"))) as ws:
        err = json.loads(ws.receive_text())
        assert err["code"] == "voice_unavailable" and "no API key" in err["message"] and err["fatal"]


def test_service_build_never_raises_and_falls_back_to_free_engines_without_a_key(monkeypatch, tmp_path):
    from pai.config.models.voice import STTProviderName, TTSProviderName
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Explicitly OpenAI but no key: voice is reported unavailable (and the reason says how to fix it).
    svc = VoiceService.build(VoiceConfig(stt=STTProviderName.OPENAI_REALTIME), runner=object())
    assert not svc.available and "API key" in svc.unavailable_reason and "vosk" in svc.unavailable_reason
    # Default "auto" with no key: the free local engines take over (when installed + models present).
    svc = VoiceService.build(
        VoiceConfig(stt=STTProviderName.FAKE, tts=TTSProviderName.FAKE), runner=object())
    assert svc.available
    # Nothing usable at all (missing models, downloads off): still no exception.
    svc = VoiceService.build(VoiceConfig(models_dir=str(tmp_path), auto_download_models=False), runner=object())
    assert not svc.available and svc.unavailable_reason


# ------------------------- OrchestratorTurnRunner ----------------------------
import asyncio

from pai.orchestration.streaming import QueueReplySink
from pai.voice import runner as runner_mod


class FakeRepoStore:
    messages = []


class FakeChatRepo:
    def __init__(self, db): ...
    async def get_or_create_session(self, user_id, session_id, *, title=None):
        if session_id == "foreign":
            raise PermissionError("no")
    async def append_message(self, session_id, role, content, **kw):
        FakeRepoStore.messages.append((session_id, role, content, kw.get("status")))


class FakeDB:
    def session(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): ...
    async def commit(self): ...


class FakeBus:
    def __init__(self): self.handlers = []
    async def subscribe(self, p, h): self.handlers.append(h); return len(self.handlers) - 1
    async def unsubscribe(self, i): ...


class FakeOrchestrator:
    def __init__(self, behaviour="ok"):
        self.event_bus, self.behaviour, self.seen = FakeBus(), behaviour, {}
    async def run(self, *, user_id, goal, session_id, source_device_id, request_id, reply_sink, context):
        self.seen = dict(goal=goal, request_id=request_id, context=context)
        for h in self.event_bus.handlers:
            await h(SimpleNamespace(data={"request_id": request_id, "stage": "executing", "message": "Running…"}))
        if self.behaviour == "hang":
            await asyncio.sleep(30)
        await reply_sink.on_delta("It is sunny.")
        return {"status": "completed", "results": [{"capability": "chat.reply", "reply": "It is sunny."}]}


async def test_orchestrator_runner_passes_voice_mode_streams_status_and_persists(monkeypatch):
    monkeypatch.setattr(runner_mod, "ChatRepository", FakeChatRepo)
    FakeRepoStore.messages.clear()
    orch = FakeOrchestrator()
    r = runner_mod.OrchestratorTurnRunner(orch, FakeDB())
    statuses, sink = [], QueueReplySink()

    async def on_status(stage, message): statuses.append((stage, message))
    out = await r.run_turn(user_id="u", session_id="s", device_id="d", text="weather?",
                           turn_id="t1", sink=sink, on_status=on_status)
    assert out.reply == "It is sunny." and out.status == "completed"
    assert orch.seen["context"]["mode"] == "voice" and orch.seen["request_id"] == "t1"
    assert statuses == [("executing", "Running…")]
    assert FakeRepoStore.messages == [("s", "user", "weather?", None), ("s", "assistant", "It is sunny.", "completed")]


async def test_runner_times_out_gracefully_and_interrupt_keeps_only_spoken_text(monkeypatch):
    monkeypatch.setattr(runner_mod, "ChatRepository", FakeChatRepo)
    FakeRepoStore.messages.clear()
    r = runner_mod.OrchestratorTurnRunner(FakeOrchestrator("hang"), FakeDB(), timeout=0.05)
    out = await r.run_turn(user_id="u", session_id="s", device_id=None, text="x", turn_id="t",
                           sink=QueueReplySink(), on_status=lambda *a: asyncio.sleep(0))
    assert out.status == "failed" and "taking too long" in out.reply
    await r.on_interrupted(user_id="u", session_id="s", turn_id="t", spoken_text="Well, the first part")
    assert FakeRepoStore.messages[-1] == ("s", "assistant", "Well, the first part", "interrupted")
    with pytest.raises(PermissionError):
        await r.ensure_chat_session("u", "foreign")


def test_real_speech_audio_flows_over_the_wire_with_espeak():
    from pai.voice.audio import pcm_to_float, rms
    from pai.voice.providers.espeak_tts import EspeakTTS
    if not EspeakTTS.available():
        pytest.skip("espeak not installed")
    app, svc, _ = make_app()
    svc.tts = EspeakTTS(sample_rate=24000)
    chunks = []
    with TestClient(app) as c, c.websocket_connect(url(create_access_token("gus"))) as ws:
        ws.send_text(start_msg())
        recv_until(ws, lambda e: e["type"] == "session.ready")
        ws.send_text(json.dumps({"type": "input.text", "text": "hello"}))
        for _ in range(400):
            m = ws.receive()
            if m.get("bytes") is not None:
                chunks.append(m["bytes"])
            elif m.get("text") and json.loads(m["text"])["type"] == "assistant.audio.end":
                break
    pcm = b"".join(chunks)
    seconds = len(pcm) / 2 / 24000
    assert seconds > 1.0, seconds                      # "Hello there. How can I help?" spoken for real
    assert rms(pcm) > 0.01                             # actual speech energy, not silence
    assert len(chunks) > 5                             # arrived as a stream of chunks, not one blob
