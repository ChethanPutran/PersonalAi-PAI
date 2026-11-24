"""Real espeak (skipped only if the binary is missing) + WAV-stream parser."""
import asyncio
import struct

import pytest

from pai.voice.audio import rms
from pai.voice.providers import espeak_tts
from pai.voice.providers.espeak_tts import EspeakTTS, _espeak_voice, _WavStream

needs_espeak = pytest.mark.skipif(not EspeakTTS.available(), reason="espeak not installed")
CHUNK_24K = 24000 * 60 // 1000 * 2


def wav_header(rate=22050, data_size=0x7FFFF000, riff_size=0x7FFFF024, extra=b""):
    return (b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + extra +
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
            b"data" + struct.pack("<I", data_size))


def test_wav_parser_handles_unknown_sizes_and_byte_by_byte_feeds():
    stream = wav_header(data_size=0xFFFFFFFF, riff_size=0) + b"\x01\x02\x03\x04"
    parser, out = _WavStream(), b""
    for i in range(len(stream)):
        out += parser.feed(stream[i:i + 1])
    assert out == b"\x01\x02\x03\x04" and parser.rate == 22050 and parser.channels == 1 and parser.bits == 16


def test_wav_parser_skips_unknown_chunks_before_data():
    junk = b"LIST" + struct.pack("<I", 5) + b"abcde" + b"\x00"          # odd size -> padded
    parser = _WavStream()
    out = parser.feed(b"RIFF\x00\x00\x00\x00WAVE" + junk +
                      wav_header()[12:] + b"\xaa\xbb")
    assert out == b"\xaa\xbb"


def test_wav_parser_rejects_non_wav():
    from pai.voice.providers.errors import VoiceProviderError
    with pytest.raises(VoiceProviderError):
        _WavStream().feed(b"NOTAWAVEFILE!")


def test_voice_and_language_mapping():
    assert _espeak_voice("en-us", None, "en") == "en-us"
    assert _espeak_voice("alloy", "en", "xx") == "en"                    # OpenAI voice name -> language
    assert _espeak_voice("alloy", "en_US", "xx") == "en-us"
    assert _espeak_voice(None, None, "en") == "en"
    assert _espeak_voice("--evil;rm", "../x", "en") == "en"               # junk never reaches argv


@needs_espeak
async def test_real_speech_is_streamed_as_pcm_chunks():
    tts = EspeakTTS(sample_rate=24000)
    chunks = [c async for c in tts.synthesize("Hello world", voice="en", language=None)]
    assert len(chunks) >= 3
    assert all(len(c) % 2 == 0 for c in chunks)
    assert all(len(c) == CHUNK_24K for c in chunks[:-1])
    audio = b"".join(chunks)
    assert 24000 * 2 * 0.3 < len(audio) < 24000 * 2 * 4                   # 0.3 s .. 4 s of speech
    assert rms(audio) > 0.01                                              # audible, not silence


@needs_espeak
async def test_resamples_to_requested_rate():
    a = b"".join([c async for c in EspeakTTS(sample_rate=24000).synthesize("testing one two")])
    b = b"".join([c async for c in EspeakTTS(sample_rate=16000).synthesize("testing one two")])
    assert abs(len(b) / len(a) - 16000 / 24000) < 0.03


@needs_espeak
async def test_text_starting_with_dash_is_spoken_not_treated_as_option():
    audio = b"".join([c async for c in EspeakTTS().synthesize("--version")])
    assert len(audio) > 2000


@needs_espeak
async def test_cancel_kills_the_process(monkeypatch):
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **kw):
        p = await real(*a, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(espeak_tts.asyncio, "create_subprocess_exec", spy)
    gen = EspeakTTS().synthesize("this is a fairly long sentence " * 40)
    assert await gen.__anext__()
    await gen.aclose()
    assert procs and procs[0].returncode is not None                      # reaped, not left running
    assert procs[0].returncode != 0


async def test_blank_text_and_missing_binary():
    from pai.voice.providers.errors import VoiceProviderError
    assert [c async for c in EspeakTTS().synthesize("  ")] == []
    with pytest.raises(VoiceProviderError, match="not installed"):
        [c async for c in EspeakTTS(binary="definitely-not-a-binary").synthesize("hi")]
