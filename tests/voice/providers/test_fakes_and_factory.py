import asyncio

import pytest

from pai.config.models.voice import STTProviderName, TTSProviderName, VoiceConfig
from pai.voice.audio import pcm_duration_ms, sine
from pai.voice.providers import create_stt, create_tts
from pai.voice.providers.errors import VoiceConfigurationError
from pai.voice.providers.espeak_tts import EspeakTTS
from pai.voice.providers.fake import FakeSTT, FakeTTS
from pai.voice.providers.openai_stt import OpenAIBatchSTT, OpenAIRealtimeSTT
from pai.voice.providers.openai_tts import OpenAITTS


async def test_fake_stt_script_partials_and_recording():
    stt = FakeSTT(["turn on the kitchen light", "thanks"], partial_every_bytes=640)
    s1 = await stt.open(sample_rate=16000, language=None)
    for _ in range(3):
        await s1.push(sine(300, 20, 16000))                     # 640 bytes each
    final = await s1.finish()
    partials = [e.text async for e in s1.partials()]
    assert final == "turn on the kitchen light"
    assert partials == ["turn", "turn on", "turn on the"] and len(s1.audio) == 1920
    s2 = await stt.open(sample_rate=16000, language=None)
    await s2.push(b"\x00\x00")
    assert await s2.finish() == "thanks"
    s3 = await stt.open(sample_rate=16000, language=None)       # script exhausted
    await s3.push(b"\x00\x00")
    assert await s3.finish() == ""
    assert len(stt.streams) == 3


async def test_fake_stt_without_audio_and_after_close():
    stt = FakeSTT(["x"])
    s = await stt.open(sample_rate=16000, language=None)
    assert await s.finish() == ""
    s = await stt.open(sample_rate=16000, language=None)
    await s.aclose()
    await s.push(b"\x00\x00")
    assert s.pushed == [] and await s.finish() == ""


async def test_fake_tts_duration_chunks_and_records():
    tts = FakeTTS(sample_rate=16000, ms_per_char=10, chunk_ms=40)
    chunks = [c async for c in tts.synthesize("hello world")]       # 11 chars -> 110 ms
    assert abs(sum(pcm_duration_ms(len(c), 16000) for c in chunks) - 110) < 1
    assert tts.calls == ["hello world"] and tts.completed == ["hello world"] and tts.cancelled == []


async def test_fake_tts_records_cancellation():
    tts = FakeTTS(chunk_delay=0.01, ms_per_char=100)
    gen = tts.synthesize("a very long sentence indeed")
    await gen.__anext__()
    await gen.aclose()
    assert tts.cancelled == ["a very long sentence indeed"] and tts.completed == []

    tts2 = FakeTTS(chunk_delay=0.05, ms_per_char=100)

    async def consume():
        async for _ in tts2.synthesize("another long sentence here"):
            pass

    t = asyncio.create_task(consume())
    await asyncio.sleep(0.12)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert tts2.cancelled == ["another long sentence here"]


def cfg(**kw):
    return VoiceConfig(**kw)


def test_factory_requires_key_for_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(VoiceConfigurationError, match="API key"):
        create_stt(cfg(stt=STTProviderName.OPENAI_REALTIME))
    with pytest.raises(VoiceConfigurationError, match="API key"):
        create_stt(cfg(stt=STTProviderName.OPENAI_BATCH))
    with pytest.raises(VoiceConfigurationError, match="API key"):
        create_tts(cfg(tts=TTSProviderName.OPENAI))


def test_factory_key_from_config_or_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(create_stt(cfg(api_key="k", stt=STTProviderName.OPENAI_REALTIME)), OpenAIRealtimeSTT)
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert isinstance(create_stt(cfg(stt=STTProviderName.OPENAI_BATCH)), OpenAIBatchSTT)
    tts = create_tts(cfg(tts=TTSProviderName.OPENAI, output_sample_rate=16000, voice="nova"))
    assert isinstance(tts, OpenAITTS) and tts.sample_rate == 16000 and tts._default_voice == "nova"
    assert tts._api_key == "from-env"
    assert create_tts(cfg(api_key="cfg-wins", tts=TTSProviderName.OPENAI))._api_key == "cfg-wins"


def test_factory_keyless_providers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(create_stt(cfg(stt=STTProviderName.FAKE)), FakeSTT)
    fake = create_tts(cfg(tts=TTSProviderName.FAKE, output_sample_rate=8000))
    assert isinstance(fake, FakeTTS) and fake.sample_rate == 8000
    if EspeakTTS.available():
        assert isinstance(create_tts(cfg(tts=TTSProviderName.ESPEAK)), EspeakTTS)


def test_factory_espeak_missing_binary(monkeypatch):
    monkeypatch.setattr(EspeakTTS, "available", staticmethod(lambda binary="espeak": False))
    with pytest.raises(VoiceConfigurationError, match="espeak"):
        create_tts(cfg(tts=TTSProviderName.ESPEAK))
