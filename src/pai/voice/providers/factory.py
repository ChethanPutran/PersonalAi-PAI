"""Build STT/TTS providers from VoiceConfig.

Providers are looked up in a registry keyed by the config value, so choosing one is a config
change (`voice.stt` / `voice.tts`) and adding another engine is one `register_*` call:

    from pai.voice.providers.factory import register_stt
    register_stt("my_engine", lambda cfg: MySTT(...))   # then: voice.stt: my_engine

"auto" picks OpenAI when an API key is available, otherwise the free local engines.
"""
from __future__ import annotations

import importlib.util
import os
from typing import Callable, Dict, Optional, Union

import httpx

from pai.config.models.voice import STTProviderName, TTSProviderName, VoiceConfig
from pai.voice.protocols import STTProvider, TTSProvider
from pai.voice.providers.airouter import AIRouterSTT, AIRouterTTS
from pai.voice.providers.errors import VoiceConfigurationError
from pai.voice.providers.espeak_tts import EspeakTTS
from pai.voice.providers.fake import FakeSTT, FakeTTS
from pai.voice.providers.openai_stt import OpenAIBatchSTT, OpenAIRealtimeSTT
from pai.voice.providers.openai_tts import OpenAITTS

STTFactory = Callable[[VoiceConfig], STTProvider]
TTSFactory = Callable[[VoiceConfig], TTSProvider]
_STT: Dict[str, STTFactory] = {}
_TTS: Dict[str, TTSFactory] = {}


def _key(name: Union[str, STTProviderName, TTSProviderName]) -> str:
    return (name.value if hasattr(name, "value") else str(name)).strip().lower()


def register_stt(name: str, factory: STTFactory) -> None:
    _STT[_key(name)] = factory


def register_tts(name: str, factory: TTSFactory) -> None:
    _TTS[_key(name)] = factory


def resolve_api_key(config: VoiceConfig) -> Optional[str]:
    return config.api_key or os.environ.get("OPENAI_API_KEY") or None


def _require_key(config: VoiceConfig, what: str) -> str:
    key = resolve_api_key(config)
    if not key:
        raise VoiceConfigurationError(
            f"{what} needs an OpenAI API key: set voice.api_key in the config or the "
            "OPENAI_API_KEY environment variable — or choose a free engine "
            "(voice.stt: vosk, voice.tts: piper | espeak).")
    return key


def _airouter_settings(c: VoiceConfig, *, what: str, model: Optional[str]) -> tuple[str, str]:
    """(api_key, model) for AIRouter speech; everything comes from `voice.airouter` (never hardcoded)."""
    a = c.airouter
    key = a.api_key or os.environ.get("AIROUTER_API_KEY")
    if not key:
        raise VoiceConfigurationError(
            f"{what} needs an AIRouter key: set voice.airouter.api_key, the AIROUTER_API_KEY environment "
            "variable, or use airouter as the LLM provider (its key is reused).")
    if not model:
        raise VoiceConfigurationError(
            f"{what}: set voice.airouter.{'stt_model' if 'STT' in what else 'tts_model'} in the config "
            "(list: GET https://api.airouter.in/v1/models?type=transcription | speech).")
    return key, model


def _airouter_stt(c: VoiceConfig, client: Optional[httpx.AsyncClient] = None, cost_tracker=None) -> STTProvider:
    key, model = _airouter_settings(c, what="AIRouter STT", model=c.airouter.stt_model)
    return AIRouterSTT(api_key=key, api_base=c.airouter.api_base, model=model,
                       timeout=c.airouter.stt_timeout_seconds, client=client, cost_tracker=cost_tracker,
                       cost_per_minute_usd=c.airouter.stt_cost_per_minute_usd)


def _airouter_tts(c: VoiceConfig, client: Optional[httpx.AsyncClient] = None, cost_tracker=None) -> TTSProvider:
    key, model = _airouter_settings(c, what="AIRouter TTS", model=c.airouter.tts_model)
    return AIRouterTTS(api_key=key, api_base=c.airouter.api_base, model=model, voice=c.airouter.voice,
                       speed=c.airouter.tts_speed, sample_rate=c.output_sample_rate,
                       timeout=c.airouter.tts_timeout_seconds, client=client, cost_tracker=cost_tracker,
                       cost_per_1k_chars_usd=c.airouter.tts_cost_per_1k_chars_usd)


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# ------------------------------------------------------------------ builders

