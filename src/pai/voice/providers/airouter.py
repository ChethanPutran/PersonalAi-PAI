"""AIRouter speech providers (https://www.airouter.in): OpenAI-compatible audio endpoints.

* ``AIRouterSTT`` — ``POST /audio/transcriptions``: one request per utterance, NO partial captions
  (the endpoint takes a complete file; live streaming transcription is not offered).
* ``AIRouterTTS`` — ``POST /audio/speech``: returns a whole audio file per request (no streaming, and
  ``pcm`` is not offered), so it asks for WAV, decodes it and yields PCM chunks. The voice pipeline
  already synthesizes sentence by sentence, so the first sentence is heard after one round trip.

Models, voice and endpoint are constructor arguments supplied from ``voice.airouter`` in config.
"""
from __future__ import annotations

import struct
from typing import Any, AsyncIterator, Optional

import httpx
from loguru import logger

from pai.voice import usage as voice_usage
from pai.voice.audio import frame_bytes, resample
from pai.voice.providers.errors import VoiceProviderError
from pai.voice.providers.openai_stt import OpenAIBatchSTT

_MAX_INPUT_CHARS = 4000          # below the smallest documented per-request limit (4096, OpenAI models)


ROUTE_STT = "voice.stt"
ROUTE_TTS = "voice.tts"


class AIRouterSTT(OpenAIBatchSTT):
    """The OpenAI-style multipart transcription call, pointed at AIRouter with a config-chosen model.

    Billed per second of audio: when a `cost_tracker` is given, every successful call is recorded with the seconds
    AIRouter says it billed (`usage.seconds`, rounded by some providers) or, failing that, the audio's length."""
    name = "airouter"
    supports_partials = False

    def __init__(self, *, api_key: str, api_base: str, model: str, timeout: float = 30.0,
                 client: Optional[httpx.AsyncClient] = None, cost_tracker: Any = None,
                 cost_per_minute_usd: Optional[float] = None) -> None:
        super().__init__(api_key=api_key, api_base=api_base, model=model, timeout=timeout, client=client)
        self._tracker, self._per_minute = cost_tracker, cost_per_minute_usd

    def _after_request(self, *, audio_seconds: float, payload: dict) -> None:
        try:
            billed = (payload.get("usage") or {}).get("seconds")
            seconds = float(billed) if isinstance(billed, (int, float)) and billed >= 0 else audio_seconds
            cost = None if self._per_minute is None else seconds / 60.0 * self._per_minute
            voice_usage.report(model=self._model, units=round(seconds, 3), unit="seconds", cost_usd=cost)
            if self._tracker is None:
                return
            self._tracker.record_metered(provider="airouter", model=self._model, route=ROUTE_STT,
                                         units=round(seconds, 3), unit="seconds", cost_usd=cost)
        except Exception as exc:                       # accounting must never break a voice turn
            logger.warning("voice cost accounting failed: {}", exc)


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """WAV bytes -> (PCM16LE mono, sample_rate).

    Hand-rolled instead of the ``wave`` module because TTS services often stream WAV with a placeholder
    data size (0 or 0xFFFFFFFF), which ``wave`` rejects; here a bad size means "the rest of the file".
    Accepts 16-bit PCM, mono or stereo (stereo is averaged to mono).
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VoiceProviderError("TTS returned audio that is not a WAV file")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and body + 16 <= len(data):
            fmt = struct.unpack_from("<HHIIHH", data, body)       # tag, channels, rate, byterate, align, bits
        elif cid == b"data":
            end = body + size
            pcm = data[body:] if size in (0, 0xFFFFFFFF) or end > len(data) else data[body:end]
            break
        pos = body + size + (size & 1)
    if fmt is None or pcm is None:
        raise VoiceProviderError("TTS returned a WAV file without format/data chunks")
    tag, channels, rate, _, _, bits = fmt
    if tag not in (1, 0xFFFE) or bits != 16 or channels not in (1, 2) or rate <= 0:
        raise VoiceProviderError(f"unsupported WAV from TTS (format={tag}, bits={bits}, channels={channels})")
    pcm = pcm[: len(pcm) - (len(pcm) % (2 * channels))]
    if channels == 2:
        import numpy as np
        pair = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2).astype(np.int32)
        pcm = ((pair[:, 0] + pair[:, 1]) // 2).astype("<i2").tobytes()
    return pcm, rate


class AIRouterTTS:
    name = "airouter"

    def __init__(self, *, api_key: str, api_base: str, model: str, voice: Optional[str] = None,
                 speed: Optional[float] = None, sample_rate: int = 24000, chunk_ms: int = 60,
                 timeout: float = 30.0, client: Optional[httpx.AsyncClient] = None, cost_tracker: Any = None,
                 cost_per_1k_chars_usd: Optional[float] = None) -> None:
        self._api_key = api_key
        self._url = f"{api_base.rstrip('/')}/audio/speech"
        self._model = model
        self._default_voice = voice
        self._speed = speed
        self.sample_rate = sample_rate
        self._chunk_ms = chunk_ms
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._tracker, self._per_1k = cost_tracker, cost_per_1k_chars_usd

    def _account(self, text: str, resp: httpx.Response) -> None:
        """Billed per input character: AIRouter reports what it billed in `X-AIRouter-Characters` (providers count
        differently: Fish Audio counts UTF-8 bytes), else the length of the text we sent."""
        try:
            raw = resp.headers.get("x-airouter-characters", "")
            chars = int(raw) if raw.strip().isdigit() else len(text)
            cost = None if self._per_1k is None else chars / 1000.0 * self._per_1k
            voice_usage.report(model=self._model, units=chars, unit="characters", cost_usd=cost)
            if self._tracker is None:
                return
            self._tracker.record_metered(provider="airouter", model=self._model, route=ROUTE_TTS, units=chars,
                                         unit="characters", cost_usd=cost)
        except Exception as exc:
            logger.warning("voice cost accounting failed: {}", exc)

    async def synthesize(self, text: str, *, voice: Optional[str] = None,
                         language: Optional[str] = None) -> AsyncIterator[bytes]:
        text = (text or "").strip()[:_MAX_INPUT_CHARS]
        if not text:
            return
        body: dict = {"model": self._model, "input": text, "response_format": "wav"}
        chosen = voice or self._default_voice
        if chosen:
            body["voice"] = chosen
        if self._speed is not None:
            body["speed"] = self._speed
        # Cancelling the consuming task (barge-in) cancels this await and closes the connection.
        try:
            resp = await self._client.post(
                self._url, headers={"Authorization": f"Bearer {self._api_key}"}, json=body,
                timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise VoiceProviderError(f"AIRouter TTS request failed: {exc}") from exc
        if resp.status_code != 200:
            raise VoiceProviderError(f"AIRouter TTS failed (HTTP {resp.status_code}): {resp.text[:300]}")
        self._account(text, resp)
        pcm, rate = wav_to_pcm(resp.content)
        logger.debug("AIRouter TTS: {} chars -> {} bytes @ {} Hz (billed chars: {})", len(text), len(pcm),
                     rate, resp.headers.get("x-airouter-characters"))
        pcm = resample(pcm, rate, self.sample_rate)
        size = frame_bytes(self.sample_rate, self._chunk_ms)
        for i in range(0, len(pcm), size):
            yield pcm[i:i + size]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
