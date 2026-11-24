"""Offline TTS fallback using the ``espeak`` binary (robotic, but needs no network or key)."""
from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import struct
from typing import AsyncIterator, Optional

from pai.voice.audio import frame_bytes, resample
from pai.voice.providers.errors import VoiceProviderError

_MAX_INPUT_CHARS = 2000
# espeak voice names look like "en", "en-us", "de", "en+f3"; OpenAI names ("alloy") do not.
_VOICE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{1,8})?(\+[a-z0-9]{1,4})?$")
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{1,8})?$")


class _WavStream:
    """Incremental parser for a *streamed* WAV (the RIFF/data sizes may be 0 or 0x7ffff000).

    ``feed()`` returns PCM bytes as soon as the ``data`` chunk starts; the sample rate is
    available as ``rate`` after the header was seen.
    """

    def __init__(self) -> None:
        self._buf = b""
        self._in_data = False
        self.rate: Optional[int] = None
        self.channels = 1
        self.bits = 16

    def feed(self, data: bytes) -> bytes:
        if self._in_data:
            return data
        self._buf += data
        buf = self._buf
        if len(buf) < 12:
            return b""
        if buf[:4] != b"RIFF" or buf[8:12] != b"WAVE":
            raise VoiceProviderError("espeak did not produce a WAV stream")
        pos = 12
        while True:
            if len(buf) < pos + 8:
                return b""                       # need more header bytes
            cid, size = buf[pos:pos + 4], struct.unpack("<I", buf[pos + 4:pos + 8])[0]
            body = pos + 8
            if cid == b"data":
                self._in_data = True
                self._buf = b""
                return buf[body:]
            if len(buf) < body + size:
                return b""
            if cid == b"fmt ":
                _fmt, self.channels, self.rate, _br, _ba, self.bits = struct.unpack(
                    "<HHIIHH", buf[body:body + 16])
            pos = body + size + (size & 1)


def _espeak_voice(voice: Optional[str], language: Optional[str], default: str) -> str:
    if voice and _VOICE_RE.match(voice.lower()):
        return voice.lower()
    if language and _LANG_RE.match(language):
        return language.lower().replace("_", "-")
    return default


class EspeakTTS:
    name = "espeak"

    def __init__(
        self,
        *,
        sample_rate: int = 24000,
        default_voice: str = "en",
        speed_wpm: int = 165,
        chunk_ms: int = 60,
        binary: str = "espeak",
    ) -> None:
        self.sample_rate = sample_rate
        self._default_voice = default_voice
        self._speed = speed_wpm
        self._binary = binary
        self._chunk_ms = chunk_ms

    @staticmethod
    def available(binary: str = "espeak") -> bool:
        return shutil.which(binary) is not None

    async def synthesize(
        self, text: str, *, voice: Optional[str] = None, language: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        text = (text or "").strip()[:_MAX_INPUT_CHARS]
        if not text:
            return
        exe = shutil.which(self._binary)
        if exe is None:
            raise VoiceProviderError(f"'{self._binary}' is not installed")
        voice_name = _espeak_voice(voice, language, self._default_voice)
        # Text goes via stdin (never argv): no option injection from text starting with '-'.
        proc = await asyncio.create_subprocess_exec(
            exe, "--stdout", "-v", voice_name, "-s", str(self._speed),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert proc.stdin and proc.stdout
            proc.stdin.write(text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            wav = _WavStream()
            pending = b""
            out_chunk = None
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                pcm = wav.feed(data)
                if not pcm:
                    continue
                if wav.bits != 16 or wav.channels != 1 or not wav.rate:
                    raise VoiceProviderError(
                        f"unsupported espeak output ({wav.channels}ch/{wav.bits}bit)")
                if out_chunk is None:
                    out_chunk = frame_bytes(wav.rate, self._chunk_ms)    # source-rate bytes
                pending += pcm
                while len(pending) >= out_chunk:
                    piece, pending = pending[:out_chunk], pending[out_chunk:]
                    yield resample(piece, wav.rate, self.sample_rate)
            pending = pending[: len(pending) - (len(pending) % 2)]
            if pending and wav.rate:
                yield resample(pending, wav.rate, self.sample_rate)
            if await proc.wait() != 0:
                raise VoiceProviderError("espeak exited with an error")
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.shield(proc.wait())

    async def aclose(self) -> None:
        return None
