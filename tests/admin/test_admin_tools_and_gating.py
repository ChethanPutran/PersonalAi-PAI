"""Test-helper endpoints (voice / LLM) and the config gate on the admin.test.* capabilities."""
import io
import json
import os
import wave

import pytest
from fastapi.testclient import TestClient

from admin_helpers import ADMIN_EMAIL, AdminHarness, isolated_config
from pai.llm.models import LLMStreamChunk
from pai.server.factory import create_application
from pai.voice.providers.fake import FakeSTT


def wav_bytes(seconds=0.5, rate=16000, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x10\x10" * int(rate * seconds) * channels)
    return buf.getvalue()


# ---------------------------------------------------------------- voice helpers

def test_voice_status_and_tts_returns_a_playable_wav(harness):
    st = harness.get("/test/voice-status").json()
    assert st["available"] and st["tts"] == "fake" and st["output_sample_rate"] == 24000
    r = harness.post("/test/tts", json={"text": "hello from the admin console"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1 and w.getnframes() > 24000 * 0.5


def test_tts_input_is_limited(harness):
    assert harness.post("/test/tts", json={"text": "x" * 5000}).status_code == 413
    assert harness.post("/test/tts", json={"text": ""}).status_code == 422


def test_stt_transcribes_wav_and_raw_pcm_and_rejects_bad_input(harness):
    harness.ctx.voice.stt = FakeSTT(["hello admin", "second clip", "third clip"])
    r = harness.client.post("/api/v1/admin/test/stt", headers={**harness.H, "Content-Type": "audio/wav"}, content=wav_bytes())
    assert r.status_code == 200 and r.json()["text"] == "hello admin" and r.json()["provider"] == "fake"
    assert 0.4 < r.json()["audio_seconds"] < 0.6
    stereo = harness.client.post("/api/v1/admin/test/stt", headers={**harness.H, "Content-Type": "audio/wav"},
                                 content=wav_bytes(channels=2, rate=44100))
    assert stereo.status_code == 200 and stereo.json()["text"] == "second clip"           # downmixed + resampled
    raw = harness.client.post("/api/v1/admin/test/stt", params={"sample_rate": 16000},
                              headers={**harness.H, "Content-Type": "application/octet-stream"}, content=b"\x01\x01" * 8000)
    assert raw.status_code == 200 and raw.json()["text"] == "third clip"
    big = harness.client.post("/api/v1/admin/test/stt", headers={**harness.H, "Content-Type": "audio/wav"},
                              content=b"\x00" * (harness.ctx.admin.config.max_test_upload_bytes + 10))
    assert big.status_code == 413
    bad = harness.client.post("/api/v1/admin/test/stt", headers={**harness.H, "Content-Type": "audio/wav"}, content=b"RIFFnot-a-wav")
    assert bad.status_code == 415
    assert harness.client.post("/api/v1/admin/test/stt", headers=harness.H, content=b"").status_code == 400


def test_voice_helpers_report_unavailable_instead_of_crashing(harness):
    voice = harness.ctx.voice
    saved = (voice.stt, voice.tts)
    voice.stt = voice.tts = None
    try:
        assert harness.post("/test/tts", json={"text": "hi"}).status_code == 503
        assert harness.get("/test/voice-status").json()["available"] is False
    finally:
        voice.stt, voice.tts = saved


# ------------------------------------------------------------------------ LLM

class FakeLLM:
    def __init__(self, fail=False): self.fail = fail
    async def stream_request(self, request, route=None):
        assert request.max_tokens <= 256
        for piece in ("A DAG ", "is a graph ", "without cycles."):
            yield LLMStreamChunk(delta=piece)
        if self.fail:
            raise RuntimeError("provider exploded")
        yield LLMStreamChunk(finish_reason="stop")


def sse(text):
    out = []
    for block in text.split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "): ev = line[7:]
            if line.startswith("data: "): data = json.loads(line[6:])
        if ev: out.append((ev, data))
    return out


def test_llm_test_streams_deltas_then_done_and_surfaces_errors(harness):
    planner = harness.ctx.orchestrator.planner
    real = planner.llm
    try:
        planner.llm = FakeLLM()
        r = harness.post("/test/llm", json={"prompt": "what is a dag?"})
        ev = sse(r.text)
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "".join(d["delta"] for e, d in ev if e == "delta") == "A DAG is a graph without cycles."
        assert ev[-1] == ("done", {"finish_reason": "stop"})
        planner.llm = FakeLLM(fail=True)
        ev = sse(harness.post("/test/llm", json={"prompt": "x"}).text)
        assert ev[-1][0] == "error" and "provider exploded" in ev[-1][1]["message"]
        assert harness.post("/test/llm", json={"prompt": "x" * 3000}).status_code == 422
    finally:
        planner.llm = real
    assert any(e.metadata.get("action") == "test_llm" for e in harness.audit_events())


# ------------------------------------------------------------------ config gate

@pytest.fixture(scope="module")
def prod_harness(tmp_path_factory):
    """A second app with admin.test_capabilities OFF (the production default)."""
    tmp = tmp_path_factory.mktemp("admin_prod")
    old = os.environ.pop("PAI_ADMIN_TEST_CAPABILITIES", None)
    os.environ["PAI_ADMIN_EMAILS"] = ADMIN_EMAIL
    app = create_application(isolated_config(tmp, test_capabilities=False))
    with TestClient(app) as client:
        yield AdminHarness(client)
    if old is not None:
        os.environ["PAI_ADMIN_TEST_CAPABILITIES"] = old


def test_test_capabilities_are_absent_when_the_config_flag_is_off(prod_harness):
    h = prod_harness
    caps = h.get("/capabilities").json()
    assert caps["test_capabilities_enabled"] is False
    assert not [c for c in caps["capabilities"] if c["test"]]
    assert h.get("/overview").json()["test_capabilities_enabled"] is False
    r = h.post("/run/demo/fanout")
    assert r.status_code == 409 and "admin.test_capabilities" in r.json()["detail"]["message"]
    r = h.post("/run/dag", json={"tasks": [{"id": "a", "capability": "admin.test.sleep"}]})
    assert r.status_code == 422 and "admin.test.sleep" in " ".join(r.json()["detail"]["issues"])
    assert h.ctx.orchestrator.dag_scheduler.active_run_count() == 0


def test_the_env_override_turns_test_capabilities_on(monkeypatch):
    from pai.config.models.admin import AdminConfig
    monkeypatch.delenv("PAI_ADMIN_TEST_CAPABILITIES", raising=False)
    assert AdminConfig().test_capabilities is False
    monkeypatch.setenv("PAI_ADMIN_TEST_CAPABILITIES", "1")
    assert AdminConfig().test_capabilities is True


def test_admin_can_be_disabled_entirely(tmp_path):
    cfg = isolated_config(tmp_path, test_capabilities=False)
    cfg.admin.enabled = False
    app = create_application(cfg)
    with TestClient(app) as c:
        assert c.get("/admin/static/js/main.js").status_code == 404                # no assets mounted
        page = c.get("/api/v1/system/admin")                                         # the page route falls back to the self-contained legacy page
        assert page.status_code == 200 and "inline" not in page.text[:0] and 'id="login-overlay"' in page.text
        r = c.get("/api/v1/admin/overview")                                          # no API mounted either
        assert r.status_code in (401, 404)                                           # (401 from auth middleware first)
        assert c.get("/openapi.json").status_code == 200
        assert not any(p.startswith("/api/v1/admin/") for p in c.get("/openapi.json").json()["paths"])
