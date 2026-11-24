"""Free engines (Vosk + Piper) driven through the real VoiceSession: no fakes except the LLM."""
import asyncio
from pathlib import Path

import pytest

from pai.config.models.voice import STTProviderName, TTSProviderName, VoiceConfig
from pai.voice.audio import resample
from pai.voice.providers import factory
from test_session import Harness, ScriptedRunner, cfg, quiet
from pai.voice.models import AssistantAudioStart, TranscriptFinal

MODELS = Path(__file__).resolve().parents[2] / "data" / "voice_models"
pytestmark = pytest.mark.skipif(
    not ((MODELS / "vosk-model-small-en-us-0.15").exists() and (MODELS / "en_US-lessac-medium.onnx").exists()),
    reason="local voice models not downloaded")


def local_cfg() -> VoiceConfig:
    return VoiceConfig(stt=STTProviderName.VOSK, tts=TTSProviderName.PIPER, models_dir=str(MODELS),
                       auto_download_models=False)


async def synth_16k(tts, text: str) -> bytes:
    pcm = b"".join([c async for c in tts.synthesize(text)])
    return resample(pcm, tts.sample_rate, 16000)


async def test_real_engines_inside_a_voice_session_hear_and_speak():
    """Spoken audio -> VAD -> Vosk -> turn -> Piper audio back, with no fakes except the LLM."""
    local = local_cfg()
    tts, stt = factory.create_tts(local), factory.create_stt(local)
    tts.sample_rate = 24000
    speech = await synth_16k(tts, "Turn on the kitchen lights please")
    runner = ScriptedRunner(chunks=("Okay, turning them on. ",))
    h = Harness(runner=runner, stt=stt, tts=tts, config=cfg())
    await h.session.start()
    await h.feed(quiet(200))
    await h.feed(speech, pace=0.0)
    await h.feed(quiet(400))
    for _ in range(100):                               # wait for the reply audio
        await asyncio.sleep(0.05)
        if h.of(AssistantAudioStart) and sum(map(len, h.audio)) > 20000:
            break
    final = h.of(TranscriptFinal)
    assert final and "kitchen lights" in final[0].text, [e for e in h.events if not isinstance(e, tuple)][:6]
    assert runner.calls and "kitchen lights" in runner.calls[0]
    assert sum(map(len, h.audio)) > 20000              # real synthesized speech came back
    await h.session.close()


