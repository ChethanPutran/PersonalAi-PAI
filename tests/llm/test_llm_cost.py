"""Token + cost accounting: price per million tokens -> cost per call, running totals per provider/model/route,
a restart-proof ledger, and tokens/cost in the trace. Hermetic (httpx MockTransport)."""
from __future__ import annotations

import json
import random

import httpx
import pytest

from pai.config.models.llm import LLMProviderConfig, ModelPrice
from pai.llm.cost import CostTracker, PriceBook, as_cost, compute_cost
from pai.llm.models import LLMMessage, LLMRequest
from pai.llm.router import LLMRouter
from pai.observability import TraceStore, Tracer, set_tracer
from pai.observability.tracer import get_tracer


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)


def req() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="hi")])


def body(prompt=1000, completion=500, cost=None) -> dict:
    u = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if cost is not None:
        u["total_cost"] = cost
    return {"id": "g", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": u}


def sse(prompt=1000, completion=500, cost=None) -> httpx.Response:
    u = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if cost is not None:
        u["total_cost"] = cost
    text = "\n".join(["data: " + json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}),
                      "data: " + json.dumps({"choices": [], "usage": u}), "data: [DONE]", ""])
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})


def router(pricing=None, tracker=None, **cfg) -> LLMRouter:
    c = dict(provider="airouter", api_key="k", model="anthropic/claude-haiku-5.5", max_retries=1)
    c.update(cfg)
    r = LLMRouter(LLMProviderConfig(**c), pricing=pricing, cost_tracker=tracker)
    r.register_route("planner", r.default_route.provider, "anthropic/claude-sonnet-5.5")
    return r


def wire(r: LLMRouter, *responses):
    p = r.get_provider(r.default_route.provider)
    queue = list(responses)
    p._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: queue.pop(0)))
    return p


PRICES = {"anthropic/claude-haiku-5.5": ModelPrice(input_per_million=1.0, output_per_million=5.0),
          "anthropic/claude-sonnet-5.5": ModelPrice(input_per_million=3.0, output_per_million=15.0)}


# ── arithmetic ──────────────────────────────────────────────────────────────

def test_cost_is_tokens_over_a_million_times_the_price_input_and_output_separately():
    assert compute_cost(1_000_000, 0, ModelPrice(input_per_million=3, output_per_million=15)) == 3.0
    assert compute_cost(0, 1_000_000, ModelPrice(input_per_million=3, output_per_million=15)) == 15.0
    assert compute_cost(1000, 500, ModelPrice(input_per_million=1, output_per_million=5)) == pytest.approx(0.0035)
    assert compute_cost(-5, -5, ModelPrice(input_per_million=1, output_per_million=1)) == 0.0


def test_reported_cost_parsing_never_raises():
    assert as_cost("0.0042") == 0.0042 and as_cost(1) == 1.0
    assert as_cost(None) is None and as_cost("abc") is None and as_cost(-1) is None and as_cost(float("nan")) is None


def test_price_book_prefers_the_model_entry_then_the_provider_default():
    pb = PriceBook({"a/b": ModelPrice(input_per_million=1, output_per_million=2)},
                   ModelPrice(input_per_million=9, output_per_million=9))
    assert pb.price_for("a/b").input_per_million == 1 and pb.price_for("other").input_per_million == 9
    assert PriceBook().price_for("a/b") is None


def test_negative_prices_are_rejected_by_config():
    with pytest.raises(Exception):
        ModelPrice(input_per_million=-1)


# ── per-request cost, totals, routes ────────────────────────────────────────

async def test_each_request_is_costed_with_its_models_price_and_totals_keep_running():
    t = CostTracker()
    r = router(PRICES, t)
    wire(r, httpx.Response(200, json=body(1000, 500)), httpx.Response(200, json=body(2000, 100)))
    a = await r.generate_request(req(), route="planner")          # sonnet: 3/15
    b = await r.generate_request(req())                           # haiku : 1/5
    assert a.usage.cost_usd == pytest.approx(1000 * 3e-6 + 500 * 15e-6) and a.usage.cost_source == "configured"
    assert b.usage.cost_usd == pytest.approx(2000 * 1e-6 + 100 * 5e-6)
    s = t.summary()
    assert s["total"]["calls"] == 2 and s["total"]["total_tokens"] == 3600 and s["total"]["prompt_tokens"] == 3000
    assert s["total"]["cost_usd"] == pytest.approx(a.usage.cost_usd + b.usage.cost_usd)
    assert s["by_route"]["planner"]["calls"] == 1 and s["by_route"]["default"]["calls"] == 1
    assert s["by_model"]["anthropic/claude-sonnet-5.5"]["total_tokens"] == 1500
    assert s["by_provider"]["airouter"]["calls"] == 2
    assert t.total_cost_usd == pytest.approx(s["total"]["cost_usd"])


async def test_streaming_calls_are_costed_once_with_their_route():
    t = CostTracker()
    r = router(PRICES, t)
    wire(r, sse(1000, 500))
    chunks = [c async for c in r.stream_request(req(), route="planner")]
    assert chunks[-1].usage.cost_usd == pytest.approx(0.0105)
    s = t.summary()
    assert s["total"]["calls"] == 1 and s["by_route"]["planner"]["calls"] == 1


