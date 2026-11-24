"""Streaming providers — no network: fake SDK clients and httpx.MockTransport."""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any, AsyncIterator, List, Optional

import httpx
import pytest
from pydantic import BaseModel

from pai.common.enums.llm import LLMModel, LLMProviderName
from pai.config.models.llm import LLMProviderConfig
from pai.llm.models import LLMMessage, LLMRequest, LLMStreamChunk
from pai.llm.provider import (
    GeminiProvider, KimiProvider, LLMProvider, LLMProviderError, OpenAIProvider,
    OpenRouterProvider,
)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)       # backoff delay x0.5


def req() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")])


def cfg(name: LLMProviderName, attempts: int = 3) -> LLMProviderConfig:
    return LLMProviderConfig(provider=name, api_key="k", max_retries=attempts,
                             model_name=LLMModel.GPT_4)


async def collect(provider: LLMProvider) -> List[LLMStreamChunk]:
    return [c async for c in provider.stream(req())]


def text_of(chunks: List[LLMStreamChunk]) -> str:
    return "".join(c.delta for c in chunks)


# ============================== OpenAI SDK ==================================

class _Delta(BaseModel):
    content: Optional[str] = None


class _Choice(BaseModel):
    delta: _Delta = _Delta()
    finish_reason: Optional[str] = None


class _Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _Event(BaseModel):
    choices: List[_Choice] = []
    usage: Optional[_Usage] = None


def ev(text=None, finish=None, usage=None) -> _Event:
    return _Event(choices=[_Choice(delta=_Delta(content=text), finish_reason=finish)], usage=usage)


class FakeSDKStream:
    def __init__(self, events: List[Any], fail_after: Optional[int] = None, gate: asyncio.Event | None = None):
        self.events, self.fail_after, self.gate, self.closed = events, fail_after, gate, False
    def __aiter__(self): return self._gen()
    async def _gen(self):
        for i, e in enumerate(self.events):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("socket reset mid-stream")
            if self.gate is not None and i >= 1:
                await self.gate.wait()                       # park until cancelled
            yield e
    async def close(self): self.closed = True


class FakeOpenAI:
    def __init__(self, behaviors: List[Any]):
        self.behaviors, self.calls, self.last_kwargs = list(behaviors), 0, {}
        self.chat = self; self.completions = self
    async def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        b = self.behaviors.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


def openai_provider(*behaviors, attempts=3) -> tuple[OpenAIProvider, FakeOpenAI]:
    p = OpenAIProvider(cfg(LLMProviderName.OPENAI, attempts))
    fake = FakeOpenAI(behaviors)
    p._client = fake
    return p, fake


async def test_openai_streams_ordered_deltas_finish_and_usage():
    stream = FakeSDKStream([
        ev(None), ev("Hel"), ev("lo "), ev(None), ev("world"), ev(None, finish="stop"),
        _Event(choices=[], usage=_Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8)),
    ])
    p, fake = openai_provider(stream)
    chunks = await collect(p)
    assert [c.delta for c in chunks[:-1]] == ["Hel", "lo ", "world"]     # null/empty skipped
    assert text_of(chunks) == "Hello world"
    last = chunks[-1]
    assert last.delta == "" and last.finish_reason == "stop"
    assert last.usage.total_tokens == 8 and last.usage.prompt_tokens == 5
    assert all(c.finish_reason is None for c in chunks[:-1])
    assert fake.last_kwargs["stream"] is True
    assert fake.last_kwargs["stream_options"] == {"include_usage": True}
    assert stream.closed


async def test_openai_falls_back_when_stream_options_unsupported():
    class Strict(FakeOpenAI):
        async def create(self, **kwargs):
            if "stream_options" in kwargs:
                raise TypeError("unexpected keyword argument 'stream_options'")
            return await super().create(**kwargs)
    p = OpenAIProvider(cfg(LLMProviderName.OPENAI))
    p._client = Strict([FakeSDKStream([ev("ok"), ev(None, finish="stop")])])
    assert text_of(await collect(p)) == "ok"


