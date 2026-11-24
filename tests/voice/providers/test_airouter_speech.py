"""AIRouter speech: STT (/audio/transcriptions) and TTS (/audio/speech, WAV). Everything (endpoint, models,
voice) comes from config. Hermetic (httpx MockTransport)."""
import struct
from pathlib import Path

import httpx
import pytest
import yaml

from pai.config.models.voice import STTProviderName, TTSProviderName, VoiceConfig
from pai.voice.audio import sine
from pai.voice.providers import factory
from pai.voice.providers.airouter import AIRouterSTT, AIRouterTTS, wav_to_pcm
from pai.voice.providers.errors import VoiceConfigurationError, VoiceProviderError

REPO = Path(__file__).resolve().parents[3]


def wav(pcm: bytes, rate=24000, channels=1, data_size=None, bits=16) -> bytes:
    size = len(pcm) if data_size is None else data_size
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", size) + pcm)


def cfg(**air) -> VoiceConfig:
    base = dict(api_key="sk-air-v1-t", api_base="https://api.test/v1", stt_model="m/stt", tts_model="m/tts",
                voice="alloy")
    base.update(air)
    return VoiceConfig(stt=STTProviderName.AIROUTER, tts=TTSProviderName.AIROUTER, airouter=base)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── config is the source of truth ───────────────────────────────────────────

def test_shipped_config_selects_airouter_with_the_requested_models():
    raw = yaml.safe_load((REPO / "config" / "config.yaml").read_text())["voice"]
    assert raw["stt"] == "airouter" and raw["tts"] == "airouter"
    assert raw["airouter"]["stt_model"] == "microsoft/mai-transcribe-2"
    assert raw["airouter"]["tts_model"] == "openai/tts-1"
    VoiceConfig(**raw)                                        # the section validates


async def test_factory_uses_only_configured_values():
    c = cfg(stt_model="vendor/custom-stt", tts_model="vendor/custom-tts", voice="nova", tts_speed=1.2)
    seen = []
    stt = factory.create_stt(c, client=client(lambda r: seen.append(r) or httpx.Response(200, json={"text": "hi"})))
    stream = await stt.open(sample_rate=16000, language="en")
    await stream.push(sine(300, 100, 16000))
    assert await stream.finish() == "hi"
    assert str(seen[0].url) == "https://api.test/v1/audio/transcriptions"
    assert b"vendor/custom-stt" in seen[0].content and seen[0].headers["authorization"] == "Bearer sk-air-v1-t"

    tseen = []
    tts = factory.create_tts(c, client=client(lambda r: tseen.append(r) or httpx.Response(200, content=wav(b"\0\0" * 2400))))
    [x async for x in tts.synthesize("hello")]
    import json
    body = json.loads(tseen[0].content)
    assert str(tseen[0].url) == "https://api.test/v1/audio/speech"
    assert body == {"model": "vendor/custom-tts", "input": "hello", "response_format": "wav", "voice": "nova", "speed": 1.2}


def test_missing_models_or_key_are_clear_configuration_errors(monkeypatch):
    monkeypatch.delenv("AIROUTER_API_KEY", raising=False)
    with pytest.raises(VoiceConfigurationError, match="stt_model"):
        factory.create_stt(cfg(stt_model=None))
    with pytest.raises(VoiceConfigurationError, match="tts_model"):
        factory.create_tts(cfg(tts_model=None))
    with pytest.raises(VoiceConfigurationError, match="key"):
        factory.create_stt(cfg(api_key=None))
    monkeypatch.setenv("AIROUTER_API_KEY", "sk-air-v1-env")
    assert isinstance(factory.create_stt(cfg(api_key=None)), AIRouterSTT)