async def test_configured_price_wins_but_the_gateway_reported_cost_is_the_fallback():
    t = CostTracker()
    r = router(None, t)                                            # no price configured
    wire(r, httpx.Response(200, json=body(1000, 500, cost=0.0123)))
    resp = await r.generate_request(req())
    assert resp.usage.cost_usd == 0.0123 and resp.usage.cost_source == "provider"
    t2 = CostTracker()
    r2 = router(PRICES, t2)
    wire(r2, httpx.Response(200, json=body(1000, 500, cost=999.0)))
    resp2 = await r2.generate_request(req())
    assert resp2.usage.cost_source == "configured" and resp2.usage.cost_usd < 1


async def test_provider_default_price_fields_apply_to_models_without_an_entry():
    t = CostTracker()
    r = router({}, t, cost_per_million_input=2.0, cost_per_million_output=4.0)
    wire(r, httpx.Response(200, json=body(1_000_000, 1_000_000)))
    assert (await r.generate_request(req())).usage.cost_usd == pytest.approx(6.0)


async def test_unknown_price_is_none_not_zero_but_tokens_are_still_counted():
    t = CostTracker()
    r = router(None, t)
    wire(r, httpx.Response(200, json=body(100, 50)))
    resp = await r.generate_request(req())
    assert resp.usage.cost_usd is None and resp.usage.cost_source is None
    s = t.summary()["total"]
    assert s["total_tokens"] == 150 and s["cost_usd"] == 0.0 and s["unpriced_calls"] == 1


async def test_failed_calls_are_not_counted_and_a_call_is_never_counted_twice():
    t = CostTracker()
    r = router(PRICES, t)
    p = wire(r, httpx.Response(401, json={"error": {"message": "bad", "type": "auth_error"}}))
    with pytest.raises(Exception):
        await r.generate_request(req())
    assert t.summary()["total"]["calls"] == 0
    usage = (await _ok(r, p)).usage
    p._account("anthropic/claude-haiku-5.5", usage)                # a second trace of the same usage
    assert t.summary()["total"]["calls"] == 1


async def _ok(r, p):
    p._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body())))
    return await r.generate_request(req())


# ── ledger ──────────────────────────────────────────────────────────────────

async def test_totals_survive_a_restart_via_the_ledger_and_bad_lines_are_skipped(tmp_path):
    ledger = tmp_path / "d" / "usage.jsonl"
    t = CostTracker(ledger_path=ledger)
    r = router(PRICES, t)
    wire(r, httpx.Response(200, json=body(1000, 500)), httpx.Response(200, json=body(10, 10)))
    await r.generate_request(req(), route="planner")
    await r.generate_request(req())
    before = t.summary()["total"]
    with ledger.open("a") as f:
        f.write("not json\n{\"ts\": 1}\n")                         # corrupt lines
    again = CostTracker(ledger_path=ledger).summary()
    assert again["total"] == before and again["by_route"]["planner"]["calls"] == 1


def test_an_unwritable_ledger_never_breaks_accounting(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    t = CostTracker(ledger_path=blocker / "usage.jsonl")           # parent is a file -> OSError
    t.record(provider="p", model="m", prompt_tokens=1, completion_tokens=1, total_tokens=2,
             cost_usd=0.1, cost_source="configured", route="r")
    assert t.summary()["total"]["calls"] == 1


# ── tracing ─────────────────────────────────────────────────────────────────

async def test_the_documented_airouter_usage_payload_is_read_for_both_generate_and_stream():
    """AIRouter reports `usage.total_cost` in USD (https://www.airouter.in/docs/api-reference#cost-reporting)."""
    t = CostTracker()
    r = router(None, t)
    wire(r, httpx.Response(200, json=body(12, 48, cost=0.00042)), sse(12, 48, cost=0.00042))
    a = await r.generate_request(req())
    chunks = [c async for c in r.stream_request(req())]
    for u in (a.usage, chunks[-1].usage):
        assert (u.prompt_tokens, u.completion_tokens, u.total_tokens) == (12, 48, 60)
        assert u.provider_cost_usd == 0.00042 and u.cost_usd == 0.00042 and u.cost_source == "provider"
    assert t.summary()["total"]["cost_usd"] == pytest.approx(0.00084)


async def test_trace_events_and_summary_carry_tokens_cost_source_and_route():
    store = TraceStore(persist_dir=None)
    tracer = Tracer(store=store)
    set_tracer(tracer)
    try:
        r = router(PRICES, CostTracker())
        wire(r, httpx.Response(200, json=body(1000, 500)), httpx.Response(200, json=body(100, 100)))
        async with tracer.start_trace("t", trace_id="tr1"):
            await r.generate_request(req(), route="planner")
            await r.generate_request(req())
        events = store.get_trace("tr1")
        summary = store.get_summary("tr1")
    finally:
        set_tracer(Tracer(store=None))
    llm = [e for e in events if str(e.name).startswith("llm:") and "cost_usd" in e.attributes]
    assert len(llm) == 2
    a = llm[0].attributes
    assert a["prompt_tokens"] == 1000 and a["completion_tokens"] == 500 and a["total_tokens"] == 1500
    assert a["cost_usd"] == pytest.approx(0.0105) and a["cost_source"] == "configured" and a["route"] == "planner"
    assert llm[1].attributes["route"] == "default"
    ends = [e for e in events if e.name == "router.generate_request" and e.kind == "span.end"]
    assert ends and ends[0].attributes["total_tokens"] == 1500 and ends[0].attributes["cost_usd"] == pytest.approx(0.0105)
    assert summary.total_tokens == 1700 and summary.cost_usd == pytest.approx(0.0105 + 100e-6 + 500e-6)
    assert "total_tokens" in summary.to_dict() and "cost_usd" in summary.to_dict()
