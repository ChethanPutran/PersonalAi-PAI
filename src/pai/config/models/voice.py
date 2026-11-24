from enum import Enum, StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class STTProviderName(StrEnum):
    AUTO = "auto"                         # OpenAI if an API key is available, else Vosk (free, local)
    OPENAI_REALTIME = "openai_realtime"   # streaming transcription over WebSocket (partials)
    OPENAI_BATCH = "openai_batch"         # one request per utterance (no partials)
    AIROUTER = "airouter"                 # AIRouter /audio/transcriptions, one request per utterance (no partials)
    VOSK = "vosk"                         # FREE, offline, streaming with partials (CPU)
    FAKE = "fake"                         # tests / demos


class TTSProviderName(StrEnum):
    AUTO = "auto"                         # OpenAI if a key is available, else Piper, else espeak
    OPENAI = "openai"                     # streaming PCM over HTTP
    AIROUTER = "airouter"                 # AIRouter /audio/speech (whole clip per sentence, WAV)
    PIPER = "piper"                       # FREE, offline, natural neural voice (CPU)
    ESPEAK = "espeak"                     # FREE, offline, robotic but tiny (espeak binary)
    FAKE = "fake"


class VADConfig(BaseModel):
    frame_ms: int = Field(default=20, ge=10, le=30)
    # Speech starts when energy stays above the adaptive threshold this long...
    min_speech_ms: int = Field(default=120, ge=20)
    # ...and ends after this much trailing silence (the turn is then sent to the assistant).
    end_silence_ms: int = Field(default=700, ge=200)
    pre_roll_ms: int = Field(default=300, ge=0)          # audio kept from just before speech start
    energy_threshold: float = Field(default=0.015, gt=0)  # RMS (0..1) floor; adapts upward to noise
    noise_margin: float = Field(default=3.0, gt=1.0)      # speech must be this many × the noise floor


class VoiceLimits(BaseModel):
    max_utterance_seconds: float = Field(default=30.0, gt=0.5)   # force end-of-speech
    idle_timeout_seconds: float = Field(default=120.0, gt=0.1)    # no audio at all
    max_session_seconds: float = Field(default=3600.0, gt=10)
    max_sessions_per_user: int = Field(default=2, ge=1)
    max_frame_bytes: int = Field(default=65536, ge=1024)


class AIRouterVoiceConfig(BaseModel):
    """AIRouter speech (https://www.airouter.in): used when `voice.stt` / `voice.tts` is `airouter`.

    Nothing here is hardcoded in the providers: the endpoint, the models and the voice all come from
    this section of config.yaml. List the available ids with GET {api_base}/models?type=transcription
    (or `speech`)."""
    api_base: str = "https://api.airouter.in/v1"
    api_key: Optional[str] = Field(default=None, repr=False)    # else AIROUTER_API_KEY, else the LLM key
    stt_model: Optional[str] = None       # required when voice.stt = airouter
    tts_model: Optional[str] = None       # required when voice.tts = airouter
    voice: Optional[str] = None           # provider-specific voice name; null = the model's default
    tts_speed: Optional[float] = Field(default=None, gt=0)      # null = the model's default
    # Prices in USD (AIRouter bills transcription per second of audio and speech per input character). Used to
    # record voice cost in the usage tracker and deduct it from the credits; null = cost unknown (still counted).
    stt_cost_per_minute_usd: Optional[float] = Field(default=None, ge=0)
    tts_cost_per_1k_chars_usd: Optional[float] = Field(default=None, ge=0)
    stt_timeout_seconds: float = Field(default=30.0, gt=1)
    tts_timeout_seconds: float = Field(default=30.0, gt=1)


class VoiceConfig(BaseModel):
    """Realtime voice interaction (see pai/voice)."""
    enabled: bool = True
    # Providers are chosen here. "auto" = OpenAI when a key is configured, otherwise the free
    # local engines (Vosk speech-to-text, Piper text-to-speech).
    stt: STTProviderName = STTProviderName.AUTO
    tts: TTSProviderName = TTSProviderName.AUTO
    api_key: Optional[str] = Field(default=None, repr=False)   # falls back to OPENAI_API_KEY
    api_base: str = "https://api.openai.com/v1"
    stt_model: str = "gpt-4o-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    voice: str = "alloy"                           # OpenAI voice name (ignored by local engines)

    # Free local engines (see pai/voice/providers/local_models.py)
    models_dir: str = "./data/voice_models"
    auto_download_models: bool = True              # fetch missing models on first use
    vosk_model: str = "vosk-model-small-en-us-0.15"
    piper_voice: str = "en_US-lessac-medium"

    airouter: AIRouterVoiceConfig = Field(default_factory=AIRouterVoiceConfig)

    language: Optional[str] = None                 # e.g. "en"; None = auto-detect

    input_sample_rate: int = 16000                 # what clients send (PCM16 mono)
    output_sample_rate: int = 24000                # what the server sends back (PCM16 mono)

    vad: VADConfig = Field(default_factory=VADConfig)
    limits: VoiceLimits = Field(default_factory=VoiceLimits)

    # Barge-in: speech this long while the assistant is talking interrupts it.
    barge_in: bool = True
    barge_in_min_speech_ms: int = Field(default=250, ge=60)
    # Spoken filler when a turn needs tools and takes long ("One moment…"); empty = none.
    thinking_filler: str = ""
    thinking_filler_after_seconds: float = 2.5
    # First speakable chunk is released early (low latency) once it has this many characters.
    first_chunk_min_chars: int = Field(default=24, ge=8)
