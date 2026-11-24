"""Where prompt tokens went (system prompt / capabilities / history / memory / user prompt / tool data) and how
many credits remain. Hermetic."""
from __future__ import annotations

import json
import random
from types import SimpleNamespace

import httpx
import pytest

from pai.config.models.llm import LLMProviderConfig, ModelPrice
from pai.llm.breakdown import (CAPABILITIES, HISTORY, MEMORY, SYSTEM_PROMPT, TOOL_DATA, USER_PROMPT,
                               estimate_tokens, prompt_breakdown, role_breakdown, scale_to)
from pai.llm.cost import CostTracker
from pai.llm.models import LLMMessage, LLMRequest
from pai.llm.router import LLMRouter
from pai.observability import TraceStore, Tracer, set_tracer


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)


# ── breakdown arithmetic ────────────────────────────────────────────────────

def test_estimate_is_about_four_characters_per_token():
    assert estimate_tokens("") == 0 and estimate_tokens(None) == 0
    assert estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2 and estimate_tokens("x" * 400) == 100


def test_prompt_breakdown_names_the_parts_and_leaves_the_rest_as_system_prompt():
    prompt = "RULES " * 10 + "CAPS" * 25 + "MEM" * 40 + "ask" * 8
    b = prompt_breakdown(prompt, {CAPABILITIES: "CAPS" * 25, MEMORY: "MEM" * 40, USER_PROMPT: "ask" * 8, HISTORY: ""})
    assert b[CAPABILITIES] == 25 and b[MEMORY] == 30 and b[USER_PROMPT] == 6
    assert b[SYSTEM_PROMPT] == estimate_tokens("RULES " * 10) and HISTORY not in b


def test_role_breakdown_default_and_agent_mapping():
    msgs = [LLMMessage("system", "s" * 40), LLMMessage("user", "u" * 80), LLMMessage("assistant", "a" * 20)]
    assert role_breakdown(msgs) == {SYSTEM_PROMPT: 10, USER_PROMPT: 20, HISTORY: 5}
    assert role_breakdown(msgs, {"user": TOOL_DATA})[TOOL_DATA] == 20


@pytest.mark.parametrize("total,est", [(100, {"a": 1, "b": 1, "c": 1}), (7, {"a": 5, "b": 1}), (1000, {"a": 3}),
                                       (3, {"a": 1, "b": 1, "c": 1, "d": 1})])
def test_scaled_parts_always_sum_to_the_providers_real_total(total, est):
    out = scale_to(total, est)
    assert sum(out.values()) == total and all(v > 0 for v in out.values())


def test_scale_edge_cases_attribute_nothing_rather_than_invent():
    assert scale_to(0, {"a": 5}) == {} and scale_to(10, {}) == {} and scale_to(10, {"a": 0}) == {}


# ── breakdown through the router ────────────────────────────────────────────

def body(prompt=1000, completion=50, cost=0.002) -> dict:
    return {"id": "g", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
                      "total_cost": cost}}


def router(tracker, **kw) -> LLMRouter:
    return LLMRouter(LLMProviderConfig(provider="airouter", api_key="k", model="anthropic/claude-haiku-5.5",
                                       max_retries=1), cost_tracker=tracker, **kw)


def wire(r, *responses):
    queue = list(responses)
    r.get_provider(r.default_route.provider)._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: queue.pop(0)))


async def test_explicit_breakdown_is_scaled_to_real_prompt_tokens_and_reaches_response_and_totals():
    t = CostTracker()
    r = router(t)
    wire(r, httpx.Response(200, json=body(prompt=1000)))
    resp = await r.generate_request(LLMRequest(
        messages=[LLMMessage("user", "x")], breakdown={SYSTEM_PROMPT: 100, MEMORY: 300, USER_PROMPT: 100}))
    assert resp.usage.breakdown == {SYSTEM_PROMPT: 200, MEMORY: 600, USER_PROMPT: 200}
    s = t.summary()
    assert s["by_section"][MEMORY] == {"tokens": 600, "calls": 1}
    assert s["recent"][-1]["breakdown"][MEMORY] == 600


async def test_without_an_explicit_breakdown_the_message_roles_are_used():
    t = CostTracker()
    r = router(t)
    wire(r, httpx.Response(200, json=body(prompt=100)))
    resp = await r.generate_request(LLMRequest(messages=[LLMMessage("system", "s" * 300), LLMMessage("user", "u" * 100)]))
    assert resp.usage.breakdown == {SYSTEM_PROMPT: 75, USER_PROMPT: 25}


