"""One realtime voice conversation (transport-agnostic).

            mic PCM ─► VAD ─► STT stream ─► transcript ─► TurnRunner (orchestrator, streamed)
                                                              │ text deltas
            speaker ◄─ PCM ◄─ TTS ◄─ SpeechChunker (sentences) ◄┘

Everything is streaming: audio is fed to STT while the user talks (partials), the reply is
spoken sentence by sentence while the LLM is still generating, and the user can interrupt
(barge-in) at any moment — the in-flight LLM stream, TTS and upstream connections are
cancelled immediately and the client is told to drop its queued audio.

The session talks to the outside world only through ``send_event`` / ``send_audio``
callbacks, so it is tested without sockets and reused by any transport.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Awaitable, Callable, List, Optional, Protocol

from loguru import logger
from pydantic import BaseModel

from pai.config.models.voice import VoiceConfig
from pai.observability import get_tracer
from pai.orchestration.streaming import ReplySink
from pai.voice import usage as voice_usage
from pai.voice.audio import pcm_duration_ms
from pai.voice.chunker import SpeechChunker, speakable
from pai.voice.models import (
    AssistantAudioEnd, AssistantAudioStart, AssistantInterrupted, AssistantStatus,
    AssistantTextDelta, AssistantTextDone, ClientMessage, ErrorEvent, InputText,
    InterruptReason, Ping, Pong, ResponseCancel, ServerEvent, SessionEnd, SessionEnded,
    SessionUpdate, SpeechStarted, SpeechStopped, StateChanged, TranscriptFinal,
    TranscriptPartial, VoiceState,
)
from pai.voice.protocols import STTProvider, STTStream, TTSProvider
from pai.voice.vad import EnergyVAD, VadEventKind

SendEvent = Callable[[ServerEvent], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]
StatusCallback = Callable[[str, str], Awaitable[None]]


class TurnOutcome(BaseModel):
    reply: str = ""
    status: str = "completed"


class TurnRunner(Protocol):
    """Runs one user turn through the assistant, streaming the reply into ``sink``."""

    async def run_turn(
        self, *, user_id: str, session_id: str, device_id: Optional[str], text: str,
        turn_id: str, sink: ReplySink, on_status: StatusCallback,
    ) -> TurnOutcome: ...

    async def on_interrupted(
        self, *, user_id: str, session_id: str, turn_id: str, spoken_text: str,
    ) -> None:
        """The reply was cut off: keep only what the user actually heard."""


class _Turn:
    def __init__(self, text: str) -> None:
        self.turn_id = uuid.uuid4().hex[:12]
        self.response_id = uuid.uuid4().hex[:12]
        self.text = text
        self.task: Optional[asyncio.Task] = None
        self.spoken: List[str] = []          # segments whose audio has started going out
        self.audio_started = False
        self.finished = False
        self.speech_ended_at: Optional[float] = None    # when the user stopped talking (VAD)
        self.stt_ms: Optional[float] = None             # end of speech -> final transcript


class _Utterance:
    def __init__(self, turn_id: str, stream: STTStream, started_in: VoiceState) -> None:
        self.turn_id = turn_id
        self.stream = stream
        self.started_in = started_in          # state when the user began (SPEAKING => maybe echo)
        self.barged = False
        self.started_at = time.time()        # for the STT span in the turn's trace
        self.ended_at: Optional[float] = None  # end of speech: where the user-felt latency starts
        self.partials_task: Optional[asyncio.Task] = None


class _SegAudio:
    """One speakable segment being synthesized: its PCM chunks arrive on ``chunks`` (None = done)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.chunks: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        self.failed = False
        self.task: Optional[asyncio.Task] = None


class _TurnSink:
    """ReplySink for one turn: text -> client captions + speakable segments for the TTS."""

    def __init__(self, session: "VoiceSession", turn: _Turn, segments: "asyncio.Queue[Optional[str]]") -> None:
        self._s, self._turn, self._segments = session, turn, segments
        self._chunker = SpeechChunker(first_chunk_min_chars=session.config.first_chunk_min_chars)
        self.text_parts: List[str] = []

    async def on_delta(self, text: str) -> None:
        if not text:
            return
        self.text_parts.append(text)
        await self._s._emit(AssistantTextDelta(
            turn_id=self._turn.turn_id, response_id=self._turn.response_id, delta=text))
        for seg in self._chunker.feed(text):
            self._segments.put_nowait(seg)

    async def on_done(self, text: str) -> None:
        for seg in self._chunker.flush():
            self._segments.put_nowait(seg)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


