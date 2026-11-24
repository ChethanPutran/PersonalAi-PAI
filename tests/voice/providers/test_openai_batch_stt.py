import io
import json
import wave

import httpx

from pai.voice.audio import sine
from pai.voice.providers.openai_stt import OpenAIBatchSTT


def make(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIBatchSTT(api_key="sk-test", api_base="https://api.test/v1", client=client, **kw)


async def test_posts_wav_multipart_and_returns_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "  turn on the light "})

    stt = make(handler)
    stream = await stt.open(sample_rate=16000, language="en")
    await stream.push(sine(300, 100, 16000))
    await stream.push(sine(300, 100, 16000))
    assert [e async for e in stream.partials()] == []          # no partials in batch mode
    assert await stream.finish() == "turn on the light"

    assert captured["url"] == "https://api.test/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer sk-test"
    body = captured["body"]
    assert b'name="model"' in body and b"gpt-4o-transcribe" in body
    assert b'name="language"' in body and b'name="response_format"' in body
    riff = body[body.index(b"RIFF"):]
    with wave.open(io.BytesIO(riff[: 44 + 6400]), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


async def test_empty_utterance_makes_no_request():
    calls = []
    stt = make(lambda r: calls.append(r) or httpx.Response(200, json={"text": "x"}))
    stream = await stt.open(sample_rate=16000, language=None)
    assert await stream.finish() == ""
    assert calls == []


async def test_http_error_and_bad_json_return_empty():
    for response in (httpx.Response(500, text="oops"), httpx.Response(200, text="not json")):
        stt = make(lambda r, resp=response: resp)
        stream = await stt.open(sample_rate=16000, language=None)
        await stream.push(sine(300, 50, 16000))
        assert await stream.finish() == ""


async def test_network_failure_returns_empty():
    def handler(request):
        raise httpx.ConnectError("down")

    stream = await make(handler).open(sample_rate=16000, language=None)
    await stream.push(sine(300, 50, 16000))
    assert await stream.finish() == ""


async def test_buffer_is_capped():
    stream = await make(lambda r: httpx.Response(200, json={"text": "ok"})).open(
        sample_rate=16000, language=None)
    for _ in range(400):
        await stream.push(b"\x00\x00" * 16000)                 # 400 s of audio offered
    assert len(stream._buf) <= 16000 * 2 * 120
