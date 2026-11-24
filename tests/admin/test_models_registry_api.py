"""config/models.yaml wired into the app: prices, startup checks, and the admin endpoint."""
import textwrap

from admin_helpers import isolated_config
from pai.app_context import create_app_context
from pai.common.enums.llm import LLMModel


def write_models(tmp_path, body: str):
    p = tmp_path / "models.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_prices_come_from_the_registry_and_config_overrides_win(tmp_path):
    p = write_models(tmp_path, """
        llm:
          chat:
            - {name: a/cheap, tool_use: enabled, input_per_million: 1, output_per_million: 2}
            - {name: a/other, tool_use: enabled, input_per_million: 3, output_per_million: 4}
        speech:
          stt: [{name: s/model, per_minute_cost: 0.002}]
          tts: [{name: t/model, per_1k_chars_cost: 0.03}]
    """)
    cfg = isolated_config(tmp_path)
    cfg.models_file = str(p)
    cfg.voice.airouter.stt_model, cfg.voice.airouter.tts_model = "s/model", "t/model"
    cfg.voice.airouter.stt_cost_per_minute_usd = None
    cfg.voice.airouter.tts_cost_per_1k_chars_usd = 0.5                        # an explicit config value wins over the registry
    from pai.config.models.llm import ModelPrice
    cfg.llm_pricing = {"a/other": ModelPrice(input_per_million=9, output_per_million=9)}
    ctx = create_app_context(cfg)
    pricing = ctx.orchestrator.planner.llm.pricing
    assert pricing["a/cheap"].input_per_million == 1 and pricing["a/other"].input_per_million == 9
    assert cfg.voice.airouter.stt_cost_per_minute_usd == 0.002 and cfg.voice.airouter.tts_cost_per_1k_chars_usd == 0.5
    assert ctx.model_roles["stt"] == "s/model" and ctx.model_registry.llm["a/cheap"].tool_use is True


def test_a_missing_registry_file_does_not_stop_the_app(tmp_path):
    cfg = isolated_config(tmp_path)
    cfg.models_file = str(tmp_path / "does-not-exist.yaml")
    ctx = create_app_context(cfg)
    assert ctx.model_registry.llm == {} and ctx.orchestrator.planner.llm.pricing == {}


def test_models_endpoint_is_admin_only_and_describes_roles_and_problems(harness):
    c = harness.client
    assert c.get("/api/v1/system/models").status_code == 401
    assert c.get("/api/v1/system/models", headers=harness.user["h"]).status_code == 403
    r = c.get("/api/v1/system/models", headers=harness.H)
    assert r.status_code == 200
    body = r.json()
    assert {"source", "roles", "llm", "stt", "tts", "warnings"} <= set(body)
    assert {x["role"] for x in body["roles"]} >= {"default", "stt", "tts"}
    assert body["llm"] and all("input_per_million" in m for m in body["llm"])