async def test_openai_cancel_closes_upstream_stream():
    gate = asyncio.Event()
    stream = FakeSDKStream([ev("a"), ev("b"), ev("c")], gate=gate)
    p, _ = openai_provider(stream)
    got: List[str] = []

    async def consume():
        async for c in p.stream(req()):
            got.append(c.delta)

    task = asyncio.create_task(consume())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if got:
            break
    assert got == ["a"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_openai_error_before_first_chunk_is_retried():
    good = FakeSDKStream([ev("fine"), ev(None, finish="stop")])
    p, fake = openai_provider(RuntimeError("503 service unavailable"),
                              FakeSDKStream([RuntimeError("never read")], fail_after=0), good)
    chunks = await collect(p)
    assert text_of(chunks) == "fine" and fake.calls == 3        # open-failure, first-event failure, success


async def test_openai_permanent_error_not_retried_and_mapped():
    p, fake = openai_provider(RuntimeError("401 unauthorized: invalid api key"))
    with pytest.raises(LLMProviderError, match="OpenAI request failed"):
        await collect(p)
    assert fake.calls == 1


async def test_openai_error_mid_stream_surfaces_without_replay():
    stream = FakeSDKStream([ev("par"), ev("tial"), ev("never")], fail_after=2)
    p, fake = openai_provider(stream, FakeSDKStream([ev("REPLAYED")]))
    seen: List[str] = []
    with pytest.raises(LLMProviderError, match="stream failed"):
        async for c in p.stream(req()):
            seen.append(c.delta)
    assert seen == ["par", "tial"] and fake.calls == 1            # no replay
    assert stream.closed


# ====================== OpenRouter / Kimi (httpx SSE) =======================

def sse(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def data(**obj) -> str:
    return "data: " + json.dumps(obj)


class Handler:
    """httpx.MockTransport handler serving scripted responses; records requests."""
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        r = self.responses.pop(0)
        return r(request) if callable(r) else r


def attach(provider: LLMProvider, handler: Handler) -> None:
    provider._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sse_response(body: bytes, status=200) -> httpx.Response:
    return httpx.Response(status, content=body, headers={"content-type": "text/event-stream"})


GOOD_SSE = sse(
    ": OPENROUTER PROCESSING",
    "",
    data(choices=[{"delta": {"role": "assistant", "content": None}}]),
    data(choices=[{"delta": {"content": "Good "}}]),
    ": keep-alive",
    data(choices=[{"delta": {"content": "morning"}, "finish_reason": None}]),
    data(choices=[{"delta": {}, "finish_reason": "stop"}]),
    data(choices=[], usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}),
    "data: [DONE]",
)


@pytest.mark.parametrize("cls,name", [(OpenRouterProvider, LLMProviderName.OPENROUTER),
                                      (KimiProvider, LLMProviderName.KIMI)])
async def test_sse_streams_deltas_finish_usage_and_request_shape(cls, name):
    p = cls(cfg(name))
    h = Handler(sse_response(GOOD_SSE))
    attach(p, h)
    chunks = await collect(p)
    assert [c.delta for c in chunks[:-1]] == ["Good ", "morning"]
    assert chunks[-1].finish_reason == "stop" and chunks[-1].usage.total_tokens == 6
    body = h.requests[0]
    assert body["stream"] is True and isinstance(body["model"], str)     # serializable model id


async def test_sse_http_5xx_before_first_chunk_is_retried_4xx_is_not():
    p = OpenRouterProvider(cfg(LLMProviderName.OPENROUTER))
    h = Handler(httpx.Response(503, text="overloaded"), sse_response(GOOD_SSE))
    attach(p, h)
    assert text_of(await collect(p)) == "Good morning" and len(h.requests) == 2

    k = KimiProvider(cfg(LLMProviderName.KIMI))
    h2 = Handler(httpx.Response(401, text="bad key"), sse_response(GOOD_SSE))
    attach(k, h2)
    with pytest.raises(LLMProviderError, match="Kimi request failed"):
        await collect(k)
    assert len(h2.requests) == 1


async def test_sse_error_mid_stream_surfaces_without_replay():
    body = sse(
        data(choices=[{"delta": {"content": "one "}}]),
        data(choices=[{"delta": {"content": "two"}}]),
        data(error={"message": "upstream went away"}),
    )
    p = OpenRouterProvider(cfg(LLMProviderName.OPENROUTER))
    h = Handler(sse_response(body), sse_response(GOOD_SSE))
    attach(p, h)
    seen: List[str] = []
    with pytest.raises(LLMProviderError, match="stream failed"):
        async for c in p.stream(req()):
            seen.append(c.delta)
    assert seen == ["one ", "two"] and len(h.requests) == 1


async def test_sse_cancel_closes_the_http_response():
    closed = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(data(choices=[{"delta": {"content": "first"}}]))
            await asyncio.sleep(30)                       # server stalls
            yield b""
        async def aclose(self):
            closed.set()

    p = KimiProvider(cfg(LLMProviderName.KIMI))
    attach(p, Handler(httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})))
    got: List[str] = []

    async def consume():
        async for c in p.stream(req()):
            got.append(c.delta)

    task = asyncio.create_task(consume())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if got:
            break
    assert got == ["first"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_sse_empty_stream_still_ends_with_a_final_chunk():
    p = OpenRouterProvider(cfg(LLMProviderName.OPENROUTER))
    attach(p, Handler(sse_response(sse("data: [DONE]"))))
    chunks = await collect(p)
    assert len(chunks) == 1 and chunks[0].delta == "" and chunks[0].finish_reason == "stop"


# ================================ Gemini ====================================

class _GFinish:
    def __init__(self, name): self.name = name


class _GCand:
    def __init__(self, finish=None): self.finish_reason = finish


class _GUsage:
    def __init__(self, p, c, t): self.prompt_token_count, self.candidates_token_count, self.total_token_count = p, c, t


class _GChunk:
    def __init__(self, text, finish=None, usage=None):
        self.text, self.candidates, self.usage_metadata = text, [_GCand(finish)], usage


class FakeGeminiStream:
    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after, self.closed = chunks, fail_after, False
    def __aiter__(self): return self._gen()
    async def _gen(self):
        for i, c in enumerate(self.chunks):
            if self.fail_after == i:
                raise RuntimeError("stream broke")
            yield c
    async def aclose(self): self.closed = True


class FakeGemini:
    def __init__(self, *behaviors):
        self.behaviors, self.calls = list(behaviors), 0
        self.aio = self; self.models = self
    async def generate_content_stream(self, **kwargs):
        self.calls += 1
        b = self.behaviors.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


async def test_gemini_streams_text_finish_reason_and_usage():
    s = FakeGeminiStream([
        _GChunk("Bon"), _GChunk("jour", usage=_GUsage(3, 2, 5)),
        _GChunk("", finish=_GFinish("STOP"), usage=_GUsage(3, 4, 7)),
    ])
    p = GeminiProvider(cfg(LLMProviderName.GEMINI))
    p._client = FakeGemini(s)
    chunks = await collect(p)
    assert text_of(chunks) == "Bonjour"
    assert chunks[-1].finish_reason == "stop" and chunks[-1].usage.total_tokens == 7   # latest usage wins
    assert s.closed


async def test_gemini_retry_before_first_chunk_and_no_replay_after():
    p = GeminiProvider(cfg(LLMProviderName.GEMINI))
    fake = FakeGemini(RuntimeError("503 unavailable"), FakeGeminiStream([_GChunk("ok")]))
    p._client = fake
    assert text_of(await collect(p)) == "ok" and fake.calls == 2

    broken = FakeGeminiStream([_GChunk("a"), _GChunk("b")], fail_after=1)
    p2 = GeminiProvider(cfg(LLMProviderName.GEMINI))
    fake2 = FakeGemini(broken, FakeGeminiStream([_GChunk("REPLAY")]))
    p2._client = fake2
    seen: List[str] = []
    with pytest.raises(LLMProviderError, match="stream failed"):
        async for c in p2.stream(req()):
            seen.append(c.delta)
    assert seen == ["a"] and fake2.calls == 1 and broken.closed


async def test_gemini_max_tokens_maps_to_length():
    p = GeminiProvider(cfg(LLMProviderName.GEMINI))
    p._client = FakeGemini(FakeGeminiStream([_GChunk("cut", finish=_GFinish("MAX_TOKENS"))]))
    assert (await collect(p))[-1].finish_reason == "length"


# ============================ base-class fallback ===========================

async def test_base_class_fallback_yields_one_chunk():
    from pai.llm.models import LLMResponse, LLMUsage

    class Plain(LLMProvider):
        name = LLMProviderName.LOCAL
        @property
        def default_model(self): return "x"
        async def generate(self, request):
            return LLMResponse(content="whole reply", provider=self.name, model=LLMModel.LOCAL_MODEL,
                               usage=LLMUsage(prompt_tokens=1, completion_tokens=2), finish_reason="length")

    chunks = await collect(Plain(cfg(LLMProviderName.LOCAL)))
    assert len(chunks) == 1
    assert chunks[0].delta == "whole reply" and chunks[0].finish_reason == "length"
    assert chunks[0].usage.completion_tokens == 2
