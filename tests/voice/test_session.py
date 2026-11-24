"""VoiceSession: streaming turn, barge-in, echo rejection, cancellation, limits."""
import asyncio
from typing import List, Optional

import pytest

from pai.config.models.voice import VADConfig, VoiceConfig, VoiceLimits
from pai.voice.audio import sine
from pai.voice.models import (
    AssistantAudioEnd, AssistantAudioStart, AssistantInterrupted, AssistantTextDelta,
    AssistantTextDone, ErrorEvent, InputText, InterruptReason, Ping, Pong, ResponseCancel,
    SessionEnded, SpeechStarted, SpeechStopped, StateChanged, TranscriptFinal, TranscriptPartial,
    VoiceState,
)
from pai.voice.providers.fake import FakeSTT, FakeTTS
from pai.voice.session import TurnOutcome, VoiceSession

RATE = 16000
FRAME = 320                                   # 20 ms of PCM16 @16k = 640 bytes (we send 2 frames)


def cfg(**kw) -> VoiceConfig:
    base = dict(
        vad=VADConfig(min_speech_ms=60, end_silence_ms=200, pre_roll_ms=60, energy_threshold=0.02),
        barge_in_min_speech_ms=100,
        limits=VoiceLimits(idle_timeout_seconds=30),
    )
    base.update(kw)
    return VoiceConfig(**base)


