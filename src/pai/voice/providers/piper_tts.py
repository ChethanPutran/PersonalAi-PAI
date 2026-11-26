"""Free, offline neural text-to-speech with Piper. CPU only, natural-sounding voices.

Piper synthesizes sentence by sentence; each sentence is handed to the event loop as soon as
it exists, so the first audio arrives long before the whole text is done. Synthesis runs in
a worker thread; cancelling the consumer stops it at the next sentence boundary.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncIterator, Optional

from loguru import logger

from pai.voice.audio import iter_chunks, resample
from pai.voice.providers.errors import VoiceConfigurationError, VoiceProviderError

_CHUNK_MS = 60


class PiperTTS:
    name = "piper"

    def __init__(self, voice_path: Path, *, sample_rate: int = 24000, workers: int = 2) -> None:
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise VoiceConfigurationError(
                "Piper is not installed. Install the free local voice extras: "
                "pip install 'ai-personal-agent[voice-local]' (or: pip install piper-tts)") from exc
        self.sample_rate = sample_rate
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="piper")
        # Loading the model takes seconds (20 s on a busy machine) and the first synthesis is cold (~3 s,
        # then ~0.4 s): do both in the background so the server starts at once and the first spoken reply
        # is already warm. synthesize() waits for it if a request arrives earlier.
        self._voice = None
        self._load_error: Optional[BaseException] = None
        self._ready = threading.Event()
        threading.Thread(target=self._load_and_warm, args=(PiperVoice, voice_path), daemon=True,
                         name="piper-load").start()

    def _load_and_warm(self, piper_voice_cls, voice_path: Path) -> None:
        try:
            logger.info("Loading Piper voice from {}", voice_path)
            self._voice = piper_voice_cls.load(str(voice_path))
            for _ in self._voice.synthesize("Hello."):          # warm the runtime
                pass
            logger.info("Piper voice ready")
        except BaseException as exc:                           # reported on first use
            self._load_error = exc
            logger.warning("Piper voice failed to load: {}", exc)
        finally:
            self._ready.set()

    async def synthesize(
        self, text: str, *, voice: Optional[str] = None, language: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        text = text.strip()
        if not text:
            return
        loop = asyncio.get_running_loop()
        if not self._ready.is_set():
            await asyncio.to_thread(self._ready.wait)
        if self._voice is None:
            raise VoiceProviderError(f"Piper voice is not available: {self._load_error}")
        queue: "asyncio.Queue[object]" = asyncio.Queue(maxsize=8)     # backpressure on the worker
        stop = threading.Event()
        END = object()

        def work() -> None:
            try:
                for chunk in self._voice.synthesize(text):
                    if stop.is_set():
                        return
                    pcm = resample(chunk.audio_int16_bytes, chunk.sample_rate, self.sample_rate)
                    fut = asyncio.run_coroutine_threadsafe(queue.put(pcm), loop)
                    while not stop.is_set():
                        try:
                            fut.result(timeout=0.2)
                            break
                        except TimeoutError:
                            continue
            except Exception as exc:                                    # reported to the consumer
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop)
            finally:
                if not stop.is_set():
                    asyncio.run_coroutine_threadsafe(queue.put(END), loop)

        job = loop.run_in_executor(self._pool, work)
        step = self.sample_rate * _CHUNK_MS // 1000 * 2
        try:
            while True:
                item = await queue.get()
                if item is END:
                    return
                if isinstance(item, Exception):
                    raise VoiceProviderError(f"Piper synthesis failed: {item}") from item
                for piece in iter_chunks(item, step):                   # type: ignore[arg-type]
                    yield piece
        finally:
            stop.set()                                                  # consumer left: stop the worker
            job.cancel()

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