def _openai_realtime_stt(c: VoiceConfig) -> STTProvider:
    return OpenAIRealtimeSTT(api_key=_require_key(c, "Realtime STT"), api_base=c.api_base, model=c.stt_model)


def _openai_batch_stt(c: VoiceConfig) -> STTProvider:
    return OpenAIBatchSTT(api_key=_require_key(c, "Batch STT"), api_base=c.api_base, model=c.stt_model,
                          client=None)


def _vosk_stt(c: VoiceConfig) -> STTProvider:
    from pai.voice.providers.local_models import ensure_vosk_model
    from pai.voice.providers.vosk_stt import VoskSTT
    return VoskSTT(ensure_vosk_model(c.models_dir, c.vosk_model, download=c.auto_download_models))


def _auto_stt(c: VoiceConfig) -> STTProvider:
    if resolve_api_key(c):
        return _openai_realtime_stt(c)
    if _has("vosk"):
        return _vosk_stt(c)
    raise VoiceConfigurationError(
        "No speech-to-text available: set OPENAI_API_KEY, or install the free engine "
        "(pip install vosk) and set voice.stt: vosk.")


def _openai_tts(c: VoiceConfig) -> TTSProvider:
    return OpenAITTS(api_key=_require_key(c, "OpenAI TTS"), api_base=c.api_base, model=c.tts_model,
                     default_voice=c.voice, sample_rate=c.output_sample_rate, client=None)


def _piper_tts(c: VoiceConfig) -> TTSProvider:
    from pai.voice.providers.local_models import ensure_piper_voice
    from pai.voice.providers.piper_tts import PiperTTS
    return PiperTTS(ensure_piper_voice(c.models_dir, c.piper_voice, download=c.auto_download_models),
                    sample_rate=c.output_sample_rate)


def _espeak_tts(c: VoiceConfig) -> TTSProvider:
    if not EspeakTTS.available():
        raise VoiceConfigurationError("TTS provider 'espeak' selected but the espeak binary is not installed")
    return EspeakTTS(sample_rate=c.output_sample_rate)


def _auto_tts(c: VoiceConfig) -> TTSProvider:
    if resolve_api_key(c):
        return _openai_tts(c)
    if _has("piper"):
        return _piper_tts(c)
    if EspeakTTS.available():
        return _espeak_tts(c)
    raise VoiceConfigurationError(
        "No text-to-speech available: set OPENAI_API_KEY, or install a free engine "
        "(pip install piper-tts, or the espeak package).")


for _n, _f in {
    "auto": _auto_stt, "openai_realtime": _openai_realtime_stt, "openai_batch": _openai_batch_stt,
    "airouter": _airouter_stt, "vosk": _vosk_stt, "fake": lambda c: FakeSTT(),
}.items():
    register_stt(_n, _f)
for _n, _f in {
    "auto": _auto_tts, "openai": _openai_tts, "airouter": _airouter_tts, "piper": _piper_tts, "espeak": _espeak_tts,
    "fake": lambda c: FakeTTS(sample_rate=c.output_sample_rate),
}.items():
    register_tts(_n, _f)


def create_stt(config: VoiceConfig, *, client: Optional[httpx.AsyncClient] = None, cost_tracker=None) -> STTProvider:
    name = _key(config.stt)
    if name == "openai_batch" and client is not None:          # tests inject a mock transport
        return OpenAIBatchSTT(api_key=_require_key(config, "Batch STT"), api_base=config.api_base,
                              model=config.stt_model, client=client)
    if name == "airouter":
        return _airouter_stt(config, client, cost_tracker)
    try:
        return _STT[name](config)
    except KeyError:
        raise VoiceConfigurationError(f"unknown STT provider {name!r} (known: {sorted(_STT)})") from None


def create_tts(config: VoiceConfig, *, client: Optional[httpx.AsyncClient] = None, cost_tracker=None) -> TTSProvider:
    name = _key(config.tts)
    if name == "openai" and client is not None:
        return OpenAITTS(api_key=_require_key(config, "OpenAI TTS"), api_base=config.api_base,
                         model=config.tts_model, default_voice=config.voice,
                         sample_rate=config.output_sample_rate, client=client)
    if name == "airouter":
        return _airouter_tts(config, client, cost_tracker)
    try:
        return _TTS[name](config)
    except KeyError:
        raise VoiceConfigurationError(f"unknown TTS provider {name!r} (known: {sorted(_TTS)})") from None