class VoiceSession:
    def __init__(
        self,
        *,
        config: VoiceConfig,
        stt: STTProvider,
        tts: TTSProvider,
        runner: TurnRunner,
        user_id: str,
        chat_session_id: str,
        device_id: Optional[str],
        send_event: SendEvent,
        send_audio: SendAudio,
        input_sample_rate: Optional[int] = None,
        language: Optional[str] = None,
        voice: Optional[str] = None,
        on_close: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.config = config
        self.stt, self.tts, self.runner = stt, tts, runner
        self.user_id, self.chat_session_id, self.device_id = user_id, chat_session_id, device_id
        self._send_event, self._send_audio, self._on_close = send_event, send_audio, on_close
        self.input_rate = input_sample_rate or config.input_sample_rate
        self.language = language or config.language
        self.voice = voice or config.voice

        self.vad = EnergyVAD(config.vad, self.input_rate)
        self.state = VoiceState.IDLE
        self._turn: Optional[_Turn] = None
        self._utt: Optional[_Utterance] = None
        self._bg: set[asyncio.Task] = set()
        self._closed = False
        self._started_at = time.monotonic()
        self._last_audio_at = time.monotonic()
        self._watchdog: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._watchdog = asyncio.create_task(self._watch(), name="voice.watchdog")
        await self._emit(StateChanged(state=self.state))      # tell the client where we start

    async def close(self, reason: str = "closed") -> None:
        if self._closed:
            return
        self._closed = True
        if self._watchdog is not None and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
        await self._cancel_turn()
        await self._drop_utterance()
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self._send_event(SessionEnded(reason=reason))
        if self._on_close:
            with contextlib.suppress(Exception):
                await self._on_close(reason)

    async def _watch(self) -> None:
        lim = self.config.limits
        while not self._closed:
            await asyncio.sleep(min(1.0, lim.idle_timeout_seconds / 4))
            now = time.monotonic()
            if now - self._started_at > lim.max_session_seconds:
                return await self.close("max_session_duration")
            if now - self._last_audio_at > lim.idle_timeout_seconds:
                return await self.close("idle_timeout")

    # -------------------------------------------------------------------- inbound

    async def on_audio(self, pcm: bytes) -> None:
        """A frame of microphone audio (PCM16LE mono at ``input_rate``)."""
        if self._closed or not pcm:
            return
        if len(pcm) > self.config.limits.max_frame_bytes:
            await self._emit(ErrorEvent(code="frame_too_large", message="audio frame too large; dropped"))
            return
        self._last_audio_at = time.monotonic()

        for ev in self.vad.process(pcm):
            if ev.kind == VadEventKind.SPEECH_START:
                await self._begin_utterance(ev.pre_roll)
            elif ev.kind == VadEventKind.SPEECH_END:
                await self._end_utterance()

        utt = self._utt
        if utt is not None and self.vad.is_speaking:
            await utt.stream.push(pcm)
            await self._maybe_barge_in(utt)
            if self.vad.speech_duration_ms >= self.config.limits.max_utterance_seconds * 1000:
                await self._end_utterance(forced=True)

    async def on_message(self, msg: ClientMessage) -> None:
        if self._closed:
            return
        if isinstance(msg, Ping):
            await self._emit(Pong(id=msg.id))
        elif isinstance(msg, ResponseCancel):
            await self.interrupt(InterruptReason.CANCEL)
        elif isinstance(msg, SessionUpdate):
            self.language = msg.language or self.language
            self.voice = msg.voice or self.voice
        elif isinstance(msg, InputText):
            await self._start_turn(msg.text.strip(), reason=InterruptReason.NEW_INPUT)
        elif isinstance(msg, SessionEnd):
            await self.close("client_ended")

    # ----------------------------------------------------------- utterance (user)

    async def _begin_utterance(self, pre_roll: bytes) -> None:
        if self._utt is not None:
            return
        try:
            stream = await self.stt.open(sample_rate=self.input_rate, language=self.language)
        except Exception as exc:
            logger.warning("STT open failed: {}", exc)
            await self._emit(ErrorEvent(code="stt_unavailable", message="speech recognition is unavailable"))
            return
        turn_id = uuid.uuid4().hex[:12]
        self._utt = _Utterance(turn_id, stream, started_in=self.state)
        if pre_roll:
            await stream.push(pre_roll)
        self._utt.partials_task = self._spawn(self._forward_partials(self._utt), "voice.partials")
        if self.state in (VoiceState.IDLE, VoiceState.LISTENING):
            await self._set_state(VoiceState.LISTENING, turn_id)
        await self._emit(SpeechStarted(turn_id=turn_id))

    async def _forward_partials(self, utt: _Utterance) -> None:
        last = ""
        try:
            async for ev in utt.stream.partials():
                if ev.text and ev.text != last and self._utt is utt:
                    last = ev.text
                    await self._emit(TranscriptPartial(turn_id=utt.turn_id, text=ev.text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("partials ended: {}", exc)

    async def _maybe_barge_in(self, utt: _Utterance) -> None:
        if (not self.config.barge_in or utt.barged
                or self.state not in (VoiceState.SPEAKING, VoiceState.THINKING)
                or self.vad.voiced_duration_ms < self.config.barge_in_min_speech_ms):
            return
        utt.barged = True
        await self.interrupt(InterruptReason.BARGE_IN)

    async def _end_utterance(self, forced: bool = False) -> None:
        utt, self._utt = self._utt, None
        if utt is None:
            return
        if forced:
            self.vad.reset()
        utt.ended_at = time.time()
        await self._emit(SpeechStopped(turn_id=utt.turn_id))
        # Finalize off the audio path so the next frames are not blocked behind the STT.
        self._spawn(self._finalize(utt), "voice.finalize")

    async def _finalize(self, utt: _Utterance) -> None:
        stt_usage: dict = {}
        try:
            with voice_usage.collect() as used:
                text = (await utt.stream.finish()).strip()
            stt_usage = voice_usage.summarize(used)
        except Exception as exc:
            logger.warning("STT finish failed: {}", exc)
            text = ""
        finally:
            with contextlib.suppress(Exception):
                await utt.stream.aclose()
            if utt.partials_task:
                utt.partials_task.cancel()
        # Speech picked up while the assistant was talking that never grew into a real
        # barge-in is almost always the assistant's own voice leaking in: ignore it.
        echo = utt.started_in == VoiceState.SPEAKING and not utt.barged
        if self._closed or not text or len(text) < 2 or echo:
            if self.state == VoiceState.LISTENING:
                await self._set_state(VoiceState.IDLE)
            return
        stt_ms = None if utt.ended_at is None else (time.time() - utt.ended_at) * 1000
        # `start` is when the user began talking, so this span includes the speech itself: the time
        # the user waits for the transcript is `finalize_ms` (end of speech -> final text).
        self._trace_span(utt.turn_id, "voice.stt", start=utt.started_at, provider=getattr(self.stt, "name", type(self.stt).__name__),
                         chars=len(text), finalize_ms=None if stt_ms is None else round(stt_ms), **stt_usage)
        await self._emit(TranscriptFinal(turn_id=utt.turn_id, text=text))
        await self._start_turn(text, reason=InterruptReason.NEW_INPUT, turn_id=utt.turn_id,
                               speech_ended_at=utt.ended_at, stt_ms=stt_ms)

    async def _drop_utterance(self) -> None:
        utt, self._utt = self._utt, None
        if utt is None:
            return
        if utt.partials_task:
            utt.partials_task.cancel()
        with contextlib.suppress(Exception):
            await utt.stream.aclose()

    # ------------------------------------------------------------- turn (assistant)

    async def _start_turn(self, text: str, *, reason: InterruptReason, turn_id: Optional[str] = None,
                          speech_ended_at: Optional[float] = None, stt_ms: Optional[float] = None) -> None:
        if not text:
            return
        await self.interrupt(reason)                     # a new request replaces a running one
        turn = _Turn(text)
        turn.speech_ended_at, turn.stt_ms = speech_ended_at, stt_ms
        if turn_id:
            turn.turn_id = turn_id
        self._turn = turn
        await self._set_state(VoiceState.THINKING, turn.turn_id)
        turn.task = asyncio.create_task(self._run_turn(turn), name=f"voice.turn.{turn.turn_id}")

    async def _run_turn(self, turn: _Turn) -> None:
        segments: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        sink = _TurnSink(self, turn, segments)
        speaker = asyncio.create_task(self._speak(turn, segments), name="voice.speak")
        filler = self._spawn(self._filler(turn, segments), "voice.filler") if self.config.thinking_filler else None

        async def on_status(stage: str, message: str) -> None:
            await self._emit(AssistantStatus(turn_id=turn.turn_id, stage=stage, message=message))

        try:
            outcome = await self.runner.run_turn(
                user_id=self.user_id, session_id=self.chat_session_id, device_id=self.device_id,
                text=turn.text, turn_id=turn.turn_id, sink=sink, on_status=on_status)
            if not sink.text and outcome.reply:          # nothing streamed (command, error...): speak it
                await sink.on_delta(outcome.reply)
            await sink.on_done(sink.text)
            segments.put_nowait(None)
            await speaker
            turn.finished = True
            await self._emit(AssistantTextDone(
                turn_id=turn.turn_id, response_id=turn.response_id, text=sink.text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("voice turn failed: {}", exc)
            segments.put_nowait(None)
            await self._emit(ErrorEvent(code="turn_failed", message="something went wrong with that request"))
        finally:
            for t in (speaker, filler):
                if t is not None and not t.done():
                    t.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            if self._turn is turn:
                self._turn = None
                if not self._closed:
                    await self._set_state(VoiceState.IDLE)

    async def _filler(self, turn: _Turn, segments: "asyncio.Queue[Optional[str]]") -> None:
        await asyncio.sleep(self.config.thinking_filler_after_seconds)
        if not turn.audio_started and not turn.finished:
            segments.put_nowait(self.config.thinking_filler)

    def _trace_span(self, turn_id: str, name: str, *, start: float, error: Optional[str] = None, **attributes: object) -> None:
        """Record a voice step (STT / TTS) in the turn's trace (its id is the turn id). Never breaks the call."""
        try:
            get_tracer().record_span(turn_id, name, start=start, attributes={"user_id": self.user_id, **attributes}, error=error)
        except Exception as exc:
            logger.debug("voice trace span failed: {}", exc)

    async def _speak(self, turn: _Turn, segments: "asyncio.Queue[Optional[str]]") -> None:
        """Turn segments into audio, in order, with the NEXT segments already being synthesized.

        A remote TTS answers one request per segment in seconds. Synthesizing a segment only after the
        previous one was sent leaves an audible gap at every sentence; here up to ``tts_prefetch``
        segments are synthesized at once while playback stays strictly in order."""
        started = False
        order: "asyncio.Queue[Optional[_SegAudio]]" = asyncio.Queue()     # playback order
        gate = asyncio.Semaphore(max(1, self.config.tts_prefetch))        # syntheses in flight (FIFO)
        synth_tasks: List[asyncio.Task] = []

        async def synth(seg: _SegAudio) -> None:
            async with gate:
                t0 = time.time(); nbytes = 0; first_ms: Optional[float] = None; err: Optional[str] = None
                used_cm = voice_usage.collect(); used = used_cm.__enter__()
                try:
                    # aclosing: cancelling between chunks must still close the upstream stream
                    async with contextlib.aclosing(
                        self.tts.synthesize(seg.text, voice=self.voice, language=self.language)
                    ) as audio:
                        async for pcm in audio:
                            if first_ms is None:
                                first_ms = (time.time() - t0) * 1000
                            nbytes += len(pcm)
                            seg.chunks.put_nowait(pcm)
                except asyncio.CancelledError:
                    err = "interrupted (barge-in or cancel)"
                    raise
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    seg.failed = True
                    logger.warning("TTS failed for a segment: {}", exc)
                finally:
                    used_cm.__exit__(None, None, None)
                    seg.chunks.put_nowait(None)
                    self._trace_span(turn.turn_id, "voice.tts", start=t0, error=err,
                                     provider=getattr(self.tts, "name", type(self.tts).__name__), chars=len(seg.text),
                                     audio_ms=round(pcm_duration_ms(nbytes, self.tts.sample_rate)),
                                     first_audio_ms=None if first_ms is None else round(first_ms),
                                     **voice_usage.summarize(used))

        async def feed() -> None:
            try:
                while True:
                    seg = await segments.get()
                    if seg is None:
                        break
                    text = speakable(seg)
                    if not text:
                        continue
                    item = _SegAudio(text)
                    item.task = asyncio.create_task(synth(item), name="voice.tts.synth")
                    synth_tasks.append(item.task)
                    order.put_nowait(item)
            finally:
                order.put_nowait(None)

        feeder = asyncio.create_task(feed(), name="voice.tts.feed")
        try:
            while True:
                item = await order.get()
                if item is None:
                    break
                while True:
                    pcm = await item.chunks.get()
                    if pcm is None:
                        break
                    if not started:
                        started = turn.audio_started = True
                        await self._emit(AssistantAudioStart(
                            response_id=turn.response_id, sample_rate=self.tts.sample_rate))
                        await self._set_state(VoiceState.SPEAKING, turn.turn_id)
                        if turn.speech_ended_at is not None:        # what the user actually waited
                            self._trace_span(turn.turn_id, "voice.latency", start=turn.speech_ended_at,
                                             stt_finalize_ms=None if turn.stt_ms is None else round(turn.stt_ms),
                                             first_audio_after_speech_end_ms=round((time.time() - turn.speech_ended_at) * 1000))
                    if item.text not in turn.spoken:
                        turn.spoken.append(item.text)
                    await self._send_audio(pcm)
                if item.failed:
                    await self._emit(ErrorEvent(code="tts_failed", message="could not speak part of the reply"))
        finally:
            feeder.cancel()
            for t in synth_tasks:                  # barge-in / cancel: stop segments still being synthesized
                if not t.done():
                    t.cancel()
            await asyncio.gather(feeder, *synth_tasks, return_exceptions=True)
            if started:
                with contextlib.suppress(Exception):
                    await self._emit(AssistantAudioEnd(response_id=turn.response_id))

    # ---------------------------------------------------------------- interruption

    async def interrupt(self, reason: InterruptReason) -> bool:
        """Stop the current response (if any). Returns True if something was running."""
        turn = self._turn
        if turn is None or turn.finished:
            return False
        spoken = " ".join(turn.spoken)
        await self._cancel_turn()
        await self._emit(AssistantInterrupted(
            response_id=turn.response_id, reason=reason, spoken_text=spoken))
        with contextlib.suppress(Exception):
            await self.runner.on_interrupted(
                user_id=self.user_id, session_id=self.chat_session_id,
                turn_id=turn.turn_id, spoken_text=spoken)
        if not self._closed:
            await self._set_state(VoiceState.LISTENING if self.vad.is_speaking else VoiceState.IDLE)
        return True

    async def _cancel_turn(self) -> None:
        turn, self._turn = self._turn, None
        if turn is None or turn.task is None:
            return
        turn.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await turn.task

    # --------------------------------------------------------------------- helpers

    async def _set_state(self, state: VoiceState, turn_id: Optional[str] = None) -> None:
        if state == self.state and turn_id is None:
            return
        self.state = state
        await self._emit(StateChanged(state=state, turn_id=turn_id))

    async def _emit(self, event: ServerEvent) -> None:
        if self._closed and not isinstance(event, SessionEnded):
            return
        try:
            await self._send_event(event)
        except Exception as exc:
            logger.debug("send_event failed: {}", exc)

    def _spawn(self, coro: Awaitable, name: str) -> asyncio.Task:
        t = asyncio.create_task(coro, name=name)       # type: ignore[arg-type]
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t
