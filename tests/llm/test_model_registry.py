"""config/models.yaml: the model registry (capabilities + prices)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pai.config.model_registry import (ModelRegistryError, check_configured, load_registry, parse_registry, parse_size,
                                       registry_report)

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "config" / "models.yaml"


def reg(**sections):
    raw = {"llm": {"chat": sections.get("chat", [])}, "speech": {"stt": sections.get("stt", []), "tts": sections.get("tts", [])},
           "models": {"embeddings": sections.get("emb", [])}, "decision": sections.get("decision", [])}
    return parse_registry(raw)


def test_the_current_layout_and_the_older_nested_layout_both_load():
    m = {"name": "a/b", "tool_use": "enabled", "input_per_million": 1, "output_per_million": 2}
    flat = parse_registry({"llm": [m], "embeddings": [{"name": "e", "input_per_million": 0.1}], "speech": {"stt": [], "tts": []}})
    nested = parse_registry({"llm": {"chat": [m]}, "models": {"embeddings": [{"name": "e", "input_per_million": 0.1}]}})
    for r in (flat, nested):
        assert list(r.llm) == ["a/b"] and list(r.embeddings) == ["e"] and r.warnings == []


def chat(name="a/b", **kw):
    return {"name": name, "provider": "airouter", "tool_use": "enabled", "input_per_million": 1, "output_per_million": 2, **kw}


# ── sizes ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [("33K", 33_000), ("1.1M", 1_100_000), ("1M", 1_000_000), ("991K", 991_000),
                                            (4096, 4096), ("4096", 4096), (" 8k ", 8000), (None, None)])
def test_sizes(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize("bad", ["lots", "1.2.3M", "-5K", "5G", ""])
def test_bad_sizes(bad):
    with pytest.raises(ValueError):
        parse_size(bad)


# ── the shipped file ────────────────────────────────────────────────────────

def test_the_shipped_file_loads_with_no_structural_warnings():
    r = load_registry(SHIPPED)
    assert r.warnings == []
    assert len(r.llm) >= 20 and len(r.stt) >= 4 and len(r.tts) >= 2 and len(r.embeddings) >= 2 and r.decision
    assert all(m.price is not None for m in r.llm.values())              # every chat model is priced


def test_the_shipped_prices_are_the_published_ones():
    r = load_registry(SHIPPED)
    h = r.llm["anthropic/claude-haiku-5.5"]
    assert (h.input_per_million, h.output_per_million, h.context_length, h.tool_use) == (0.1, 0.5, 1_000_000, True)
    assert r.stt_price("microsoft/mai-transcribe-2") == 0.0017            # $0.10 per hour of audio
    assert r.tts_price("openai/tts-1") == 0.015                           # $15 per 1M characters
    assert r.llm["mistral/mistral-nemo"].tool_use is False


def test_llm_pricing_covers_chat_embedding_and_decision_models():
    p = load_registry(SHIPPED).llm_pricing()
    assert p["anthropic/claude-haiku-5.5"].input_per_million == 0.1
    assert "alibaba/qwen3-embedding-8b" in p and "typesafe-ai/jev-1.14.0" in p


# ── parsing and errors ──────────────────────────────────────────────────────

def test_every_problem_is_reported_at_once():
    with pytest.raises(ModelRegistryError) as e:
        reg(chat=[{"provider": "x"}, chat("n1", input_per_million=-1), chat("n2", context_length="lots"), chat("n3", tool_use="maybe"), "oops"])
    msg = str(e.value)
    assert "5 problem(s)" in msg and "n1" in msg and "n2" in msg and "n3" in msg and "expected a mapping" in msg


def test_duplicates_warn_and_the_later_entry_wins():
    r = reg(chat=[chat("dup", input_per_million=1), chat("dup", input_per_million=9)])
    assert r.llm["dup"].input_per_million == 9 and any("twice" in w for w in r.warnings)


def test_a_misnested_llm_section_is_flagged():
    r = parse_registry({"speech": {"stt": [], "tts": [], "llm": [chat()]}})
    assert r.llm == {} and any("nested under `speech`" in w for w in r.warnings)


def test_legacy_tts_per_1m_key_is_understood_and_empty_inputs_are_fine():
    r = reg(tts=[{"name": "t", "per_1m_chars_cost": 15}])
    assert r.tts_price("t") == 0.015
    assert parse_registry(None).llm == {} and parse_registry({}).tts == {}


def test_a_non_mapping_root_or_non_list_section_is_an_error():
    with pytest.raises(ModelRegistryError, match="top level"):
        parse_registry(["x"])
    with pytest.raises(ModelRegistryError, match="expected a list"):
        parse_registry({"llm": {"chat": {"a": 1}}})
    with pytest.raises(ModelRegistryError, match="expected a list"):
        parse_registry({"embeddings": {"a": 1}})


def test_a_missing_file_is_an_empty_registry_with_a_warning_but_a_broken_file_is_an_error(tmp_path):
    r = load_registry(tmp_path / "nope.yaml")
    assert r.llm == {} and "not found" in r.warnings[0]
    bad = tmp_path / "bad.yaml"
    bad.write_text("llm: [unclosed")
    with pytest.raises(ModelRegistryError, match="cannot read"):
        load_registry(bad)


def test_models_without_a_price_are_not_in_the_pricing_map():
    r = reg(chat=[{"name": "free-form"}, chat("priced")])
    assert list(r.llm_pricing()) == ["priced"]


# ── startup checks ──────────────────────────────────────────────────────────

def test_check_configured_flags_unknown_models_missing_prices_and_models_that_cannot_use_tools():
    r = reg(chat=[chat("ok/model"), chat("no/tools", tool_use="disabled"), {"name": "no/price", "tool_use": "enabled"}],
            stt=[{"name": "s", "per_minute_cost": 0.002}, {"name": "s-free"}], tts=[{"name": "t", "per_1k_chars_cost": 0.01}])
    out = check_configured(r, {"default": "ok/model", "planner": "no/tools", "chat": "no/tools", "agent": "missing/model", "x": "no/price"},
                           stt="s-free", tts="t")
    text = "\n".join(out)
    assert "planner: model 'no/tools' has tool_use disabled" in text
    assert "chat: model 'no/tools'" not in text                            # the chat role does not need tools
    assert "agent: model 'missing/model' is not in models.yaml" in text
    assert "x: model 'no/price' has no price" in text
    assert "stt: model 's-free' has no price" in text and "tts" not in text.replace("tts-", "")
    assert check_configured(r, {"default": "ok/model"}, stt="s", tts="t") == []


def test_an_empty_registry_has_nothing_to_compare_with():
    assert check_configured(parse_registry({}), {"planner": "anything"}, stt="x", tts="y") == []


def test_the_shipped_config_selects_models_the_registry_knows_for_the_voice_roles():
    r = load_registry(SHIPPED)
    cfg = yaml.safe_load((REPO / "config" / "config.yaml").read_text())
    a = cfg["voice"]["airouter"]
    assert r.stt_price(a["stt_model"]) is not None and r.tts_price(a["tts_model"]) is not None
    assert a["stt_cost_per_minute_usd"] is None and a["tts_cost_per_1k_chars_usd"] is None      # prices come from models.yaml
    assert cfg["models_file"] == "config/models.yaml"


# ── admin report ────────────────────────────────────────────────────────────

def test_report_lists_roles_all_models_cheapest_first_and_the_problems():
    r = reg(chat=[chat("pricey", input_per_million=5, output_per_million=5), chat("cheap", input_per_million=0.1, output_per_million=0.1),
                  {"name": "unpriced"}], stt=[{"name": "s", "per_minute_cost": 0.002}], tts=[{"name": "t", "per_1k_chars_cost": 0.01}])
    rep = registry_report(r, {"default": "cheap", "planner": "ghost", "stt": "s", "tts": "t"})
    assert [m["name"] for m in rep["llm"]] == ["cheap", "pricey", "unpriced"]
    roles = {x["role"]: x for x in rep["roles"]}
    assert roles["default"]["known"] and roles["default"]["input_per_million"] == 0.1 and roles["default"]["kind"] == "llm"
    assert roles["planner"]["known"] is False
    assert roles["stt"]["per_minute_cost"] == 0.002 and roles["tts"]["per_1k_chars_cost"] == 0.01
    assert any("ghost" in w for w in rep["warnings"])