async def test_streaming_calls_carry_the_breakdown_too():
    t = CostTracker()
    r = router(t)
    sse = "\n".join(["data: " + json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}),
                     "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 80, "completion_tokens": 5, "total_tokens": 85}}),
                     "data: [DONE]", ""]).encode()
    wire(r, httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}))
    chunks = [c async for c in r.stream_request(LLMRequest(messages=[LLMMessage("user", "hi")],
                                                           breakdown={MEMORY: 1, HISTORY: 3}))]
    assert chunks[-1].usage.breakdown == {MEMORY: 20, HISTORY: 60}


async def test_trace_event_carries_prompt_breakdown():
    store = TraceStore(persist_dir=None)
    tracer = Tracer(store=store)
    set_tracer(tracer)
    try:
        r = router(CostTracker())
        wire(r, httpx.Response(200, json=body(prompt=100)))
        async with tracer.start_trace("t", trace_id="bd"):
            await r.generate_request(LLMRequest(messages=[LLMMessage("user", "x")], breakdown={MEMORY: 1, USER_PROMPT: 1}))
        events = store.get_trace("bd")
    finally:
        set_tracer(Tracer(store=None))
    llm = [e for e in events if str(e.name).startswith("llm:") and "prompt_breakdown" in e.attributes]
    assert llm and llm[-1].attributes["prompt_breakdown"] == {MEMORY: 50, USER_PROMPT: 50}


async def test_old_ledger_lines_without_a_breakdown_still_load(tmp_path):
    ledger = tmp_path / "u.jsonl"
    ledger.write_text(json.dumps({"ts": 1.0, "provider": "p", "model": "m", "route": "r", "prompt_tokens": 5,
                                  "completion_tokens": 5, "total_tokens": 10, "cost_usd": 0.5,
                                  "cost_source": "configured"}) + "\n")
    s = CostTracker(ledger_path=ledger).summary()
    assert s["total"]["calls"] == 1 and s["by_section"] == {}


async def test_decomposer_sends_its_prompt_parts_with_the_planner_request():
    """The planner prompt's capabilities / history / memory / goal are measured, the rest is system_prompt."""
    from pai.planning.goal_decomposer import GoalDecomposer
    from pai.prompts.registry import PromptRegistry
    from pai.config.models.prompts import PromptRegistryConfig
    import pai.planning.goal_decomposer as gd

    class _Span:
        def set_attribute(self, *a): ...
        def set_error(self, *a): ...

    class _Tracer:
        def span(self, *a, **k):
            class C:
                async def __aenter__(s): return _Span()
                async def __aexit__(s, *e): return False
            return C()

    seen = {}

    class LLM:
        async def generate_request(self, req, route=None):
            seen["breakdown"], seen["route"] = req.breakdown, route
            return SimpleNamespace(content='[{"id":"task_1","name":"c","capability":"chat.reply","type":"one_shot","parameters":{}}]',
                                   finish_reason="stop")

    class Router:
        async def describe_capabilities_with_schema(self, user_id):
            return {"chat.reply": {"description": "reply", "parameters": {"type": "object", "properties": {}}}}

    class Guard:
        def inspect_request(self, **kw):
            return SimpleNamespace(memory_block="REMEMBER " * 30, history_block="EARLIER " * 20)

        def sanitize_response(self, raw_text, **kw):
            return SimpleNamespace(clean_text=raw_text)

    class Tracker:
        async def record(self, **kw): ...

    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(gd, "get_tracer", lambda: _Tracer())
    try:
        d = GoalDecomposer(LLM(), Router(), None, None, PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True)),
                           Tracker(), Guard())
        await d.decompose("what is the weather in paris", {"user_id": "u1"})
    finally:
        mp.undo()
    b = seen["breakdown"]
    assert seen["route"] == "planner"
    assert b[MEMORY] == estimate_tokens("REMEMBER " * 30) and b[HISTORY] == estimate_tokens("EARLIER " * 20)
    assert b[USER_PROMPT] == estimate_tokens("what is the weather in paris")
    assert b[CAPABILITIES] > 0 and b[SYSTEM_PROMPT] > b[CAPABILITIES]


# ── credits ─────────────────────────────────────────────────────────────────

def spend(t: CostTracker, usd, ts=None):
    r = t.record(provider="p", model="m", prompt_tokens=10, completion_tokens=10, total_tokens=20,
                 cost_usd=usd, cost_source="configured" if usd is not None else None, route="r")
    if ts is not None:
        r.ts = ts
    return r


def test_no_credits_configured_reports_unconfigured():
    c = CostTracker().credits_summary()
    assert c["configured"] is False and c["remaining_usd"] is None


def test_configured_initial_credits_count_all_spend():
    t = CostTracker(initial_credits_usd=10.0)
    spend(t, 1.5)
    spend(t, 0.5)
    c = t.credits_summary()
    assert c["source"] == "config" and c["initial_usd"] == 10.0 and c["spent_usd"] == 2.0
    assert c["remaining_usd"] == 8.0 and c["used_pct"] == 20.0 and c["exhausted"] is False


