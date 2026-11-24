"""Settings editor (admin panel): overrides file, validation, secrets, pending changes."""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from pai.config import settings_admin as S
from pai.config.app import Config

REPO = Path(__file__).resolve().parents[2]
SCHEMA = Config.model_json_schema()
BASE = yaml.safe_load((REPO / "config" / "config.yaml").read_text())


# ── paths and dicts ─────────────────────────────────────────────────────────

def test_get_set_del_path_and_pruning():
    d = {}
    S.set_path(d, "a.b.c", 1)
    S.set_path(d, "a.x", 2)
    assert d == {"a": {"b": {"c": 1}, "x": 2}} and S.get_path(d, "a.b.c") == 1 and S.get_path(d, "a.nope", "dflt") == "dflt"
    assert S.del_path(d, "a.b.c") and d == {"a": {"x": 2}}                  # the emptied parent "b" is pruned
    assert not S.del_path(d, "a.b.c") and not S.del_path(d, "z.y")
    assert S.del_path(d, "a.x") and d == {}


def test_deep_merge_replaces_lists_and_keeps_siblings_and_does_not_mutate_inputs():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    over = {"a": {"y": [9]}, "c": 3}
    out = S.deep_merge(base, over)
    assert out == {"a": {"x": 1, "y": [9]}, "b": 1, "c": 3} and base["a"]["y"] == [1, 2]


def test_flatten_treats_lists_and_empty_dicts_as_leaves():
    assert S.flatten({"a": {"b": 1}, "c": [1, 2], "d": {}}) == {"a.b": 1, "c": [1, 2], "d": {}}


# ── secrets ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["redis.password", "llm_provider.api_key", "voice.airouter.api_key", "voice.api_key", "secrets.X",
                                  "secrets", "security.jwt_secret", "memory.knowledge_graph.password", "x.encryption_key"])
def test_credentials_are_secret(path):
    assert S.is_secret_path(path)


@pytest.mark.parametrize("path", ["llm_provider.max_tokens", "api.cors.allow_credentials", "security.encryption.key_length",
                                  "prompts.guard.redact_secrets", "security.authentication.access_token_expire_minutes",
                                  "voice.airouter.voice", "llm_routes.planner"])
def test_ordinary_settings_are_not_secret(path):
    assert not S.is_secret_path(path)


def test_the_real_schemas_secrets_are_found_and_stripped():
    assert {"redis.password", "llm_provider.api_key", "voice.airouter.api_key", "memory.knowledge_graph.password"} <= set(S.secret_paths(SCHEMA))
    out = S.strip_secrets({"redis": {"password": "p", "host": "h"}, "secrets": {"X": 1}, "voice": {"airouter": {"api_key": "k", "voice": "v"}}})
    assert out == {"redis": {"host": "h"}, "voice": {"airouter": {"voice": "v"}}}


def test_known_paths_cover_nested_settings_and_exclude_secrets():
    known = S.known_paths(SCHEMA)
    assert {"voice.airouter.voice", "llm_routes", "admin.history_retention_days", "llm_provider.model_name"} <= known
    assert "secrets" not in known and not any(p.startswith("secrets.") for p in known)


# ── files ───────────────────────────────────────────────────────────────────

def test_overrides_round_trip_keep_a_backup_and_an_empty_set_removes_the_file(tmp_path):
    p = tmp_path / "config" / "overrides.yaml"
    S.write_overrides(p, {"voice": {"airouter": {"voice": "nova"}}})
    assert S.load_yaml_dict(p) == {"voice": {"airouter": {"voice": "nova"}}} and p.read_text().startswith("# Settings saved from the admin panel")
    S.write_overrides(p, {"llm_routes": {"planner": "a/b"}})
    assert S.load_yaml_dict(p.with_suffix(".yaml.bak")) == {"voice": {"airouter": {"voice": "nova"}}}      # the previous version
    S.write_overrides(p, {})
    assert not p.exists() and S.load_yaml_dict(p) == {}
    assert not [x for x in p.parent.iterdir() if ".tmp" in x.name or x.name.startswith("overrides.yaml.") and x.suffix not in (".bak",)]


