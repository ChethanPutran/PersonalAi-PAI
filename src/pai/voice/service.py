"""Process-wide voice facilities: providers, per-user session limits, availability."""
from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from typing import Optional

from loguru import logger

from pai.config.models.voice import VoiceConfig
from pai.voice.models import Capabilities, Limits, SessionReady
from pai.voice.protocols import STTProvider, TTSProvider
from pai.voice.session import TurnRunner


class VoiceUnavailable(RuntimeError):
    """Voice is disabled or its providers could not be built (no API key...)."""


class VoiceService:
    def __init__(
        self, config: VoiceConfig, *, stt: Optional[STTProvider], tts: Optional[TTSProvider],
        runner: Optional[TurnRunner], unavailable_reason: str = "",
    ) -> None:
        self.config, self.stt, self.tts, self.runner = config, stt, tts, runner
        self._reason = unavailable_reason
        self._active: Counter[str] = Counter()
        self._sessions: set = set()                 # live VoiceSession objects (closed on shutdown)
        self._base_runner = runner                  # kept so a settings reload can rebuild the providers

    @classmethod
    def build(cls, config: VoiceConfig, runner: TurnRunner, cost_tracker=None) -> "VoiceService":
        """Never raises: a deployment without voice keys must still start the server."""
        if not config.enabled:
            svc = cls(config, stt=None, tts=None, runner=None, unavailable_reason="voice is disabled")
        else:
            try:
                from pai.voice.providers.factory import create_stt, create_tts
                svc = cls(config, stt=create_stt(config, cost_tracker=cost_tracker),
                          tts=create_tts(config, cost_tracker=cost_tracker), runner=runner)
            except Exception as exc:
                logger.warning("Voice unavailable: {}", exc)
                svc = cls(config, stt=None, tts=None, runner=None, unavailable_reason=str(exc))
        svc._base_runner = runner
        return svc

    def reload(self, config: VoiceConfig, cost_tracker=None, *, close_after_seconds: float = 120.0) -> None:
        """Apply new voice settings to the RUNNING service (settings reload). Sessions already open keep the providers
        they started with (closed after a grace period); new sessions use the new ones."""
        import asyncio
        fresh = type(self).build(config, self._base_runner, cost_tracker)
        old = [p for p in (self.stt, self.tts) if p is not None]
        self.config, self.stt, self.tts, self.runner, self._reason = fresh.config, fresh.stt, fresh.tts, fresh.runner, fresh._reason
        try:
            loop = asyncio.get_running_loop()
            for p in old:
                loop.call_later(close_after_seconds, lambda p=p: asyncio.ensure_future(p.aclose()))
        except RuntimeError:
            pass

    @property
    def available(self) -> bool:
        return self.stt is not None and self.tts is not None and self.runner is not None

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    @property
    def active_sessions(self) -> int:
        return sum(self._active.values())

    def try_acquire(self, user_id: str) -> bool:
        if self._active[user_id] >= self.config.limits.max_sessions_per_user:
            return False
        self._active[user_id] += 1
        return True

    def register(self, session) -> None:
        self._sessions.add(session)

    def unregister(self, session) -> None:
        self._sessions.discard(session)

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    def release(self, user_id: str) -> None:
        self._active[user_id] -= 1
        if self._active[user_id] <= 0:
            del self._active[user_id]

    def ready_event(self, *, voice_session_id: str, chat_session_id: str, input_rate: int) -> SessionReady:
        assert self.stt is not None and self.tts is not None
        lim = self.config.limits
        return SessionReady(
            voice_session_id=voice_session_id, chat_session_id=chat_session_id,
            input_sample_rate=input_rate, output_sample_rate=self.tts.sample_rate,
            limits=Limits(max_utterance_seconds=lim.max_utterance_seconds,
                          idle_timeout_seconds=lim.idle_timeout_seconds,
                          max_session_seconds=lim.max_session_seconds),
            capabilities=Capabilities(
                stt=self.stt.name, tts=self.tts.name,
                partial_transcripts=self.stt.supports_partials, barge_in=self.config.barge_in),
        )

    async def aclose(self) -> None:
        # End every live conversation first (each client gets `session.ended`), then the engines.
        sessions = list(self._sessions)
        if sessions:
            await asyncio.gather(*(s.close("server_shutdown") for s in sessions), return_exceptions=True)
        self._sessions.clear()
        for p in (self.stt, self.tts):
            if p is not None:
                with contextlib.suppress(Exception):
                    await p.aclose()