def test_unpriced_calls_are_counted_but_cannot_be_deducted():
    t = CostTracker(initial_credits_usd=5.0)
    spend(t, None)
    spend(t, 1.0)
    c = t.credits_summary()
    assert c["remaining_usd"] == 4.0 and c["unpriced_calls"] == 1


def test_exhausted_credits_go_to_zero_or_below_and_are_flagged():
    t = CostTracker(initial_credits_usd=1.0)
    spend(t, 1.25)
    c = t.credits_summary()
    assert c["exhausted"] is True and c["remaining_usd"] == -0.25 and c["used_pct"] == 100.0


def test_admin_balance_counts_from_the_moment_it_is_set_and_replaces_config(tmp_path):
    cp = tmp_path / "credits.json"
    t = CostTracker(initial_credits_usd=10.0, credits_path=cp)
    spend(t, 4.0)                                         # before the admin sets a balance
    t.set_credits(20.0)
    assert t.credits_summary()["remaining_usd"] == 20.0 and t.credits_summary()["source"] == "admin"
    spend(t, 3.0)
    assert t.credits_summary()["remaining_usd"] == 17.0
    t.set_credits(50.0)                                   # a top-up: a new balance, counting restarts
    assert t.credits_summary()["remaining_usd"] == 50.0


def test_admin_balance_survives_a_restart_and_only_later_spend_counts(tmp_path):
    ledger, cp = tmp_path / "u.jsonl", tmp_path / "c.json"
    t = CostTracker(ledger_path=ledger, credits_path=cp)
    spend(t, 2.0)                                         # before: must not be deducted from the new balance
    t.set_credits(10.0)
    spend(t, 1.0)
    again = CostTracker(ledger_path=ledger, credits_path=cp)
    c = again.credits_summary()
    assert c["source"] == "admin" and c["initial_usd"] == 10.0 and c["spent_usd"] == pytest.approx(1.0)
    assert c["remaining_usd"] == pytest.approx(9.0)


def test_clearing_returns_to_the_configured_credits_and_counts_all_spend_again(tmp_path):
    ledger, cp = tmp_path / "u.jsonl", tmp_path / "c.json"
    t = CostTracker(ledger_path=ledger, credits_path=cp, initial_credits_usd=10.0)
    spend(t, 2.0)
    t.set_credits(100.0)
    c = t.clear_credits()
    assert c["source"] == "config" and c["remaining_usd"] == 8.0 and not cp.exists()
    assert CostTracker(ledger_path=ledger, credits_path=cp, initial_credits_usd=10.0).credits_summary()["remaining_usd"] == 8.0
    only_admin = CostTracker(credits_path=tmp_path / "x.json")
    only_admin.set_credits(5.0)
    assert only_admin.clear_credits()["configured"] is False


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_invalid_balances_are_rejected(bad):
    with pytest.raises(ValueError):
        CostTracker().set_credits(bad)


def test_corrupt_credit_file_is_ignored(tmp_path):
    cp = tmp_path / "c.json"
    cp.write_text("{not json")
    assert CostTracker(credits_path=cp, initial_credits_usd=3.0).credits_summary()["source"] == "config"
    cp.write_text(json.dumps({"balance_usd": -5, "as_of": 1}))
    assert CostTracker(credits_path=cp).credits_summary()["configured"] is False


async def test_credits_fall_as_real_calls_are_made():
    t = CostTracker(initial_credits_usd=1.0)
    r = router(t)
    wire(r, httpx.Response(200, json=body(cost=0.25)), httpx.Response(200, json=body(cost=0.25)))
    await r.generate_request(LLMRequest(messages=[LLMMessage("user", "a")]))
    await r.generate_request(LLMRequest(messages=[LLMMessage("user", "b")]))
    assert t.summary()["credits"]["remaining_usd"] == 0.5


# ── time series + per-goal ──────────────────────────────────────────────────

H = 3600
T0 = 1_700_000_000 // H * H          # an hour boundary


def with_ts(t: CostTracker, ts: float, usd, route="chat", model="m", prompt=10, completion=5, trace_id=None):
    """Record a call as if it happened at `ts`."""
    return t.record(provider="p", model=model, prompt_tokens=prompt, completion_tokens=completion,
                    total_tokens=prompt + completion, cost_usd=usd, cost_source="configured" if usd is not None else None,
                    route=route, trace_id=trace_id, ts=ts)


def test_series_buckets_calls_tokens_and_cost_and_fills_empty_buckets():
    t = CostTracker()
    with_ts(t, T0 + 5, usd=1.0)
    with_ts(t, T0 + 1800, usd=0.5, prompt=100, completion=50)
    with_ts(t, T0 + 2 * H + 10, usd=2.0)
    s = t.series(bucket="hour", since=T0, until=T0 + 2 * H)
    assert s["bucket_seconds"] == H and [p["t"] for p in s["points"]] == [T0, T0 + H, T0 + 2 * H]
    p0, p1, p2 = s["points"]
    assert (p0["calls"], p0["prompt_tokens"], p0["completion_tokens"], p0["total_tokens"], p0["cost_usd"]) == (2, 110, 55, 165, 1.5)
    assert p1["calls"] == 0 and p1["cost_usd"] == 0 and p2["cost_usd"] == 2.0


