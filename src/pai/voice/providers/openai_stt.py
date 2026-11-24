"""OpenAI speech-to-text providers.

* ``OpenAIRealtimeSTT`` — streaming: audio goes out over a WebSocket while the user is still
  speaking and interim text comes back (``partials()``).
* ``OpenAIBatchSTT``    — one HTTP request per utterance, no partials.

Both are per-utterance: ``open()`` returns a stream for ONE utterance.

NOTE (not verified against the live API — written from memory of the docs): the realtime
transcription endpoint/URL, the ``transcription_session.update`` payload, the
``input_audio_buffer.append|commit`` messages and the
``conversation.item.input_audio_transcription.delta|completed`` events, plus the
``OpenAI-Beta: realtime=v1`` header; and the ``/audio/transcriptions`` multipart form.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import wave
from typing import AsyncIterator, Literal, Optional

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from pai.voice.audio import resample
from pai.voice.protocols import TranscriptEvent
from pai.voice.providers.errors import VoiceProviderError

_REALTIME_RATE = 24000          # the realtime API takes PCM16 @ 24 kHz
_MAX_UTTERANCE_BYTES = 16000 * 2 * 120     # batch buffer cap: 2 minutes @ 16 kHz


# ------------------------------------------------------------------ typed wire messages

class _TranscriptionSettings(BaseModel):
    model: str
    language: Optional[str] = None


class _SessionSettings(BaseModel):
    input_audio_format: Literal["pcm16"] = "pcm16"
    input_audio_transcription: _TranscriptionSettings
    turn_detection: None = None           # server VAD OFF: we commit explicitly


class _SessionUpdate(BaseModel):
    type: Literal["transcription_session.update"] = "transcription_session.update"
    session: _SessionSettings


class _AudioAppend(BaseModel):
    type: Literal["input_audio_buffer.append"] = "input_audio_buffer.append"
    audio: str


class _AudioCommit(BaseModel):
    type: Literal["input_audio_buffer.commit"] = "input_audio_buffer.commit"


class _ErrorBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: Optional[str] = None
    code: Optional[str] = None


class _ServerEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    delta: Optional[str] = None
    transcript: Optional[str] = None
    error: Optional[_ErrorBody] = None


def _ws_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/realtime?intent=transcription"


# ------------------------------------------------------------------ realtime (streaming)

class _RealtimeStream:
    """STTStream for one utterance over an open realtime WebSocket."""

    _END = object()

    def __init__(self, ws, *, sample_rate: int, finish_timeout: float) -> None:
        self._ws = ws
        self._sample_rate = sample_rate
        self._finish_timeout = finish_timeout
        self._queue: "asyncio.Queue[object]" = asyncio.Queue()
        self._final: "asyncio.Future[str]" = asyncio.get_running_loop().create_future()
        self._text = ""
        self._pushed = False
        self._closed = False
        self._reader = asyncio.create_task(self._read(), name="openai-stt-reader")

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    event = _ServerEvent.model_validate_json(raw)
                except ValidationError:
                    continue
                if event.type == "conversation.item.input_audio_transcription.delta":
                    if event.delta:
                        self._text += event.delta
                        await self._queue.put(TranscriptEvent(text=self._text, final=False))
                elif event.type == "conversation.item.input_audio_transcription.completed":
                    if not self._final.done():
                        self._final.set_result((event.transcript or self._text).strip())
                elif event.type == "error":
                    msg = event.error.message if event.error else "unknown error"
                    logger.warning("openai realtime stt error: {}", msg)
                    if not self._final.done():
                        self._final.set_result("")
        except ConnectionClosed:
            pass
        except Exception as exc:                      # reader must never raise unobserved
            logger.warning("openai realtime stt reader failed: {}", exc)
        finally:
            if not self._final.done():
                self._final.set_result("")
            await self._queue.put(self._END)

    async def push(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        audio = resample(pcm, self._sample_rate, _REALTIME_RATE)
        msg = _AudioAppend(audio=base64.b64encode(audio).decode("ascii"))
        try:
            await self._ws.send(msg.model_dump_json())
            self._pushed = True
        except ConnectionClosed:
            self._closed = True

    async def partials(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self._queue.get()
            if item is self._END:
                self._queue.put_nowait(self._END)     # keep the iterator re-entrant
                return
            yield item                                # type: ignore[misc]

    async def finish(self) -> str:
        if self._closed or not self._pushed:
            await self.aclose()
            return ""
        try:
            await self._ws.send(_AudioCommit().model_dump_json())
            return await asyncio.wait_for(asyncio.shield(self._final), self._finish_timeout)
        except (asyncio.TimeoutError, ConnectionClosed):
            logger.warning("openai realtime stt: no final transcript (timeout/closed)")
            return ""
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed and self._reader.done():
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._ws.close()
        if not self._reader.done():
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if not self._final.done():
            self._final.set_result("")


class OpenAIRealtimeSTT:
    """Streaming STT (partials) via the OpenAI Realtime transcription WebSocket.

    One connection per utterance (simple and robust; a warm pool would save ~100-300 ms of
    connect latency and is a possible later optimisation).
    """
    name = "openai_realtime"
    supports_partials = True

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-transcribe",
        connect_timeout: float = 5.0,
        finish_timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._url = _ws_url(api_base)
        self._model = model
        self._connect_timeout = connect_timeout
        self._finish_timeout = finish_timeout

    async def open(self, *, sample_rate: int, language: Optional[str]) -> _RealtimeStream:
        try:
            ws = await connect(
                self._url,
                additional_headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "OpenAI-Beta": "realtime=v1",
                },
                open_timeout=self._connect_timeout,
                max_size=None,
            )
        except Exception as exc:
            raise VoiceProviderError(f"could not connect to realtime STT: {exc}") from exc

        settings = _TranscriptionSettings(model=self._model, **({"language": language} if language else {}))
        # exclude_unset omits `language` when not given and keeps `turn_detection: null`; the
        # defaulted discriminator fields therefore have to be set explicitly to be serialized.
        update = _SessionUpdate(
            type="transcription_session.update",
            session=_SessionSettings(
                input_audio_format="pcm16", input_audio_transcription=settings, turn_detection=None),
        )
        await ws.send(update.model_dump_json(exclude_unset=True))
        return _RealtimeStream(ws, sample_rate=sample_rate, finish_timeout=self._finish_timeout)

    async def aclose(self) -> None:
        return None


# ------------------------------------------------------------------ batch (one request)

class _BatchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str = ""


class _BatchStream:
    def __init__(self, provider: "OpenAIBatchSTT", sample_rate: int, language: Optional[str]) -> None:
        self._p = provider
        self._rate = sample_rate
        self._language = language
        self._buf = bytearray()
        self._closed = False

    async def push(self, pcm: bytes) -> None:
        if self._closed:
            return
        if len(self._buf) + len(pcm) <= _MAX_UTTERANCE_BYTES:
            self._buf += pcm

    async def partials(self) -> AsyncIterator[TranscriptEvent]:
        return
        yield  # pragma: no cover  (makes this an async generator that yields nothing)

    async def finish(self) -> str:
        if self._closed or not self._buf:
            self._closed = True
            return ""
        self._closed = True
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self._rate)
            w.writeframes(bytes(self._buf))
        data = {"model": self._p._model, "response_format": "json"}
        if self._language:
            data["language"] = self._language
        try:
            resp = await self._p._client.post(
                f"{self._p._api_base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._p._api_key}"},
                files={"file": ("utterance.wav", wav.getvalue(), "audio/wav")},
                data=data,
                timeout=self._p._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("openai batch stt request failed: {}", exc)
            return ""
        if resp.status_code != 200:
            logger.warning("openai batch stt HTTP {}: {}", resp.status_code, resp.text[:200])
            return ""
        try:
            text = _BatchResponse.model_validate_json(resp.content).text.strip()
        except ValidationError:
            return ""
        try:
            payload = json.loads(resp.content)
        except ValueError:
            payload = {}
        self._p._after_request(audio_seconds=len(self._buf) / 2 / self._rate, payload=payload if isinstance(payload, dict) else {})
        return text

    async def aclose(self) -> None:
        self._closed = True
        self._buf.clear()


class OpenAIBatchSTT:
    name = "openai_batch"
    supports_partials = False

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-transcribe",
        timeout: float = 20.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

    def _after_request(self, *, audio_seconds: float, payload: dict) -> None:
        """Called after every successful transcription (subclasses account for cost here)."""

    async def open(self, *, sample_rate: int, language: Optional[str]) -> _BatchStream:
        return _BatchStream(self, sample_rate, language)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