def test_a_corrupt_overrides_file_is_a_clear_error_not_a_crash(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text("a: [unclosed")
    with pytest.raises(S.SettingsError, match="cannot read"):
        S.load_yaml_dict(p)
    p.write_text("- just\n- a list\n")
    with pytest.raises(S.SettingsError, match="top level"):
        S.load_yaml_dict(p)


# ── validation ──────────────────────────────────────────────────────────────

def apply(set_values=None, reset=(), overrides=None):
    return S.apply_changes(Config, SCHEMA, BASE, overrides or {}, set_values or {}, reset)


def test_a_valid_change_is_returned_as_the_new_overrides():
    assert apply({"voice.airouter.voice": "nova", "admin.history_retention_days": 14}) == \
        {"voice": {"airouter": {"voice": "nova"}}, "admin": {"history_retention_days": 14}}


@pytest.mark.parametrize("path,value,fragment", [
    ("admin.history_retention_days", 0, "greater than or equal"),
    ("admin.history_retention_days", "many", "integer"),
    ("api.port", "abc", ""),
    ("llm_provider.provider", "not-a-provider", ""),
    ("voice.stt", "teleport", ""),
])
def test_invalid_values_are_refused_with_the_path_and_nothing_is_returned(path, value, fragment):
    with pytest.raises(S.SettingsError) as e:
        apply({path: value})
    assert e.value.status == 422 and any(path.replace(".", ".") in x["path"] for x in e.value.errors), e.value.errors
    assert fragment in json.dumps(e.value.errors)


def test_unknown_and_secret_settings_cannot_be_set_or_reset():
    with pytest.raises(S.SettingsError) as e:
        apply({"no.such.setting": 1, "redis.password": "x", "secrets.SECURITY__JWT_SECRET": "x"}, reset=["llm_provider.api_key"])
    msgs = {x["path"]: x["message"] for x in e.value.errors}
    assert msgs["no.such.setting"] == "unknown setting" and "live in .env" in msgs["redis.password"]
    assert "live in .env" in msgs["secrets.SECURITY__JWT_SECRET"] and "live in .env" in msgs["llm_provider.api_key"]


def test_reset_removes_a_saved_value_and_a_bad_combination_is_caught_as_a_whole():
    assert apply(reset=["voice.airouter.voice"], overrides={"voice": {"airouter": {"voice": "nova"}}, "admin": {"history_retention_days": 3}}) == \
        {"admin": {"history_retention_days": 3}}
    with pytest.raises(S.SettingsError):                       # both values are valid alone, an unknown nested key is not
        apply({"voice": {"airouter": {"bogus": 1}}})


# ── pending / snapshot ──────────────────────────────────────────────────────

def test_pending_lists_saved_values_that_differ_from_what_is_running():
    running = {"voice": {"airouter": {"voice": "alloy"}}, "admin": {"history_retention_days": 7}}
    saved = {"voice": {"airouter": {"voice": "nova"}}, "admin": {"history_retention_days": 7}}
    assert S.pending(saved, running) == [{"path": "voice.airouter.voice", "saved": "nova", "running": "alloy"}]
    assert S.pending({}, running) == []


def test_snapshot_never_contains_secret_values():
    running = {"redis": {"password": "TOPSECRET", "host": "h"}, "voice": {"airouter": {"api_key": "KEY123", "voice": "v"}}}
    snap = S.snapshot(Config, running, {"redis": {"password": "TOPSECRET"}}, {"voice": {"airouter": {"api_key": "KEY123"}}})
    text = json.dumps(snap)
    assert "TOPSECRET" not in text and "KEY123" not in text and "secrets" not in snap["schema"]["properties"]
    assert snap["values"]["redis"] == {"host": "h"} and "redis.password" in snap["secret_paths"]


# ── the saved file really changes what Config loads ─────────────────────────

def test_config_loads_overrides_deep_merged_over_config_yaml(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "config" / "config.yaml", tmp_path / "config" / "config.yaml")
    shutil.copy(REPO / "config" / "models.yaml", tmp_path / "config" / "models.yaml")
    for k in ("SECURITY__JWT_SECRET", "SECURITY__ENCRYPTION_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.chdir(tmp_path)
    before = Config()
    (tmp_path / "config" / "overrides.yaml").write_text("llm_routes:\n  planner: x/y\nvoice:\n  airouter:\n    voice: nova\n")
    after = Config()
    assert after.llm_routes["planner"] == "x/y" and after.llm_routes["chat"] == before.llm_routes["chat"]      # siblings kept
    assert after.voice.airouter.voice == "nova" and after.voice.airouter.stt_model == before.voice.airouter.stt_model


# ── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture
def isolated_settings(harness, tmp_path):
    ctx = harness.ctx
    old = ctx.settings_overrides_path
    ctx.settings_overrides_path = str(tmp_path / "overrides.yaml")
    try:
        yield ctx
    finally:
        ctx.settings_overrides_path = old


def test_settings_endpoints_are_admin_only(harness):
    c = harness.client
    for method, path, kw in [("get", "/api/v1/system/settings", {}), ("put", "/api/v1/system/settings", {"json": {}}),
                             ("post", "/api/v1/system/reload", {})]:
        assert getattr(c, method)(path, **kw).status_code == 401
        assert getattr(c, method)(path, headers=harness.user["h"], **kw).status_code == 403


def test_get_settings_returns_schema_values_and_no_secrets(harness, isolated_settings):
    r = harness.client.get("/api/v1/system/settings", headers=harness.H)
    assert r.status_code == 200
    body = r.json()
    assert {"schema", "values", "base", "overrides", "secret_paths", "pending"} <= set(body)
    assert "secrets" not in body["schema"]["properties"] and "llm_routes" in body["schema"]["properties"]
    jwt = os.environ.get("SECURITY__JWT_SECRET")
    assert not jwt or jwt not in r.text
    assert "redis.password" in body["secret_paths"] and "password" not in body["values"].get("redis", {})


def test_put_saves_validated_changes_and_reports_them_as_pending(harness, isolated_settings):
    c, H = harness.client, harness.H
    r = c.put("/api/v1/system/settings", json={"set": {"admin.history_retention_days": 21, "voice.airouter.voice": "nova"}}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert {x["path"] for x in body["changed"]} == {"admin.history_retention_days", "voice.airouter.voice"}
    assert body["overrides"]["admin"]["history_retention_days"] == 21
    assert {p["path"] for p in body["pending"]} == {"admin.history_retention_days", "voice.airouter.voice"}
    assert S.load_yaml_dict(isolated_settings.settings_overrides_path)["voice"]["airouter"]["voice"] == "nova"

    r = c.put("/api/v1/system/settings", json={"reset": ["voice.airouter.voice"]}, headers=H)
    assert "voice" not in r.json()["overrides"] and [x["path"] for x in r.json()["changed"]] == ["voice.airouter.voice"]
    c.put("/api/v1/system/settings", json={"reset": ["admin.history_retention_days"]}, headers=H)
    assert not Path(isolated_settings.settings_overrides_path).exists()           # nothing left -> no file


def test_put_refuses_bad_values_and_secrets_and_writes_nothing(harness, isolated_settings):
    c, H = harness.client, harness.H
    for body, status in [({"set": {"admin.history_retention_days": 0}}, 422), ({"set": {"nope.nothing": 1}}, 422),
                         ({"set": {"redis.password": "x"}}, 422), ({"set": {"bad path!": 1}}, 422), ({"reset": ["../etc"]}, 422)]:
        r = c.put("/api/v1/system/settings", json=body, headers=H)
        assert r.status_code == status, (body, r.text)
    r = c.put("/api/v1/system/settings", json={"set": {"admin.history_retention_days": 0, "voice.airouter.voice": "nova"}}, headers=H)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["path"].endswith("history_retention_days")
    assert not Path(isolated_settings.settings_overrides_path).exists()           # the valid half was NOT saved either


def test_unknown_nested_keys_are_refused_but_free_form_maps_and_nested_objects_work():
    with pytest.raises(S.SettingsError) as e:
        apply({"voice": {"airouter": {"bogus": 1}}})
    assert [x["path"] for x in e.value.errors] == ["voice.airouter.bogus"]
    # a whole nested object may be replaced as long as every key in it is real...
    assert apply({"voice.airouter": {"voice": "nova", "tts_speed": 1.1}}) == {"voice": {"airouter": {"voice": "nova", "tts_speed": 1.1}}}
    # ...and maps with arbitrary keys (llm_routes, llm_pricing) take any key
    assert apply({"llm_routes": {"planner": "a/b", "custom_role": "c/d"}})["llm_routes"]["custom_role"] == "c/d"
    assert apply({"llm_pricing": {"x/y": {"input_per_million": 1, "output_per_million": 2}}})["llm_pricing"]["x/y"]["input_per_million"] == 1
    with pytest.raises(S.SettingsError, match="refused"):                       # a secret smuggled inside an object is still refused
        apply({"voice": {"airouter": {"api_key": "sk-leak"}}})
    assert S.freeform_paths(SCHEMA) >= {"llm_routes", "llm_pricing"}