def test_series_minute_and_day_rollups_agree_with_hours():
    t = CostTracker()
    with_ts(t, T0 + 30, usd=1.0)
    with_ts(t, T0 + 90, usd=2.0)
    minute = t.series(bucket="minute", since=T0, until=T0 + 120)
    assert [p["cost_usd"] for p in minute["points"]] == [1.0, 2.0, 0]
    day = t.series(bucket="day", since=T0 - 86400, until=T0 + 86400)
    assert sum(p["cost_usd"] for p in day["points"]) == 3.0 and sum(p["calls"] for p in day["points"]) == 2


def test_series_filters_by_route_and_model_and_counts_unpriced():
    t = CostTracker()
    with_ts(t, T0 + 1, usd=1.0, route="planner", model="a")
    with_ts(t, T0 + 2, usd=2.0, route="chat", model="b")
    with_ts(t, T0 + 3, usd=None, route="chat", model="b")
    assert t.series(bucket="hour", since=T0, until=T0, route="planner")["points"][0]["cost_usd"] == 1.0
    chat = t.series(bucket="hour", since=T0, until=T0, route="chat", model="b")["points"][0]
    assert chat["cost_usd"] == 2.0 and chat["calls"] == 2 and chat["unpriced_calls"] == 1
    assert t.series(bucket="hour", since=T0, until=T0, model="nope")["points"][0]["calls"] == 0


def test_series_default_window_and_validation():
    t = CostTracker()
    now = T0 + 5 * H + 100
    s = t.series(bucket="hour", now=now)
    assert len(s["points"]) == 24 and s["points"][-1]["t"] == T0 + 5 * H
    with pytest.raises(ValueError):
        t.series(bucket="week")
    with pytest.raises(ValueError):
        t.series(bucket="minute", since=0, until=10**9)


def test_series_survives_a_restart_through_the_ledger(tmp_path):
    ledger = tmp_path / "u.jsonl"
    ledger.write_text("\n".join(json.dumps({"ts": T0 + i, "provider": "p", "model": "m", "route": "chat", "prompt_tokens": 1,
                                            "completion_tokens": 1, "total_tokens": 2, "cost_usd": 0.5, "cost_source": "configured"})
                                for i in (1, 2, 3)) + "\n")
    s = CostTracker(ledger_path=ledger).series(bucket="hour", since=T0, until=T0)
    assert s["points"][0]["calls"] == 3 and s["points"][0]["cost_usd"] == 1.5


async def test_calls_in_a_goal_carry_its_trace_id_into_the_ledger(tmp_path):
    t = CostTracker(ledger_path=tmp_path / "u.jsonl")
    r = router(t)
    wire(r, httpx.Response(200, json=body(prompt=10)), httpx.Response(200, json=body(prompt=20)), httpx.Response(200, json=body(prompt=30)))
    store = TraceStore(persist_dir=None)
    tracer = Tracer(store=store)
    set_tracer(tracer)
    try:
        async with tracer.start_trace("goal", trace_id="goal-1"):
            await r.generate_request(LLMRequest(messages=[LLMMessage("user", "a")]), route="planner")
            await r.generate_request(LLMRequest(messages=[LLMMessage("user", "b")]), route="chat")
        await r.generate_request(LLMRequest(messages=[LLMMessage("user", "c")]))      # outside any trace
    finally:
        set_tracer(Tracer(store=None))
    lines = [json.loads(x) for x in (tmp_path / "u.jsonl").read_text().splitlines()]
    assert [l["trace_id"] for l in lines] == ["goal-1", "goal-1", None]
    assert [l["route"] for l in lines] == ["planner", "chat", "default"]
    assert CostTracker(ledger_path=tmp_path / "u.jsonl").summary()["total"]["calls"] == 3


def test_a_missing_or_zero_total_means_prompt_plus_completion():
    t = CostTracker()
    t.record(provider="p", model="m", prompt_tokens=100, completion_tokens=20, total_tokens=0, cost_usd=0.1, cost_source="configured", route="r")
    t.record(provider="p", model="m", prompt_tokens=1, completion_tokens=1, total_tokens=50, cost_usd=0.1, cost_source="configured", route="r")
    assert t.summary()["total"]["total_tokens"] == 120 + 50          # an explicit total is kept, a zero one is derived
    assert t.record_metered(provider="p", model="m", route="voice.tts", units=5, unit="characters", cost_usd=None).total_tokens == 0