def loud(ms: int) -> bytes: return sine(300.0, ms, RATE, amplitude=0.5)
def quiet(ms: int) -> bytes: return b"\x00\x00" * (RATE * ms // 1000)


class ScriptedRunner:
    """Pretends to be the orchestrator: streams `chunks` with `delay` between them."""
    def __init__(self, chunks=("Sure thing. ", "Here is the answer. ", "Anything else?"), delay=0.0, hang=False):
        self.chunks, self.delay, self.hang = list(chunks), delay, hang
        self.calls: List[str] = []
        self.cancelled = 0
        self.interrupted: List[str] = []
        self.finished_at: Optional[int] = None
        self.order: List[str] = []

    async def run_turn(self, *, user_id, session_id, device_id, text, turn_id, sink, on_status):
        self.calls.append(text)
        await on_status("planning", "thinking")
        try:
            if self.hang:
                await asyncio.sleep(30)
            for c in self.chunks:
                await sink.on_delta(c)
                self.order.append("delta")
                if self.delay:
                    await asyncio.sleep(self.delay)
            self.order.append("runner_done")
            return TurnOutcome(reply="".join(self.chunks))
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def on_interrupted(self, *, user_id, session_id, turn_id, spoken_text):
        self.interrupted.append(spoken_text)


class Harness:
    def __init__(self, runner=None, stt=None, tts=None, config=None, **kw):
        self.events: list = []
        self.audio: List[bytes] = []
        self.marks: List[tuple] = []          # (kind, index into events) for ordering checks
        self.stt = stt or FakeSTT(["hello assistant"])
        self.tts = tts or FakeTTS(ms_per_char=20, chunk_ms=40)
        self.runner = runner or ScriptedRunner()
        self.config = config or cfg()
        self.session = VoiceSession(
            config=self.config, stt=self.stt, tts=self.tts, runner=self.runner, user_id="u1",
            chat_session_id="chat1", device_id="dev1", send_event=self._event, send_audio=self._audio,
            input_sample_rate=RATE, **kw)

    async def _event(self, e): self.events.append(e)
    async def _audio(self, pcm):
        self.audio.append(pcm)
        self.events.append(("audio", len(pcm)))

    def of(self, cls): return [e for e in self.events if isinstance(e, cls)]
    def index(self, cls): return next(i for i, e in enumerate(self.events) if isinstance(e, cls))

    async def feed(self, pcm: bytes, step_ms: int = 20, pace: float = 0.0):
        n = RATE * step_ms // 1000 * 2
        for i in range(0, len(pcm), n):
            await self.session.on_audio(pcm[i:i + n])
            await asyncio.sleep(pace)

    async def say(self, ms=400, then_silence=300):
        await self.feed(loud(ms))
        await self.feed(quiet(then_silence))

    async def settle(self, t=0.3): await asyncio.sleep(t)


@pytest.fixture
async def h():
    harness = Harness()
    await harness.session.start()
    yield harness
    await harness.session.close()


async def test_full_streaming_turn_event_order(h):
    await h.say()
    await h.settle(0.5)
    kinds = [type(e).__name__ for e in h.events if not isinstance(e, tuple)]
    for needed in ("SpeechStarted", "SpeechStopped", "TranscriptFinal", "AssistantTextDelta",
                   "AssistantAudioStart", "AssistantAudioEnd", "AssistantTextDone"):
        assert needed in kinds, (needed, kinds)
    assert h.index(SpeechStarted) < h.index(SpeechStopped) < h.index(TranscriptFinal)
    assert h.index(TranscriptFinal) < h.index(AssistantTextDelta) < h.index(AssistantAudioStart)
    assert h.index(AssistantAudioStart) < h.index(AssistantAudioEnd) < h.index(AssistantTextDone)
    assert h.of(TranscriptFinal)[0].text == "hello assistant"
    assert h.runner.calls == ["hello assistant"]
    states = [e.state for e in h.of(StateChanged)]
    assert states[:4] == [VoiceState.IDLE, VoiceState.LISTENING, VoiceState.THINKING, VoiceState.SPEAKING]
    assert states[-1] == VoiceState.IDLE
    assert sum(len(a) for a in h.audio) > 0
    assert h.of(AssistantTextDone)[0].text == "Sure thing. Here is the answer. Anything else?"


async def test_partial_transcripts_stream_while_the_user_is_still_talking(h):
    h.stt = h.session.stt = FakeSTT(["one two three four five six"], partial_every_bytes=3200)
    await h.feed(loud(800))                      # still talking: no end-of-speech yet
    await h.settle(0.1)
    partials = h.of(TranscriptPartial)
    assert partials and partials[0].text.startswith("one")
    assert not h.of(TranscriptFinal)
    await h.feed(quiet(300))
    await h.settle(0.2)
    assert h.of(TranscriptFinal)[0].text == "one two three four five six"


async def test_reply_audio_starts_before_the_llm_finishes_generating():
    runner = ScriptedRunner(chunks=("First sentence is here. ", "Second sentence is here. ", "Third one."), delay=0.15)
    harness = Harness(runner=runner)
    await harness.session.start()
    await harness.say()
    await harness.settle(1.0)
    first_audio = harness.index(AssistantAudioStart)
    # at the moment audio started, the runner had NOT finished producing text
    deltas_before_audio = sum(isinstance(e, AssistantTextDelta) for e in harness.events[:first_audio])
    assert 1 <= deltas_before_audio < 3
    assert len(harness.tts.calls) >= 2           # sentence-by-sentence synthesis
    await harness.session.close()


async def test_barge_in_cancels_llm_tts_and_tells_client_to_flush():
    runner = ScriptedRunner(chunks=("This is a long answer. ",) * 30, delay=0.05)
    tts = FakeTTS(ms_per_char=40, chunk_ms=40, chunk_delay=0.01)
    harness = Harness(runner=runner, tts=tts, stt=FakeSTT(["tell me a story", "stop and do this instead"]))
    await harness.session.start()
    await harness.say()
    # wait until the assistant is actually speaking
    for _ in range(100):
        await asyncio.sleep(0.01)
        if harness.session.state == VoiceState.SPEAKING:
            break
    assert harness.session.state == VoiceState.SPEAKING
    audio_before = len(harness.audio)

    await harness.feed(loud(500), pace=0.005)    # the user talks over it
    await harness.settle(0.1)

    interrupted = harness.of(AssistantInterrupted)
    assert len(interrupted) == 1 and interrupted[0].reason == InterruptReason.BARGE_IN
    assert interrupted[0].spoken_text.startswith("This is a long answer.")
    assert runner.cancelled == 1                                       # LLM stream cancelled
    assert tts.cancelled, "TTS synthesis must be abandoned, not drained"
    assert runner.interrupted and runner.interrupted[0].startswith("This is")  # history keeps only what was heard
    cut = harness.index(AssistantInterrupted)
    audio_after_cut = [e for e in harness.events[cut + 1:] if isinstance(e, tuple)]
    assert audio_after_cut == []                                       # nothing sent after the interrupt event
    assert len(harness.audio) >= audio_before

    # the barge-in speech becomes the NEXT turn (no audio was lost)
    runner.chunks = ["Okay, doing that."]
    await harness.feed(quiet(300))
    await harness.settle(0.4)
    assert runner.calls[-1] == "stop and do this instead"
    await harness.session.close()


async def test_short_noise_while_speaking_is_treated_as_echo_not_a_command():
    runner = ScriptedRunner(chunks=("A reasonably long reply here. ",) * 6, delay=0.05)
    tts = FakeTTS(ms_per_char=30, chunk_ms=40, chunk_delay=0.01)
    harness = Harness(runner=runner, tts=tts, stt=FakeSTT(["hello", "this is just echo of the assistant"]))
    await harness.session.start()
    await harness.say()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if harness.session.state == VoiceState.SPEAKING:
            break
    await harness.feed(loud(80))                 # > min_speech (60ms) but < barge-in (100ms)
    await harness.feed(quiet(300))
    for _ in range(100):                          # wait for the reply to finish naturally
        await asyncio.sleep(0.05)
        if harness.of(AssistantTextDone):
            break
    assert not harness.of(AssistantInterrupted)
    assert runner.calls == ["hello"]             # the echo was NOT forwarded as a new request
    assert harness.of(AssistantTextDone)
    await harness.session.close()


async def test_manual_cancel_and_typed_input(h):
    h.runner.hang = True
    await h.session.on_message(InputText(text="what's the weather"))
    await h.settle(0.1)
    assert h.session.state == VoiceState.THINKING and h.runner.calls == ["what's the weather"]
    await h.session.on_message(ResponseCancel())
    await h.settle(0.05)
    assert h.of(AssistantInterrupted)[0].reason == InterruptReason.CANCEL
    assert h.runner.cancelled == 1 and h.session.state == VoiceState.IDLE
    h.runner.hang = False
    await h.session.on_message(InputText(text="try again"))
    await h.settle(0.5)
    assert h.of(AssistantTextDone)


async def test_new_request_while_thinking_replaces_the_old_one(h):
    h.runner.hang = True
    await h.session.on_message(InputText(text="first"))
    await h.settle(0.05)
    h.runner.hang = False
    await h.session.on_message(InputText(text="second"))
    await h.settle(0.5)
    assert h.runner.calls == ["first", "second"] and h.runner.cancelled == 1
    assert h.of(AssistantInterrupted)[0].reason == InterruptReason.NEW_INPUT
    assert len(h.of(AssistantTextDone)) == 1


async def test_silence_or_unintelligible_audio_starts_no_turn():
    harness = Harness(stt=FakeSTT([""]))
    await harness.session.start()
    await harness.say()
    await harness.settle(0.2)
    assert harness.runner.calls == [] and harness.session.state == VoiceState.IDLE
    assert not harness.of(TranscriptFinal)
    await harness.session.close()


async def test_tts_failure_does_not_break_the_text_reply():
    class BrokenTTS(FakeTTS):
        async def synthesize(self, text, *, voice=None, language=None):
            raise RuntimeError("tts down")
            yield b""
    harness = Harness(tts=BrokenTTS())
    await harness.session.start()
    await harness.say()
    await harness.settle(0.4)
    assert any(e.code == "tts_failed" for e in harness.of(ErrorEvent))
    assert harness.of(AssistantTextDone) and not harness.of(AssistantAudioStart)
    await harness.session.close()


async def test_oversized_frame_is_dropped_and_ping_pong(h):
    await h.session.on_audio(b"\x00" * 70000)
    assert h.of(ErrorEvent)[0].code == "frame_too_large"
    await h.session.on_message(Ping(id="x"))
    assert h.of(Pong)[0].id == "x"


async def test_idle_timeout_ends_the_session():
    harness = Harness(config=cfg(limits=VoiceLimits(idle_timeout_seconds=0.3)))
    await harness.session.start()
    await asyncio.sleep(1.4)
    assert [e.reason for e in harness.of(SessionEnded)] == ["idle_timeout"]


async def test_close_cancels_everything_and_leaks_no_tasks():
    runner = ScriptedRunner(chunks=("long reply. ",) * 50, delay=0.05)
    harness = Harness(runner=runner, tts=FakeTTS(chunk_delay=0.01))
    await harness.session.start()
    before = {t for t in asyncio.all_tasks() if not t.done()}
    await harness.say()
    await harness.settle(0.2)
    await harness.session.close("client_ended")
    await asyncio.sleep(0.05)
    leaked = {t for t in asyncio.all_tasks() if not t.done()} - before - {asyncio.current_task()}
    assert not leaked, leaked
    assert runner.cancelled == 1 and harness.of(SessionEnded)[0].reason == "client_ended"
