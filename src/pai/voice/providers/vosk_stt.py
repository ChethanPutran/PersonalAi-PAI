"""Free, offline, STREAMING speech-to-text with Vosk (Kaldi). CPU only, ~40 MB model.

Gives real partial transcripts while the user is still talking. Recognition is CPU work,
so it runs in a worker thread and never blocks the event loop. Vosk models are
single-language: ``language`` is accepted for interface compatibility but the model decides.
"""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncIterator, List, Optional

from loguru import logger
from pydantic import BaseModel

from pai.voice.protocols import TranscriptEvent
from pai.voice.providers.errors import VoiceConfigurationError


class _VoskResult(BaseModel):
    text: str = ""
    partial: str = ""


def _parse(raw: str) -> _VoskResult:
    try:
        return _VoskResult.model_validate(json.loads(raw))
    except Exception:
        return _VoskResult()


class VoskSTTStream:
    def __init__(self, recognizer, pool: ThreadPoolExecutor) -> None:
        self._rec, self._pool = recognizer, pool
        self._segments: List[str] = []           # text Vosk already finalized mid-utterance
        self._last_partial = ""
        self._events: "asyncio.Queue[Optional[TranscriptEvent]]" = asyncio.Queue()
        self._closed = False
        self._finished = False

    async def push(self, pcm: bytes) -> None:
        if self._closed or self._finished or not pcm:
            return
        loop = asyncio.get_running_loop()
        done, raw = await loop.run_in_executor(self._pool, self._accept, pcm)
        if done:                                  # Vosk hit its own pause: keep that sentence
            seg = _parse(raw).text
            if seg:
                self._segments.append(seg)
                self._last_partial = ""
                self._events.put_nowait(TranscriptEvent(text=" ".join(self._segments)))
        else:
            partial = _parse(raw).partial
            if partial and partial != self._last_partial:
                self._last_partial = partial
                self._events.put_nowait(TranscriptEvent(text=" ".join([*self._segments, partial])))

    def _accept(self, pcm: bytes):
        done = self._rec.AcceptWaveform(pcm)
        return done, (self._rec.Result() if done else self._rec.PartialResult())

    async def partials(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self._events.get()
            if item is None:
                self._events.put_nowait(None)
                return
            yield item

    async def finish(self) -> str:
        if self._closed:
            return ""
        self._finished = True
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(self._pool, self._rec.FinalResult)
        tail = _parse(raw).text
        text = " ".join([*self._segments, *([tail] if tail else [])]).strip()
        self._events.put_nowait(None)
        return text

    async def aclose(self) -> None:
        self._closed = True
        self._events.put_nowait(None)


class VoskSTT:
    name = "vosk"
    supports_partials = True

    def __init__(self, model_path: Path, *, workers: int = 2) -> None:
        try:
            import vosk
        except ImportError as exc:
            raise VoiceConfigurationError(
                "Vosk is not installed. Install the free local voice extras: "
                "pip install 'ai-personal-agent[voice-local]' (or: pip install vosk)") from exc
        vosk.SetLogLevel(-1)
        self._vosk = vosk
        logger.info("Loading Vosk model from {}", model_path)
        self._model = vosk.Model(str(model_path))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vosk")

    async def open(self, *, sample_rate: int, language: Optional[str]) -> VoskSTTStream:
        # Vosk resamples internally for common rates; we hand it the client's rate directly.
        rec = self._vosk.KaldiRecognizer(self._model, float(sample_rate))
        return VoskSTTStream(rec, self._pool)

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