def test_the_llm_key_is_reused_when_airouter_is_the_llm_provider(monkeypatch):
    from pai.config.loader import load_config
    monkeypatch.setenv("LLM_PROVIDER__API_KEY", "sk-air-v1-llm")
    load_config.cache_clear()                 # load_config is cached: another test may have loaded it without the key
    try:
        c = load_config()
        assert c.llm_provider.provider.value == "airouter" and c.voice.airouter.api_key == "sk-air-v1-llm"
    finally:
        load_config.cache_clear()


# ── STT ─────────────────────────────────────────────────────────────────────

async def test_stt_has_no_partials_and_http_errors_return_empty():
    stt = factory.create_stt(cfg(), client=client(lambda r: httpx.Response(402, json={"error": {"type": "billing_error"}})))
    assert stt.supports_partials is False and stt.name == "airouter"
    stream = await stt.open(sample_rate=16000, language=None)
    await stream.push(sine(300, 100, 16000))
    assert [e async for e in stream.partials()] == []
    assert await stream.finish() == ""


# ── TTS ─────────────────────────────────────────────────────────────────────

async def test_tts_decodes_wav_resamples_and_chunks_to_60ms():
    pcm = sine(440, 1000, 16000)                                   # 1 s @ 16 kHz
    tts = factory.create_tts(cfg(), client=client(lambda r: httpx.Response(200, content=wav(pcm, rate=16000))))
    chunks = [c async for c in tts.synthesize("hello world")]
    assert tts.sample_rate == 24000
    assert all(len(c) == 24000 * 60 // 1000 * 2 for c in chunks[:-1])
    assert abs(sum(map(len, chunks)) - 24000 * 2) <= 4              # 1 s @ 24 kHz, s16


async def test_tts_errors_are_voice_provider_errors_and_empty_text_makes_no_request():
    calls = []
    tts = factory.create_tts(cfg(), client=client(lambda r: calls.append(r) or httpx.Response(400, json={"error": {"message": "bad"}})))
    assert [c async for c in tts.synthesize("   ")] == [] and calls == []
    with pytest.raises(VoiceProviderError, match="HTTP 400"):
        [c async for c in tts.synthesize("hi")]
    bad = factory.create_tts(cfg(), client=client(lambda r: httpx.Response(200, content=b"ID3 not a wav")))
    with pytest.raises(VoiceProviderError, match="not a WAV"):
        [c async for c in bad.synthesize("hi")]


async def test_a_per_call_voice_overrides_the_configured_one_and_unset_voice_is_omitted():
    import json
    seen = []
    ok = lambda r: seen.append(json.loads(r.content)) or httpx.Response(200, content=wav(b"\0\0" * 100))
    tts = factory.create_tts(cfg(voice=None), client=client(ok))
    [c async for c in tts.synthesize("a")]
    [c async for c in tts.synthesize("b", voice="onyx")]
    assert "voice" not in seen[0] and seen[1]["voice"] == "onyx" and "speed" not in seen[0]


# ── WAV decoding ────────────────────────────────────────────────────────────

def test_wav_with_placeholder_sizes_is_read_to_the_end():
    pcm = b"\x01\x00" * 50
    for size in (0, 0xFFFFFFFF, 9999999):
        out, rate = wav_to_pcm(wav(pcm, data_size=size))
        assert out == pcm and rate == 24000


def test_stereo_is_averaged_to_mono_and_odd_trailing_bytes_dropped():
    out, _ = wav_to_pcm(wav(struct.pack("<hh", 1000, 3000) * 10 + b"\x01", channels=2))
    assert struct.unpack("<10h", out) == (2000,) * 10


def test_unsupported_wav_is_rejected():
    with pytest.raises(VoiceProviderError, match="unsupported"):
        wav_to_pcm(wav(b"\0" * 8, bits=8))
    with pytest.raises(VoiceProviderError):
        wav_to_pcm(b"RIFF\0\0\0\0WAVE")


# ── voice cost: STT per second of audio, TTS per character ──────────────────

from pai.llm.cost import CostTracker  # noqa: E402


def priced(**kw) -> VoiceConfig:
    return cfg(stt_cost_per_minute_usd=0.0017, tts_cost_per_1k_chars_usd=0.015, **kw)


async def transcribe(stt, seconds=1.0):
    stream = await stt.open(sample_rate=16000, language=None)
    await stream.push(sine(300, int(seconds * 1000), 16000))
    return await stream.finish()


def test_shipped_voice_prices_come_from_models_yaml():
    from pai.config.model_registry import load_registry
    raw = yaml.safe_load((REPO / "config" / "config.yaml").read_text())["voice"]["airouter"]
    reg = load_registry(REPO / "config" / "models.yaml")
    assert raw["stt_cost_per_minute_usd"] is None and raw["tts_cost_per_1k_chars_usd"] is None      # null = from the registry
    assert reg.stt_price(raw["stt_model"]) == 0.0017        # microsoft/mai-transcribe-2: $0.10 per hour of audio
    assert reg.tts_price(raw["tts_model"]) == 0.015         # openai/tts-1: $15 per 1M characters
    VoiceConfig(airouter=raw)


async def test_stt_cost_uses_the_seconds_airouter_billed(tmp_path):
    t = CostTracker(initial_credits_usd=1.0)
    stt = factory.create_stt(priced(), client=client(lambda r: httpx.Response(
        200, json={"text": "hello", "usage": {"seconds": 12}})), cost_tracker=t)
    assert await transcribe(stt, seconds=1.0) == "hello"
    s = t.summary()
    rec = s["recent"][-1]
    assert (rec["route"], rec["model"], rec["unit"], rec["units"]) == ("voice.stt", "m/stt", "seconds", 12.0)
    assert rec["cost_usd"] == pytest.approx(12 / 60 * 0.0017) and rec["total_tokens"] == 0
    assert s["by_route"]["voice.stt"]["calls"] == 1 and s["credits"]["remaining_usd"] == pytest.approx(1.0 - 0.00034)


async def test_stt_cost_falls_back_to_the_audio_length():
    t = CostTracker()
    stt = factory.create_stt(priced(), client=client(lambda r: httpx.Response(200, json={"text": "x"})), cost_tracker=t)
    await transcribe(stt, seconds=3.0)
    rec = t.summary()["recent"][-1]
    assert rec["units"] == pytest.approx(3.0) and rec["cost_usd"] == pytest.approx(3 / 60 * 0.0017)


async def test_no_configured_price_counts_the_call_but_not_a_cost():
    t = CostTracker()
    stt = factory.create_stt(cfg(), client=client(lambda r: httpx.Response(200, json={"text": "x"})), cost_tracker=t)
    await transcribe(stt)
    s = t.summary()
    assert s["recent"][-1]["cost_usd"] is None and s["total"]["unpriced_calls"] == 1 and s["total"]["cost_usd"] == 0


async def test_a_failed_transcription_costs_nothing_and_no_tracker_is_fine():
    t = CostTracker()
    stt = factory.create_stt(priced(), client=client(lambda r: httpx.Response(402, json={"error": {}})), cost_tracker=t)
    assert await transcribe(stt) == ""
    assert t.summary()["total"]["calls"] == 0
    ok = factory.create_stt(priced(), client=client(lambda r: httpx.Response(200, json={"text": "x"})))
    assert await transcribe(ok) == "x"


async def test_tts_cost_uses_the_characters_airouter_billed():
    t = CostTracker()
    body = wav(b"\0\0" * 2400)
    tts = factory.create_tts(priced(), client=client(lambda r: httpx.Response(
        200, content=body, headers={"x-airouter-characters": "65"})), cost_tracker=t)
    [c async for c in tts.synthesize("Namaste duniya")]                   # 14 chars sent, 65 billed (non-Latin text)
    rec = t.summary()["recent"][-1]
    assert (rec["route"], rec["model"], rec["unit"], rec["units"]) == ("voice.tts", "m/tts", "characters", 65.0)
    assert rec["cost_usd"] == pytest.approx(65 / 1000 * 0.015)


async def test_tts_cost_falls_back_to_the_text_length_and_ignores_failures_and_empty_text():
    t = CostTracker()
    tts = factory.create_tts(priced(), client=client(lambda r: httpx.Response(200, content=wav(b"\0\0" * 100))), cost_tracker=t)
    [c async for c in tts.synthesize("  hello  ")]
    assert t.summary()["recent"][-1]["units"] == 5.0
    [c async for c in tts.synthesize("   ")]                              # nothing sent, nothing billed
    bad = factory.create_tts(priced(), client=client(lambda r: httpx.Response(500, json={})), cost_tracker=t)
    with pytest.raises(VoiceProviderError):
        [c async for c in bad.synthesize("hi")]
    assert t.summary()["total"]["calls"] == 1


async def test_voice_usage_survives_a_restart_in_the_ledger(tmp_path):
    ledger = tmp_path / "u.jsonl"
    t = CostTracker(ledger_path=ledger)
    tts = factory.create_tts(priced(), client=client(lambda r: httpx.Response(200, content=wav(b"\0\0" * 100))), cost_tracker=t)
    [c async for c in tts.synthesize("hello world")]
    again = CostTracker(ledger_path=ledger).summary()
    assert again["recent"][-1]["unit"] == "characters" and again["recent"][-1]["units"] == 11.0
    assert again["by_route"]["voice.tts"]["cost_usd"] == pytest.approx(11 / 1000 * 0.015)


def test_the_voice_service_hands_the_tracker_to_the_providers():
    from pai.voice.service import VoiceService
    t = CostTracker()
    svc = VoiceService.build(priced(), runner=object(), cost_tracker=t)
    assert svc.stt._tracker is t and svc.tts._tracker is t
    assert svc.stt._per_minute == 0.0017 and svc.tts._per_1k == 0.015


# ── real-time voice cost summary (admin panel) ──────────────────────────────

def test_voice_summary_totals_units_cost_and_the_last_hour():
    t = CostTracker()
    now = 1_800_000_000.0
    t.record_metered(provider="airouter", model="m/stt", route="voice.stt", units=30, unit="seconds", cost_usd=0.00085, ts=now - 120)
    t.record_metered(provider="airouter", model="m/stt", route="voice.stt", units=60, unit="seconds", cost_usd=0.0017, ts=now - 7200)   # 2 h ago
    t.record_metered(provider="airouter", model="m/tts", route="voice.tts", units=200, unit="characters", cost_usd=0.003, ts=now - 60)
    t.record_metered(provider="airouter", model="m/tts", route="voice.tts", units=10, unit="characters", cost_usd=None, ts=now - 30)
    t.record(provider="airouter", model="llm", prompt_tokens=5, completion_tokens=5, total_tokens=10, cost_usd=9.0, cost_source="provider", route="chat", ts=now - 10)
    v = t.voice_summary(now=now)
    assert v["stt"] == {"calls": 2, "unit": "seconds", "units": 90.0, "cost_usd": 0.00255, "unpriced_calls": 0,
                        "last_hour_calls": 1, "last_hour_cost_usd": 0.00085}
    assert v["tts"]["units"] == 210.0 and v["tts"]["calls"] == 2 and v["tts"]["unpriced_calls"] == 1 and v["tts"]["cost_usd"] == 0.003
    assert v["total_cost_usd"] == pytest.approx(0.00555) and v["last_hour_cost_usd"] == pytest.approx(0.00385)   # LLM spend is not voice
    assert t.summary()["by_unit"]["voice.tts"] == {"unit": "characters", "units": 210.0, "calls": 2}


def test_voice_summary_with_no_voice_usage_is_all_zeros():
    v = CostTracker().voice_summary()
    assert v["stt"]["calls"] == 0 and v["tts"]["units"] == 0 and v["total_cost_usd"] == 0 and v["stt"]["unit"] is None
