"""The FREE engines (Vosk STT, Piper TTS), for real — no mocks, no network, no API key."""
import asyncio
import time
import zipfile
from pathlib import Path

import pytest

from pai.config.models.voice import STTProviderName, TTSProviderName, VoiceConfig
from pai.voice.audio import resample
from pai.voice.providers import factory
from pai.voice.providers.errors import VoiceConfigurationError
from pai.voice.providers.local_models import ensure_piper_voice, ensure_vosk_model

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "data" / "voice_models"
VOSK = "vosk-model-small-en-us-0.15"
PIPER = "en_US-lessac-medium"
have_vosk = (MODELS / VOSK).exists()
have_piper = (MODELS / f"{PIPER}.onnx").exists()
pytestmark = pytest.mark.skipif(not (have_vosk and have_piper), reason="local voice models not downloaded")


def local_cfg(**kw) -> VoiceConfig:
    return VoiceConfig(stt=STTProviderName.VOSK, tts=TTSProviderName.PIPER, models_dir=str(MODELS),
                       auto_download_models=False, **kw)


async def synth_16k(tts, text: str) -> bytes:
    pcm = b"".join([c async for c in tts.synthesize(text)])
    return resample(pcm, tts.sample_rate, 16000)


async def test_piper_to_vosk_roundtrip_streams_partials_then_final():
    cfg = local_cfg()
    tts, stt = factory.create_tts(cfg), factory.create_stt(cfg)
    audio = await synth_16k(tts, "Hello world. Please turn on the kitchen lights.")
    stream = await stt.open(sample_rate=16000, language=None)
    partials = []

    async def collect():
        async for ev in stream.partials():
            partials.append(ev.text)
    collector = asyncio.create_task(collect())
    for i in range(0, len(audio), 3200):                      # 100 ms frames, as a live mic would
        await stream.push(audio[i:i + 3200])
    text = await stream.finish()
    await asyncio.wait_for(collector, 2)
    assert "hello world" in text and "kitchen lights" in text, text
    assert len(partials) >= 3 and "hello" in partials[0]       # partials arrived while audio was still coming
    assert partials == sorted(partials, key=len) or len(set(partials)) > 1
    await stt.aclose(); await tts.aclose()


async def test_piper_streams_in_chunks_and_first_audio_comes_early():
    tts = factory.create_tts(local_cfg())
    t0 = time.perf_counter()
    first_at, chunks = None, 0
    async for c in tts.synthesize("This is the first sentence. And here is a second, longer sentence to speak."):
        chunks += 1
        first_at = first_at or time.perf_counter() - t0
        assert len(c) % 2 == 0
    total = time.perf_counter() - t0
    assert chunks > 10 and first_at < total          # a stream of small chunks, not one blob
    await tts.aclose()


async def test_closing_piper_early_stops_synthesis_promptly():
    tts = factory.create_tts(local_cfg())
    gen = tts.synthesize("Sentence one is here. " * 40)
    await gen.__anext__()
    t0 = time.perf_counter()
    await gen.aclose()                                # barge-in
    assert time.perf_counter() - t0 < 1.0
    await tts.aclose()


# ------------------------------- model management ---------------------------------

def test_model_names_are_validated_not_trusted_as_paths(tmp_path):
    for bad in ("../etc/passwd", "a/b", "..", "x"):
        with pytest.raises(VoiceConfigurationError):
            ensure_vosk_model(str(tmp_path), bad, download=False)
    with pytest.raises(VoiceConfigurationError, match="not found"):
        ensure_vosk_model(str(tmp_path), "vosk-model-small-xx-0.1", download=False)
    with pytest.raises(VoiceConfigurationError, match="not found"):
        ensure_piper_voice(str(tmp_path), "en_US-nobody-medium", download=False)


def test_download_is_restricted_to_known_hosts_and_zip_slip_is_blocked(tmp_path, monkeypatch):
    from pai.voice.providers import local_models
    with pytest.raises(VoiceConfigurationError, match="refusing"):
        local_models._download("https://evil.example/x.zip", tmp_path / "x.zip")
    with pytest.raises(VoiceConfigurationError, match="refusing"):
        local_models._download("http://alphacephei.com/x.zip", tmp_path / "x.zip")

    def evil_download(url, dest):                      # an archive that tries to escape the models dir
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("../escaped.txt", "pwned")
    monkeypatch.setattr(local_models, "_download", evil_download)
    with pytest.raises(VoiceConfigurationError, match="unsafe"):
        ensure_vosk_model(str(tmp_path / "models"), "vosk-model-evil-0.1", download=True)
    assert not (tmp_path / "escaped.txt").exists()


# ----------------------------- provider selection (config) --------------------------

def test_auto_uses_the_free_local_engines_without_an_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = VoiceConfig(models_dir=str(MODELS), auto_download_models=False)          # defaults = auto
    assert (cfg.stt, cfg.tts) == (STTProviderName.AUTO, TTSProviderName.AUTO)
    stt, tts = factory.create_stt(cfg), factory.create_tts(cfg)
    assert (stt.name, tts.name) == ("vosk", "piper") and stt.supports_partials


def test_auto_prefers_openai_when_a_key_is_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = VoiceConfig(models_dir=str(MODELS), auto_download_models=False)
    assert factory.create_stt(cfg).name.startswith("openai")
    assert factory.create_tts(cfg).name.startswith("openai")


def test_explicit_choice_is_honoured_and_openai_without_key_explains_the_free_option(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(VoiceConfigurationError, match="vosk"):
        factory.create_stt(VoiceConfig(stt=STTProviderName.OPENAI_REALTIME))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")                                # key present, still local
    assert factory.create_stt(local_cfg()).name == "vosk"
    assert factory.create_tts(VoiceConfig(tts=TTSProviderName.ESPEAK)).name == "espeak"


def test_any_other_engine_can_be_plugged_in_by_name():
    class MyTTS:
        name, sample_rate = "mine", 24000
        async def synthesize(self, text, *, voice=None, language=None):
            yield b"\x00\x00"
        async def aclose(self): ...
    factory.register_tts("mine", lambda cfg: MyTTS())
    cfg = VoiceConfig.model_construct(tts="mine", stt="fake")     # a name the enums do not list
    assert factory.create_tts(cfg).name == "mine"
    with pytest.raises(VoiceConfigurationError, match="unknown TTS"):
        factory.create_tts(VoiceConfig.model_construct(tts="nope"))
