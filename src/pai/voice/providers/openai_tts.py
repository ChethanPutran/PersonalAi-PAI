"""OpenAI text-to-speech: streams raw PCM over HTTP.

NOTE (not verified against the live API): ``POST {api_base}/audio/speech`` with
``response_format: "pcm"`` returns headerless 24 kHz, 16-bit little-endian mono PCM, streamed
as chunked transfer; ``model``/``voice``/``input`` are the request fields.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

import httpx
from pydantic import BaseModel

from pai.voice.audio import frame_bytes, resample
from pai.voice.providers.errors import VoiceProviderError

_OPENAI_PCM_RATE = 24000
_MAX_INPUT_CHARS = 4000


class _SpeechRequest(BaseModel):
    model: str
    voice: str
    input: str
    response_format: str = "pcm"


class OpenAITTS:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini-tts",
        default_voice: str = "alloy",
        sample_rate: int = 24000,
        chunk_ms: int = 60,
        timeout: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = api_key
        self._url = f"{api_base.rstrip('/')}/audio/speech"
        self._model = model
        self._default_voice = default_voice
        self.sample_rate = sample_rate
        self._chunk = frame_bytes(_OPENAI_PCM_RATE, chunk_ms)      # source-rate bytes per chunk
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

    async def synthesize(
        self, text: str, *, voice: Optional[str] = None, language: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        text = (text or "").strip()[:_MAX_INPUT_CHARS]
        if not text:
            return
        body = _SpeechRequest(model=self._model, voice=voice or self._default_voice, input=text)
        pending = b""
        # Leaving this `async with` (normal end, aclose(), or task cancellation) closes the
        # HTTP response, which aborts the upstream transfer.
        async with self._client.stream(
            "POST", self._url,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            content=body.model_dump_json(),
            timeout=self._timeout,
        ) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise VoiceProviderError(f"OpenAI TTS failed (HTTP {resp.status_code}): {detail}")
            async for piece in resp.aiter_bytes():
                pending += piece
                while len(pending) >= self._chunk:
                    chunk, pending = pending[: self._chunk], pending[self._chunk:]
                    yield resample(chunk, _OPENAI_PCM_RATE, self.sample_rate)
            pending = pending[: len(pending) - (len(pending) % 2)]     # never emit half a sample
            if pending:
                yield resample(pending, _OPENAI_PCM_RATE, self.sample_rate)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
