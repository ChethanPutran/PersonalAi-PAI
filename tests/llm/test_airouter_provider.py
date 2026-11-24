"""AIRouter (https://www.airouter.in) is OpenAI-compatible: it reuses the OpenRouter HTTP/SSE provider with
its own endpoint, and accepts the gateway's `<provider>/<model>` slugs. Hermetic (httpx MockTransport)."""
from __future__ import annotations

import json
import random

import httpx
import pytest

from pai.common.enums.llm import LLMModel, LLMProviderName
from pai.config.models.llm import LLMProviderConfig
from pai.llm.models import LLMMessage, LLMRequest
from pai.llm.provider import (LLM_PROVIDER_CLASSES, AIRouterProvider, LLMProviderError, OpenRouterProvider,
                              create_provider)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)


def make(**kw) -> AIRouterProvider:
    base = dict(provider="airouter", api_key="sk-air-v1-test", model="openai/gpt-5.2", max_retries=2)
    return create_provider(LLMProviderConfig(**{**base, **kw}))


def attach(p, *responses):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        r = responses[min(len(seen) - 1, len(responses) - 1)]
        return r

    p._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


def req(**kw) -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")], **kw)


OK = httpx.Response(200, json={
    "id": "gen-1", "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4, "total_cost": 0.001}})


def test_factory_builds_the_provider_with_the_documented_endpoint():
    p = make()
    assert LLM_PROVIDER_CLASSES[LLMProviderName.AIROUTER] is AIRouterProvider
    assert isinstance(p, OpenRouterProvider) and p.name is LLMProviderName.AIROUTER
    assert p._endpoint == "https://api.airouter.in/v1/chat/completions"


def test_base_url_override_is_used_and_trailing_slash_tolerated():
    assert make(base_url="http://localhost:9000/v1/")._endpoint == "http://localhost:9000/v1/chat/completions"
    assert OpenRouterProvider(LLMProviderConfig(provider="openrouter", api_key="k"))._endpoint \
        == "https://openrouter.ai/api/v1/chat/completions"             # OpenRouter itself is unchanged


def test_gateway_model_slugs_are_accepted_and_compare_by_value():
    m = make().model
    assert m.value == "openai/gpt-5.2" and m == LLMModel("openai/gpt-5.2")
    assert LLMModel("gpt-4") is LLMModel.GPT_4                          # known members still resolve normally
    with pytest.raises(Exception):
        LLMProviderConfig(model="   ")


def test_config_yaml_key_model_is_honoured():
    assert LLMProviderConfig(model="google/gemini-3-flash").model_name.value == "google/gemini-3-flash"
    assert LLMProviderConfig(model_name="a/b").model_name.value == "a/b"


async def test_generate_sends_bearer_key_slug_and_parses_usage():
    p = make()
    seen = attach(p, OK)
    out = await p.generate(req(max_tokens=50))
    r = seen[0]
    assert str(r.url) == "https://api.airouter.in/v1/chat/completions"
    assert r.headers["authorization"] == "Bearer sk-air-v1-test"
    body = json.loads(r.content)
    assert body["model"] == "openai/gpt-5.2" and body["max_tokens"] == 50
    assert out.content == "hello" and out.usage.total_tokens == 4 and out.provider is LLMProviderName.AIROUTER


async def test_errors_name_airouter_not_openrouter_and_a_402_is_not_retried():
    p = make()
    seen = attach(p, httpx.Response(402, json={"error": {"message": "no credits", "type": "billing_error"}}), OK)
    with pytest.raises(LLMProviderError) as e:
        await p.generate(req())
    assert "AIRouter" in str(e.value) and "OpenRouter" not in str(e.value) and "billing_error" in str(e.value)
    assert len(seen) == 1


async def test_5xx_is_retried():
    p = make()
    seen = attach(p, httpx.Response(502, json={"error": {"message": "x", "type": "server_error"}}), OK)
    assert (await p.generate(req())).content == "hello"
    assert len(seen) == 2


async def test_stream_skips_keepalives_and_reads_deltas_and_usage():
    body = "\n".join([
        ": keepalive",
        'data: ' + json.dumps({"choices": [{"delta": {"content": "One "}}]}),
        'data: ' + json.dumps({"choices": [{"delta": {"content": "two"}, "finish_reason": None}]}),
        'data: ' + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        'data: ' + json.dumps({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}),
        "data: [DONE]", ""]).encode()
    p = make()
    seen = attach(p, httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    chunks = [c async for c in p.stream(req())]
    assert "".join(c.delta for c in chunks) == "One two"
    assert chunks[-1].finish_reason == "stop" and chunks[-1].usage.total_tokens == 3
    assert json.loads(seen[0].content)["stream"] is True


async def test_error_frame_inside_a_200_stream_surfaces():
    body = ("\n".join([
        'data: ' + json.dumps({"choices": [{"delta": {"content": "partial"}}]}),
        'data: ' + json.dumps({"error": {"message": "Stream interrupted", "type": "server_error"}}), ""])).encode()
    p = make()
    attach(p, httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    with pytest.raises(LLMProviderError):
        [c async for c in p.stream(req())]


def test_a_missing_key_is_a_clear_configuration_error():
    with pytest.raises(Exception, match="API key missing"):
        create_provider(LLMProviderConfig(provider="airouter", model="x/y"))


# ── role routes (planner / chat / agent) ────────────────────────────────────

def test_roles_route_to_their_own_model_and_unconfigured_roles_use_the_default():
    from pai.llm.router import LLMRouter
    r = LLMRouter(LLMProviderConfig(provider="airouter", api_key="k", model="anthropic/claude-haiku-5.5"))
    r.register_route("planner", r.default_route.provider, "anthropic/claude-sonnet-5.5")
    assert r.resolve_route("planner").model.value == "anthropic/claude-sonnet-5.5"
    assert isinstance(r.resolve_route("planner").model, LLMModel)            # providers need the enum, not a str
    assert r.resolve_route("chat") is r.default_route                       # well-known but unconfigured
    with pytest.raises(ValueError):
        r.resolve_route("typo-route")                                       # a misspelt custom route still fails loudly


async def test_the_route_model_reaches_the_wire_for_generate_and_stream():
    from pai.llm.router import LLMRouter
    r = LLMRouter(LLMProviderConfig(provider="airouter", api_key="k", model="anthropic/claude-haiku-5.5"))
    r.register_route("planner", r.default_route.provider, "anthropic/claude-sonnet-5.5")
    prov = r.get_provider(r.default_route.provider)
    sse = ("data: " + json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}) + "\ndata: [DONE]\n").encode()
    seen = attach(prov, OK, httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}),
                  OK, httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}))
    await r.generate_request(req(), route="planner")
    [c async for c in r.stream_request(req(), route="planner")]
    await r.generate_request(req(), route="chat")
    [c async for c in r.stream_request(req(), route="chat")]
    assert [json.loads(x.content)["model"] for x in seen] == [
        "anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5",
        "anthropic/claude-haiku-5.5", "anthropic/claude-haiku-5.5"]


def test_shipped_config_uses_airouter_with_planner_sonnet_and_chat_haiku():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "config.yaml").read_text())
    assert cfg["llm_provider"]["provider"] == "airouter"
    assert cfg["llm_routes"]["planner"] == "anthropic/claude-sonnet-5.5"
    assert cfg["llm_routes"]["chat"] == "anthropic/claude-haiku-5.5"
    LLMProviderConfig(**{**cfg["llm_provider"], "api_key": "k"})            # the section validates
