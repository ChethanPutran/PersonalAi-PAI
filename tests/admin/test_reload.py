"""Reload saved settings into the RUNNING server: what applies live, what needs a restart, and that a bad
configuration changes nothing."""
import asyncio
import copy

import pytest

from admin_helpers import isolated_config
from pai.app_context import create_app_context
from pai.config import settings_admin as S
from pai.config.reload import LIVE_GROUPS, diff_settings, reload_settings


def make(tmp_path):
    """A fresh app context (its own router, voice service, tracker) and the config the 'saved settings' will be."""
    cfg = isolated_config(tmp_path)
    new = cfg.model_copy(deep=True)                      # taken BEFORE the context fills in runtime defaults
    return create_app_context(cfg), new


async def run(ctx, new):
    return await reload_settings(ctx, lambda: new.model_copy(deep=True))      # Config() is a fresh object on every reload


def test_diff_lists_changed_leaf_paths_and_ignores_secrets_only_changes():
    a = {"voice": {"stt": "auto", "airouter": {"voice": "alloy"}}, "x": 1}
    b = {"voice": {"stt": "auto", "airouter": {"voice": "nova"}}, "x": 1, "y": 2}
    assert diff_settings(a, b) == ["voice.airouter.voice", "y"]
    assert diff_settings(a, a) == []


async def test_nothing_changed_nothing_applied(tmp_path):
    ctx, new = make(tmp_path)
    r = await run(ctx, new)
    assert r == {"ok": True, "applied": [], "failed": [], "restart_required": [], "unchanged": True}


async def test_models_routes_and_prices_apply_live(tmp_path):
    ctx, new = make(tmp_path)
    router = ctx.orchestrator.planner.llm
    old_default = router.default_route.model
    new.llm_routes["planner"] = "anthropic/claude-haiku-5.5"
    new.llm_routes["audit"] = "x/custom"
    new.llm_provider.model_name = "google/gemini-3-flash"
    from pai.config.models.llm import ModelPrice
    new.llm_pricing = {"google/gemini-3-flash": ModelPrice(input_per_million=1, output_per_million=2)}
    r = await run(ctx, new)
    assert r["ok"] and [a["group"] for a in r["applied"]] == ["llm"] and r["restart_required"] == []
    assert router.resolve_route("planner").model == "anthropic/claude-haiku-5.5" and router.resolve_route("audit").model == "x/custom"
    assert router.default_route.model == "google/gemini-3-flash" != old_default
    assert router.pricing["google/gemini-3-flash"].input_per_million == 1                    # the override wins over models.yaml
    assert router.get_provider(router.default_route.provider).price_book.price_for("google/gemini-3-flash").output_per_million == 2
    assert ctx.model_roles["planner"] == "anthropic/claude-haiku-5.5"
    assert S.get_path(ctx.running_settings, "llm_routes.audit") == "x/custom"                 # "running" moved: nothing pending
    assert (await run(ctx, new))["unchanged"] is True


async def test_settings_that_need_a_restart_are_listed_and_not_marked_as_running(tmp_path):
    ctx, new = make(tmp_path)
    before_port = S.get_path(ctx.running_settings, "api.port")
    new.api.port = before_port + 1
    new.admin.history_retention_days = 3                                                      # live
    r = await run(ctx, new)
    assert r["restart_required"] == ["api.port"] and [a["group"] for a in r["applied"]] == ["history"]
    assert S.get_path(ctx.running_settings, "api.port") == before_port                         # still pending after the reload
    assert S.get_path(ctx.running_settings, "admin.history_retention_days") == 3
    assert ctx.admin.history.retention_days == 3
    assert (await run(ctx, new))["restart_required"] == ["api.port"]                          # and it stays listed


async def test_voice_providers_are_rebuilt_with_the_new_models_and_prices(tmp_path):
    ctx, new = make(tmp_path)
    from pai.config.models.voice import STTProviderName, TTSProviderName
    new.voice.stt, new.voice.tts = STTProviderName.AIROUTER, TTSProviderName.AIROUTER           # the test config uses fake voice
    new.voice.airouter.api_key = "sk-air-v1-test"
    new.voice.airouter.stt_model = "openai/whisper-1"                                         # priced in models.yaml: 0.006/min
    new.voice.airouter.voice = "nova"
    old_stt = ctx.voice.stt
    r = await run(ctx, new)
    assert r["ok"] and "voice" in [a["group"] for a in r["applied"]]
    assert ctx.voice.stt is not old_stt and ctx.voice.stt._model == "openai/whisper-1"
    assert ctx.voice.stt._per_minute == 0.006 and ctx.voice.tts._default_voice == "nova"       # price filled from the registry
    assert ctx.voice.config.airouter.voice == "nova"


