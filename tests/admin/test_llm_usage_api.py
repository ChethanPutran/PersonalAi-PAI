"""Admin LLM usage + credits endpoints (real app)."""


def test_usage_and_credits_are_admin_only(harness):
    c = harness.client
    for method, path, kw in [("get", "/api/v1/system/llm/usage", {}), ("put", "/api/v1/system/llm/credits", {"json": {"balance_usd": 5}}),
                             ("delete", "/api/v1/system/llm/credits", {})]:
        assert getattr(c, method)(path, **kw).status_code == 401
        assert getattr(c, method)(path, headers=harness.user["h"], **kw).status_code == 403


def test_set_read_and_reset_credits(harness):
    c, H = harness.client, harness.H
    first = c.get("/api/v1/system/llm/usage", headers=H).json()
    assert {"total", "by_model", "by_route", "by_section", "credits", "recent"} <= set(first)
    assert first["credits"]["configured"] is False

    r = c.put("/api/v1/system/llm/credits", json={"balance_usd": 12.5}, headers=H)
    assert r.status_code == 200 and r.json()["remaining_usd"] == 12.5 and r.json()["source"] == "admin"
    assert c.get("/api/v1/system/llm/usage", headers=H).json()["credits"]["initial_usd"] == 12.5

    for bad in ({"balance_usd": -1}, {"balance_usd": "lots"}, {}):
        assert c.put("/api/v1/system/llm/credits", json=bad, headers=H).status_code == 422

    assert c.delete("/api/v1/system/llm/credits", headers=H).json()["configured"] is False
    assert c.get("/api/v1/system/llm/usage", headers=H).json()["credits"]["configured"] is False


def test_series_endpoint_validates_and_returns_buckets(harness):
    c, H = harness.client, harness.H
    assert c.get("/api/v1/system/llm/usage/series").status_code == 401
    assert c.get("/api/v1/system/llm/usage/series", headers=harness.user["h"]).status_code == 403
    r = c.get("/api/v1/system/llm/usage/series", headers=H, params={"bucket": "hour"})
    assert r.status_code == 200 and len(r.json()["points"]) == 24 and r.json()["bucket_seconds"] == 3600
    assert {"t", "calls", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"} <= set(r.json()["points"][0])
    assert c.get("/api/v1/system/llm/usage/series", headers=H, params={"bucket": "week"}).status_code == 422
    assert c.get("/api/v1/system/llm/usage/series", headers=H, params={"bucket": "minute", "since": 0}).status_code == 422


def test_usage_reports_voice_cost_and_active_sessions(harness):
    tracker = harness.ctx.orchestrator.planner.llm.cost_tracker
    tracker.record_metered(provider="airouter", model="m/stt", route="voice.stt", units=60, unit="seconds", cost_usd=0.0017)
    tracker.record_metered(provider="airouter", model="m/tts", route="voice.tts", units=1000, unit="characters", cost_usd=0.015)
    v = harness.client.get("/api/v1/system/llm/usage", headers=harness.H).json()["voice"]
    assert v["stt"]["units"] >= 60 and v["tts"]["units"] >= 1000 and v["total_cost_usd"] >= 0.0167
    assert v["stt"]["unit"] == "seconds" and v["tts"]["unit"] == "characters"
    assert isinstance(v["active_sessions"], int) and v["last_hour_cost_usd"] >= 0.0167
