"""OpenAIRealtimeSTT against a local WebSocket server that emulates the realtime
transcription protocol (as understood from the OpenAI docs; NOT the live API)."""
import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import List

import pytest
from websockets.asyncio.server import serve

from pai.voice.audio import sine
from pai.voice.providers.errors import VoiceProviderError
from pai.voice.providers.openai_stt import OpenAIRealtimeSTT

DELTAS = ["hello ", "wor", "ld"]


@dataclass
class Server:
    mode: str = "normal"            # normal | silent | error
    received: List[dict] = field(default_factory=list)
    headers: object = None
    path: str = ""
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    port: int = 0

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


@pytest.fixture
async def server():
    s = Server()

    async def handler(ws):
        s.path = ws.request.path
        s.headers = ws.request.headers      # case-insensitive mapping
        appended = 0
        try:
            async for raw in ws:
                msg = json.loads(raw)
                s.received.append(msg)
                if msg["type"] == "input_audio_buffer.append":
                    if s.mode != "silent" and appended < len(DELTAS):
                        await ws.send(json.dumps({
                            "type": "conversation.item.input_audio_transcription.delta",
                            "delta": DELTAS[appended]}))
                    appended += 1
                elif msg["type"] == "input_audio_buffer.commit":
                    if s.mode == "error":
                        await ws.send(json.dumps({"type": "error", "error": {"message": "boom"}}))
                    elif s.mode == "normal":
                        await ws.send(json.dumps({
                            "type": "conversation.item.input_audio_transcription.completed",
                            "transcript": "hello world"}))
        finally:
            s.closed.set()

    async with serve(handler, "127.0.0.1", 0) as srv:
        s.port = srv.sockets[0].getsockname()[1]
        yield s


def provider(server, **kw):
    return OpenAIRealtimeSTT(api_key="sk-test", api_base=server.api_base, **kw)


async def test_streams_partials_then_final_and_speaks_the_protocol(server):
    stt = provider(server)
    stream = await stt.open(sample_rate=16000, language="en")
    seen = []

    async def collect():
        async for ev in stream.partials():
            seen.append(ev)

    collector = asyncio.create_task(collect())
    for _ in range(3):
        await stream.push(sine(300, 10, 16000))          # 160 samples @16k = 320 bytes
    final = await stream.finish()
    await asyncio.wait_for(collector, 2)

    assert final == "hello world"
    assert [e.text for e in seen] == ["hello", "hello wor", "hello world"] or \
           [e.text for e in seen] == ["hello ", "hello wor", "hello world"]
    assert all(not e.final for e in seen)

    assert server.path == "/v1/realtime?intent=transcription"
    assert server.headers.get("Authorization") == "Bearer sk-test"
    assert server.headers.get("OpenAI-Beta") == "realtime=v1"

    first = server.received[0]
    assert first["type"] == "transcription_session.update"
    sess = first["session"]
    assert sess["input_audio_format"] == "pcm16"
    assert sess["input_audio_transcription"] == {"model": "gpt-4o-transcribe", "language": "en"}
    assert "turn_detection" in sess and sess["turn_detection"] is None        # server VAD off

    appends = [m for m in server.received if m["type"] == "input_audio_buffer.append"]
    assert len(appends) == 3
    # 16 kHz -> 24 kHz: 160 samples become 240 samples = 480 bytes
    assert all(len(base64.b64decode(m["audio"])) == 480 for m in appends)
    assert server.received[-1]["type"] == "input_audio_buffer.commit"
    await asyncio.wait_for(server.closed.wait(), 2)                            # connection released


async def test_language_is_omitted_when_not_given(server):
    stream = await provider(server).open(sample_rate=24000, language=None)
    await stream.push(sine(300, 20, 24000))
    await stream.finish()
    tr = server.received[0]["session"]["input_audio_transcription"]
    assert tr == {"model": "gpt-4o-transcribe"}


async def test_finish_without_audio_returns_empty_and_sends_no_commit(server):
    stream = await provider(server).open(sample_rate=16000, language=None)
    assert await stream.finish() == ""
    await asyncio.wait_for(server.closed.wait(), 2)
    assert not any(m["type"] == "input_audio_buffer.commit" for m in server.received)


async def test_finish_times_out_and_returns_empty(server):
    server.mode = "silent"
    stream = await provider(server, finish_timeout=0.3).open(sample_rate=16000, language=None)
    await stream.push(sine(300, 20, 16000))
    t = asyncio.get_event_loop().time()
    assert await stream.finish() == ""
    assert asyncio.get_event_loop().time() - t < 2


async def test_upstream_error_event_returns_empty(server):
    server.mode = "error"
    stream = await provider(server).open(sample_rate=16000, language=None)
    await stream.push(sine(300, 20, 16000))
    assert await stream.finish() == ""


async def test_aclose_mid_stream_closes_connection_and_ends_partials(server):
    stream = await provider(server).open(sample_rate=16000, language=None)
    await stream.push(sine(300, 20, 16000))
    ended = asyncio.create_task(_drain(stream))
    await stream.aclose()
    await stream.aclose()                                  # idempotent
    await asyncio.wait_for(server.closed.wait(), 2)
    await asyncio.wait_for(ended, 2)
    assert await stream.finish() == ""


async def _drain(stream):
    return [e async for e in stream.partials()]


async def test_open_raises_clear_error_when_unreachable():
    stt = OpenAIRealtimeSTT(api_key="k", api_base="http://127.0.0.1:1/v1", connect_timeout=1.0)
    with pytest.raises(VoiceProviderError, match="could not connect"):
        await stt.open(sample_rate=16000, language=None)