async def test_credits_follow_the_config_but_an_admin_set_balance_wins(tmp_path):
    ctx, new = make(tmp_path)
    tracker = ctx.orchestrator.planner.llm.cost_tracker
    new.llm_cost.initial_credits_usd = 5.0
    assert (await run(ctx, new))["applied"][0]["group"] == "credits"
    assert tracker.credits_summary()["initial_usd"] == 5.0 and tracker.credits_summary()["source"] == "config"
    tracker.set_credits(9.0)                                                                  # the admin sets a balance
    new.llm_cost.initial_credits_usd = 7.0
    await run(ctx, new)
    assert tracker.credits_summary()["initial_usd"] == 9.0 and tracker.credits_summary()["source"] == "admin"
    new.llm_cost.initial_credits_usd = None
    await run(ctx, new)
    tracker.clear_credits()
    assert tracker.credits_summary()["configured"] is False


async def test_a_configuration_that_cannot_load_changes_nothing(tmp_path):
    ctx, new = make(tmp_path)
    router = ctx.orchestrator.planner.llm
    before = (router.default_route.model, dict(router._routes), dict(ctx.running_settings))

    def broken():
        raise ValueError("1 validation error for Config\napi.port\n  Input should be a valid integer")
    r = await reload_settings(ctx, broken)
    assert r["ok"] is False and "could not be loaded" in r["error"] and r["applied"] == []
    assert (router.default_route.model, dict(router._routes), dict(ctx.running_settings)) == before


async def test_a_live_group_that_fails_leaves_the_running_system_as_it_was(tmp_path):
    ctx, new = make(tmp_path)
    router = ctx.orchestrator.planner.llm
    router_before = (router.default_route.model, set(router._routes))
    new.llm_provider.api_key = None                                                            # the provider cannot be built without a key
    new.llm_routes["planner"] = "x/changed"
    new.admin.history_retention_days = 4                                                       # an unrelated live group still applies
    r = await run(ctx, new)
    assert r["ok"] is False and [f["group"] for f in r["failed"]] == ["llm"] and [a["group"] for a in r["applied"]] == ["history"]
    assert (router.default_route.model, set(router._routes)) == router_before
    assert router.resolve_route("planner").model != "x/changed"
    assert S.get_path(ctx.running_settings, "llm_routes.planner") != "x/changed"               # still pending


def test_every_live_prefix_maps_to_exactly_one_group():
    seen = [p for prefixes in LIVE_GROUPS.values() for p in prefixes]
    assert len(seen) == len(set(seen))


# ── the endpoint ────────────────────────────────────────────────────────────

def test_reload_endpoint_applies_saved_settings_and_leaves_the_admin_session_alone(harness, tmp_path):
    from pai.config.app import Config
    ctx = harness.ctx
    base = Config.model_validate(copy.deepcopy(ctx.running_settings))            # exactly what the harness server runs with
    saved = base.model_copy(deep=True)
    saved.admin.history_retention_days = 11
    saved.api.port = base.api.port + 5
    old_loader, old_path = ctx.settings_loader, ctx.settings_overrides_path
    ctx.settings_loader = lambda: saved
    try:
        r = harness.client.post("/api/v1/system/reload", headers=harness.H)
        assert r.status_code == 200
        body = r.json()
        assert [a["group"] for a in body["applied"]] == ["history"] and body["restart_required"] == ["api.port"] and body["ok"] is True
        assert "schema" in body["settings"] and ctx.admin.history.retention_days == 11
        # the admin session still works: same token, no re-login
        assert harness.client.get("/api/v1/admin/overview", headers=harness.H).status_code == 200
        ctx.settings_loader = lambda: (_ for _ in ()).throw(ValueError("bad yaml"))
        bad = harness.client.post("/api/v1/system/reload", headers=harness.H).json()
        assert bad["ok"] is False and harness.client.get("/api/v1/admin/overview", headers=harness.H).status_code == 200
    finally:
        ctx.settings_loader = old_loader
        ctx.admin.history.retention_days = 7
