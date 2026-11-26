"""Voice: sentences are synthesized ahead of playback (no gap per sentence), order is kept, barge-in
cancels what was prefetched, and the trace records what the user actually waited."""
import asyncio
import sys
import time
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_session import Harness, ScriptedRunner, cfg  # noqa: E402

from pai.voice.models import AssistantAudioEnd, InterruptReason  # noqa: E402
from pai.voice.providers.fake import FakeTTS  # noqa: E402

SENTENCES = ("First sentence is here. ", "Second sentence follows it. ", "Third one closes the reply.")


class SlowTTS:
    """Each segment takes ``delay`` to synthesize (like a remote TTS), then yields tagged PCM."""
    name = "slow"
    sample_rate = 24000

    def __init__(self, delay: float, per_segment: Optional[list] = None):
        self.delay, self.per_segment = delay, per_segment
        self.started, self.cancelled, self._n = [], [], 0

    async def synthesize(self, text, *, voice=None, language=None) -> AsyncIterator[bytes]:
        idx = self._n; self._n += 1
        self.started.append(text)
        try:
            await asyncio.sleep(self.per_segment[idx] if self.per_segment else self.delay)
            yield bytes([idx + 1]) * 640
        except asyncio.CancelledError:
            self.cancelled.append(text)
            raise


async def run_reply(tts, prefetch, settle=2.0):
    h = Harness(runner=ScriptedRunner(chunks=SENTENCES), tts=tts, config=cfg(tts_prefetch=prefetch))
    await h.session.start()
    t0 = time.monotonic()
    await h.say()
    for _ in range(int(settle / 0.02)):
        if h.of(AssistantAudioEnd):
            break
        await asyncio.sleep(0.02)
    elapsed = time.monotonic() - t0
    await h.session.close()
    return h, elapsed


async def test_prefetch_removes_the_gap_per_sentence():
    _, serial = await run_reply(SlowTTS(0.3), prefetch=1)
    h, fast = await run_reply(SlowTTS(0.3), prefetch=3)
    assert len(h.audio) == 3
    assert fast < serial - 0.35, (fast, serial)       # ~0.3 s instead of ~0.9 s of synthesis waits


async def test_playback_stays_in_order_even_when_a_later_segment_finishes_first():
    tts = SlowTTS(0, per_segment=[0.4, 0.1, 0.02])    # the LAST sentence is ready first
    h, _ = await run_reply(tts, prefetch=3)
    assert [chunk[0] for chunk in h.audio] == [1, 2, 3]


async def test_barge_in_cancels_segments_still_being_synthesized():
    tts = SlowTTS(30.0)                                # nothing ever finishes
    h = Harness(runner=ScriptedRunner(chunks=SENTENCES), tts=tts, config=cfg(tts_prefetch=3))
    await h.session.start()
    await h.say()
    await asyncio.sleep(0.3)
    assert len(tts.started) == 3                       # all three were prefetched
    await h.session.interrupt(InterruptReason.BARGE_IN)
    await asyncio.sleep(0.1)
    assert len(tts.cancelled) == 3                     # none keeps running after the interruption
    await h.session.close()


async def test_a_failed_segment_is_reported_and_the_rest_still_play():
    class FlakyTTS(SlowTTS):
        async def synthesize(self, text, *, voice=None, language=None):
            if text.startswith("Second"):
                raise RuntimeError("provider hiccup")
            async for pcm in super().synthesize(text, voice=voice, language=language):
                yield pcm
    h, _ = await run_reply(FlakyTTS(0.02), prefetch=2)
    assert len(h.audio) == 2
    assert any(getattr(e, "code", None) == "tts_failed" for e in h.events)


async def test_trace_records_end_of_speech_to_first_audio(monkeypatch):
    spans = []

    class Tracer:
        def record_span(self, trace_id, name, *, start, attributes=None, error=None):
            spans.append((name, dict(attributes or {})))
    monkeypatch.setattr("pai.voice.session.get_tracer", lambda: Tracer())
    await run_reply(FakeTTS(ms_per_char=5, chunk_ms=40), prefetch=2)
    names = [n for n, _ in spans]
    assert "voice.latency" in names and "voice.stt" in names
    latency = dict(spans)["voice.latency"]
    assert latency["first_audio_after_speech_end_ms"] >= 0 and latency["stt_finalize_ms"] is not None
    assert dict(spans)["voice.stt"]["finalize_ms"] is not None
