"""Energy-based voice activity detection with an adaptive noise floor.

Deliberately dependency-free. It decides *when the user starts and stops talking* (the
turn boundary) — transcription is the STT provider's job. If webrtcvad/silero are wanted
later, implement the same small interface (``process`` -> ``VadEvent`` list).
"""
from __future__ import annotations

from collections import deque
from enum import StrEnum
from typing import Deque, List

from pydantic import BaseModel

from pai.config.models.voice import VADConfig
from pai.voice.audio import frame_bytes, rms


class VadEventKind(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


class VadEvent(BaseModel):
    kind: VadEventKind
    pre_roll: bytes = b""          # SPEECH_START: audio from just before the detection
    speech_ms: float = 0.0         # SPEECH_END: voiced duration


class EnergyVAD:
    def __init__(self, config: VADConfig, sample_rate: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self._frame = frame_bytes(sample_rate, config.frame_ms)
        self._carry = b""
        self._noise = config.energy_threshold / config.noise_margin    # initial floor guess
        self._pre: Deque[bytes] = deque(maxlen=max(1, config.pre_roll_ms // config.frame_ms))
        self._speaking = False
        self._voiced_ms = 0.0          # sustained voiced time before the start fires
        self._speech_ms = 0.0          # wall time since start (voiced + short pauses)
        self._voiced_total_ms = 0.0    # time that was actually voiced since start
        self._silence_ms = 0.0

    # ----------------------------------------------------------------- state
    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def speech_duration_ms(self) -> float:
        """Time since this utterance's speech started (0 when not speaking)."""
        return self._speech_ms if self._speaking else 0.0

    @property
    def voiced_duration_ms(self) -> float:
        """Time that was actually VOICED in this utterance (trailing silence excluded).
        This is what barge-in uses, so a short noise followed by silence never counts."""
        return self._voiced_total_ms if self._speaking else 0.0

    def reset(self) -> None:
        self._carry, self._speaking = b"", False
        self._voiced_ms = self._speech_ms = self._silence_ms = self._voiced_total_ms = 0.0
        self._pre.clear()

    @property
    def threshold(self) -> float:
        return max(self.config.energy_threshold, self._noise * self.config.noise_margin)

    # --------------------------------------------------------------- process
    def process(self, pcm: bytes) -> List[VadEvent]:
        data = self._carry + pcm
        n = len(data) // self._frame * self._frame
        self._carry = data[n:]
        events: List[VadEvent] = []
        for i in range(0, n, self._frame):
            ev = self._frame_step(data[i : i + self._frame])
            if ev is not None:
                events.append(ev)
        return events

    def _frame_step(self, frame: bytes) -> "VadEvent | None":
        cfg = self.config
        level = rms(frame)
        voiced = level >= self.threshold

        if not self._speaking:
            if not voiced:
                self._noise += 0.05 * (level - self._noise)      # track the room only while quiet
                self._voiced_ms = 0.0
                self._pre.append(frame)
                return None
            self._voiced_ms += cfg.frame_ms
            self._pre.append(frame)
            if self._voiced_ms >= cfg.min_speech_ms:
                self._speaking = True
                self._speech_ms = self._voiced_total_ms = self._voiced_ms
                self._silence_ms = 0.0
                pre = b"".join(self._pre)
                self._pre.clear()
                return VadEvent(kind=VadEventKind.SPEECH_START, pre_roll=pre)
            return None

        # speaking
        self._speech_ms += cfg.frame_ms
        if voiced:
            self._voiced_total_ms += cfg.frame_ms
        if voiced or level >= 0.6 * self.threshold:               # hysteresis: dips are not silence
            self._silence_ms = 0.0
            return None
        self._silence_ms += cfg.frame_ms
        if self._silence_ms >= cfg.end_silence_ms:
            ms = self._speech_ms - self._silence_ms
            self._speaking = False
            self._voiced_ms = self._speech_ms = self._silence_ms = self._voiced_total_ms = 0.0
            return VadEvent(kind=VadEventKind.SPEECH_END, speech_ms=max(ms, 0.0))
        return None
