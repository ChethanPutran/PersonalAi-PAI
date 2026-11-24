"""Provider contracts. Concrete providers live in pai/voice/providers/."""
from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from pydantic import BaseModel


class TranscriptEvent(BaseModel):
    text: str
    final: bool = False


@runtime_checkable
class STTStream(Protocol):
    """Speech-to-text for ONE utterance."""

    async def push(self, pcm: bytes) -> None:
        """Feed PCM16LE mono at the sample rate given to ``STTProvider.open``."""

    def partials(self) -> AsyncIterator[TranscriptEvent]:
        """Interim hypotheses while audio is being pushed (may yield nothing).
        The iterator ends when the stream is finished or closed."""

    async def finish(self) -> str:
        """No more audio: flush and return the FINAL transcript ('' if nothing was said)."""

    async def aclose(self) -> None:
        """Abort and release resources. Safe to call at any time, more than once."""


@runtime_checkable
class STTProvider(Protocol):
    name: str
    supports_partials: bool

    async def open(self, *, sample_rate: int, language: Optional[str]) -> STTStream: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    sample_rate: int          # rate of the PCM it yields (already resampled)

    def synthesize(
        self, text: str, *, voice: Optional[str], language: Optional[str],
    ) -> AsyncIterator[bytes]:
        """Yield PCM16LE mono chunks (~20–200 ms each) as soon as they exist.
        Must stop promptly (closing the HTTP stream / killing the process) when the
        consuming task is cancelled — that is how barge-in works."""

    async def aclose(self) -> None: ...
