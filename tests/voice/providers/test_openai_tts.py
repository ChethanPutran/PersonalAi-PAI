import asyncio
import contextlib
import json

import httpx
import pytest

from pai.voice.providers.errors import VoiceProviderError
from pai.voice.providers.openai_tts import OpenAITTS

CHUNK_24K = 24000 * 60 // 1000 * 2          # 60 ms of 24 kHz s16 = 2880 bytes


class Body(httpx.AsyncByteStream):
    def __init__(self, pieces, endless=False):
        self.pieces, self.endless, self.closed = pieces, endless, False
        self.pulled = 0

    async def __aiter__(self):
        i = 0
        while True:
            if i >= len(self.pieces):
                if not self.endless:
                    return
                i = 0
            self.pulled += 1
            yield self.pieces[i]
            i += 1
            await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


def make(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAITTS(api_key="sk-test", api_base="https://api.test/v1", client=client, **kw)


async def collect(gen):
    return [c async for c in gen]


async def test_streams_even_sized_chunks_and_preserves_all_audio():
    pcm = bytes(range(256)) * 60 + b"\x01"                          # odd total on purpose
    pieces = [pcm[i:i + 1001] for i in range(0, len(pcm), 1001)]    # odd-sized network reads
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, stream=Body(pieces))

    tts = make(handler)
    chunks = await collect(tts.synthesize("Hello there", voice="nova"))
    assert all(len(c) % 2 == 0 for c in chunks)
    assert all(len(c) == CHUNK_24K for c in chunks[:-1])
    assert b"".join(chunks) == pcm[:-1]                              # only the dangling half-sample dropped

    req = seen["req"]
    assert str(req.url) == "https://api.test/v1/audio/speech"
    assert req.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(req.content) == {
        "model": "gpt-4o-mini-tts", "voice": "nova", "input": "Hello there", "response_format": "pcm"}


async def test_default_voice_and_blank_text():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, stream=Body([b"\x00\x00" * 100]))

    tts = make(handler, default_voice="shimmer")
    assert await collect(tts.synthesize("   ")) == []               # nothing to say -> no request
    await collect(tts.synthesize("hi"))
    assert bodies == [{"model": "gpt-4o-mini-tts", "voice": "shimmer", "input": "hi", "response_format": "pcm"}]


async def test_resamples_to_configured_rate():
    pcm = b"\x10\x00" * 24000                                        # 1 s @ 24 kHz
    tts = make(lambda r: httpx.Response(200, stream=Body([pcm])), sample_rate=16000)
    out = b"".join(await collect(tts.synthesize("x")))
    assert abs(len(out) - 16000 * 2) <= 8 * 2                         # ~1 s @ 16 kHz
    assert tts.sample_rate == 16000


async def test_non_200_raises_with_upstream_message():
    tts = make(lambda r: httpx.Response(401, json={"error": {"message": "Incorrect API key"}}))
    with pytest.raises(VoiceProviderError, match=r"HTTP 401.*Incorrect API key"):
        await collect(tts.synthesize("hello"))


async def test_aclose_stops_upstream_transfer():
    body = Body([b"\x00\x00" * 4000], endless=True)
    tts = make(lambda r: httpx.Response(200, stream=body))
    gen = tts.synthesize("a long sentence")
    first = await gen.__anext__()
    assert first
    await gen.aclose()
    assert body.closed


async def test_task_cancelled_while_waiting_on_audio_closes_upstream_promptly():
    """Cancellation that lands inside the generator (waiting for the next chunk)."""
    body = Body([b"\x00\x00" * 4000], endless=True)

    class Slow(Body):
        async def __aiter__(self):
            yield b"\x00\x00" * 4000
            await asyncio.sleep(30)                 # upstream stalls

    slow = Slow([])
    tts = make(lambda r: httpx.Response(200, stream=slow))

    async def consume():
        async for _ in tts.synthesize("a long sentence"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert slow.closed


async def test_consumer_cancelled_between_chunks_closes_upstream_via_aclosing():
    """A cancel that lands in the consumer (not inside the generator) leaves an async
    generator suspended — the consumer must use aclosing()/aclose(), as the voice session does."""
    body = Body([b"\x00\x00" * 4000], endless=True)
    tts = make(lambda r: httpx.Response(200, stream=body))

    async def consume():
        async with contextlib.aclosing(tts.synthesize("a long sentence")) as chunks:
            async for _ in chunks:
                await asyncio.sleep(0.01)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed
    pulled = body.pulled
    await asyncio.sleep(0.05)
    assert body.pulled == pulled                                     # nothing keeps reading
