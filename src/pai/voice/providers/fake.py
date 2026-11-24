"""Deterministic providers for tests and demos (no network, no models).

FakeSTT   scripted transcripts, optional partials, records the audio it was fed.
FakeTTS   emits sine-tone PCM whose duration is proportional to the text length, records
          what was spoken / cancelled.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional, Sequence

from pai.voice.audio import frame_bytes, sine
from pai.voice.protocols import TranscriptEvent


class FakeSTTStream:
    def __init__(self, transcript: str, *, sample_rate: int, partial_every_bytes: int, delay: float) -> None:
        self.transcript = transcript
        self.sample_rate = sample_rate
        self.pushed: List[bytes] = []                  # every pcm chunk received
        self.closed = False
        self.finished = False
        self._every = partial_every_bytes
        self._delay = delay
        self._bytes = 0
        self._emitted = 0
        self._queue: "asyncio.Queue[Optional[TranscriptEvent]]" = asyncio.Queue()

    @property
    def audio(self) -> bytes:
        return b"".join(self.pushed)

    async def push(self, pcm: bytes) -> None:
        if self.closed or self.finished:
            return
        self.pushed.append(pcm)
        self._bytes += len(pcm)
        words = self.transcript.split()
        while self._every and self._bytes // self._every > self._emitted and self._emitted < len(words) - 1:
            self._emitted += 1
            self._queue.put_nowait(TranscriptEvent(text=" ".join(words[: self._emitted]), final=False))

    async def partials(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.put_nowait(None)
                return
            yield item

    async def finish(self) -> str:
        if self.closed:
            return ""
        self.finished = True
        if self._delay:
            await asyncio.sleep(self._delay)
        self._queue.put_nowait(None)
        return self.transcript if self.pushed else ""

    async def aclose(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


class FakeSTT:
    """``script``: the transcript for each successive utterance (then '' once exhausted)."""
    name = "fake"
    supports_partials = True

    def __init__(
        self, script: Sequence[str] = (), *, partial_every_bytes: int = 3200, finish_delay: float = 0.0,
    ) -> None:
        self._script = list(script)
        self._every = partial_every_bytes
        self._delay = finish_delay
        self.streams: List[FakeSTTStream] = []

    def add(self, *transcripts: str) -> None:
        self._script.extend(transcripts)

    async def open(self, *, sample_rate: int, language: Optional[str]) -> FakeSTTStream:
        transcript = self._script.pop(0) if self._script else ""
        stream = FakeSTTStream(transcript, sample_rate=sample_rate,
                               partial_every_bytes=self._every, delay=self._delay)
        self.streams.append(stream)
        return stream

    async def aclose(self) -> None:
        return None


class FakeTTS:
    """Speaks ``ms_per_char`` of 440 Hz tone per character, in ``chunk_ms`` chunks.

    ``calls`` — every text passed to synthesize; ``completed`` / ``cancelled`` — which of
    them finished vs. were abandoned (barge-in); ``chunk_delay`` simulates synthesis latency.
    """
    name = "fake"

    def __init__(
        self, *, sample_rate: int = 24000, ms_per_char: int = 40, chunk_ms: int = 40,
        chunk_delay: float = 0.0, first_chunk_delay: float = 0.0,
    ) -> None:
        self.sample_rate = sample_rate
        self._ms_per_char = ms_per_char
        self._chunk_ms = chunk_ms
        self._chunk_delay = chunk_delay
        self._first_delay = first_chunk_delay
        self.calls: List[str] = []
        self.completed: List[str] = []
        self.cancelled: List[str] = []

    def duration_ms(self, text: str) -> int:
        return max(self._chunk_ms, len(text.strip()) * self._ms_per_char)

    async def synthesize(
        self, text: str, *, voice: Optional[str] = None, language: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        text = text.strip()
        if not text:
            return
        self.calls.append(text)
        finished = False
        try:
            if self._first_delay:
                await asyncio.sleep(self._first_delay)
            remaining = self.duration_ms(text)
            while remaining > 0:
                ms = min(self._chunk_ms, remaining)
                yield sine(440.0, ms, self.sample_rate)
                remaining -= ms
                if self._chunk_delay:
                    await asyncio.sleep(self._chunk_delay)
            finished = True
            self.completed.append(text)
        finally:
            if not finished:
                self.cancelled.append(text)

    async def aclose(self) -> None:
        return None
