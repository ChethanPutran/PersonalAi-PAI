"""config/*.yaml must match src/pai/config/*: no key the models do not have (a typo used to be silently ignored)."""
import copy
import os
import shutil
from pathlib import Path

import pytest
import yaml

from pai.config import settings_admin as S
from pai.config.app import Config
from pai.config.strict import format_unknown, main, unknown_keys, validate_files

REPO = Path(__file__).resolve().parents[2]
SHIPPED = yaml.safe_load((REPO / "config" / "config.yaml").read_text())


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    for k in ("SECURITY__JWT_SECRET", "SECURITY__ENCRYPTION_KEY"):
        monkeypatch.setenv(k, "test")


# ── the shipped files ───────────────────────────────────────────────────────

def test_every_key_in_the_shipped_config_is_a_real_setting():
    assert unknown_keys(SHIPPED, Config) == []


def test_the_shipped_config_and_registry_validate():
    assert S.validate_settings(Config, {}, SHIPPED) == []
    assert main([]) == 0                                              # what CI runs: config.yaml, overrides.yaml, models.yaml


def test_the_settings_that_used_to_be_silently_ignored_now_take_effect():
    c = Config.model_validate(copy.deepcopy(SHIPPED))
    assert (c.orchestration.task_timeout_seconds, c.orchestration.max_task_retries, c.orchestration.retry_delay_seconds) == (300, 3, 2)
    assert c.executor.max_concurrent_tasks == 5
    assert c.security.authentication.refresh_token_expiration_days == 7
    assert str(c.plugins.plugin_local_artifact_path) == "plugin_registry/.packages" and c.storage.artifacts_directory == "./artifacts"


def test_no_other_yaml_files_are_left_that_the_loader_ignores():
    present = {p.name for p in (REPO / "config").glob("*.yaml")}
    assert {"config.yaml", "models.yaml"} <= present
    assert present <= {"config.yaml", "models.yaml", "overrides.yaml"}, \
        f"config/{sorted(present - {'config.yaml', 'models.yaml', 'overrides.yaml'})} is not loaded by pai.config.app"


# ── detection ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,suggestion", [
    ("orchestration.task_timeout", "task_timeout_seconds"),
    ("executor.max_concurrent_executions", "max_concurrent_tasks"),
    ("security.authentication.refresh_token_expire_days", "refresh_token_expiration_days"),
    ("storage.artifact_directory", "artifacts_directory"),
    ("plugins.local_artifact_path", "plugin_local_artifact_path"),
    ("voice.airouter.stt_modle", "stt_model"),
])
def test_a_typo_at_any_depth_is_reported_with_the_right_suggestion(path, suggestion):
    data = copy.deepcopy(SHIPPED)
    S.set_path(data, path, 1)
    assert unknown_keys(data, Config) == [(path, suggestion)]


def test_a_setting_that_does_not_exist_is_reported_without_a_guess():
    data = copy.deepcopy(SHIPPED)
    data["app"]["debug"] = True
    data["totally_new_section"] = {"x": 1}
    found = dict(unknown_keys(data, Config))
    assert found == {"app.debug": None, "totally_new_section": None} and "no such setting" in format_unknown(list(found.items()))


def test_aliases_free_form_maps_and_the_env_secrets_are_not_false_positives():
    data = copy.deepcopy(SHIPPED)
    assert data["llm_provider"].get("model")                              # config.yaml says `model:` (accepted: it is an alias of model_name)
    data["llm_routes"]["any_new_role"] = "x/y"                            # free-form map
    data["llm_pricing"] = {"a/b": {"input_per_million": 1, "output_per_million": 2}}
    data["executor"]["executors"] = {"gpu": "http://gpu:9000"}
    data["secrets"] = {"anything": 1}                                     # filled from .env, never from YAML
    assert unknown_keys(data, Config) == []


def test_the_settings_editor_and_the_strict_check_agree_on_what_a_setting_is():
    known = S.known_paths(Config.model_json_schema())
    leaves = [p for p in known if not any(k.startswith(p + ".") for k in known)]
    data: dict = {}
    for p in leaves:
        S.set_path(data, p, None)
    assert unknown_keys(data, Config) == []


# ── the loader refuses them ─────────────────────────────────────────────────

def test_config_validation_fails_loudly_on_an_unknown_setting():
    data = copy.deepcopy(SHIPPED)
    data["orchestration"]["task_timeout"] = 5
    with pytest.raises(ValueError) as e:
        Config.model_validate(data)
    assert "unknown setting(s)" in str(e.value) and "orchestration.task_timeout" in str(e.value) and "task_timeout_seconds" in str(e.value)


def test_startup_refuses_a_typo_in_the_saved_admin_settings_too(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    for f in ("config.yaml", "models.yaml"):
        shutil.copy(REPO / "config" / f, tmp_path / "config" / f)
    monkeypatch.chdir(tmp_path)
    Config()                                                              # fine without overrides
    (tmp_path / "config" / "overrides.yaml").write_text("voice:\n  airouter:\n    stt_modle: x/y\n")
    with pytest.raises(ValueError, match="voice.airouter.stt_modle"):
        Config()


# ── the command ─────────────────────────────────────────────────────────────

def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return p


def test_command_reports_typos_and_exits_nonzero(tmp_path, capsys):
    bad = copy.deepcopy(SHIPPED)
    bad["orchestration"]["task_timeout"] = 5
    assert validate_files([write(tmp_path, "c.yaml", bad)]) == 1
    out = capsys.readouterr().out
    assert "ERR" in out and "orchestration.task_timeout" in out and "did you mean 'task_timeout_seconds'" in out


def test_command_reports_invalid_values_and_broken_yaml_and_skips_missing_optional_files(tmp_path, capsys):
    wrong = copy.deepcopy(SHIPPED)
    wrong["admin"]["history_retention_days"] = 0
    assert validate_files([write(tmp_path, "c.yaml", wrong)]) == 1
    assert "history_retention_days" in capsys.readouterr().out
    assert validate_files([write(tmp_path, "broken.yaml", "a: [unclosed")]) == 1
    assert validate_files([write(tmp_path, "ok.yaml", SHIPPED), tmp_path / "overrides.yaml"]) == 0       # missing overrides: fine


def test_command_checks_files_in_order_so_overrides_can_fix_nothing_but_add(tmp_path):
    over = {"voice": {"airouter": {"voice": "nova"}}}
    assert validate_files([write(tmp_path, "c.yaml", SHIPPED), write(tmp_path, "o.yaml", over)]) == 0
    assert validate_files([write(tmp_path, "c.yaml", SHIPPED), write(tmp_path, "o2.yaml", {"voice": {"airouter": {"nope": 1}}})]) == 1


def test_command_validates_the_model_registry(tmp_path, capsys):
    reg = write(tmp_path, "models.yaml", {"llm": [{"name": "a/b", "input_per_million": -1}]})
    assert validate_files([write(tmp_path, "c.yaml", SHIPPED)], reg) == 1
    assert "input_per_million" in capsys.readouterr().out


# ── aliases: one canonical path per setting ─────────────────────────────────

def test_canonicalize_renames_aliased_keys_so_every_view_uses_the_field_name():
    from pai.config.strict import canonicalize
    out = canonicalize(SHIPPED, Config)
    assert "model" not in out["llm_provider"] and out["llm_provider"]["model_name"] == SHIPPED["llm_provider"]["model"]
    assert canonicalize({"llm_provider": {"model": "a", "model_name": "b"}}, Config)["llm_provider"] == {"model_name": "b"}   # explicit wins
    assert canonicalize({"llm_routes": {"model": "x"}, "bogus": {"model": 1}}, Config) == {"llm_routes": {"model": "x"}, "bogus": {"model": 1}}


def test_the_settings_snapshot_uses_field_names_and_the_override_beats_an_aliased_base():
    snap = S.snapshot(Config, {}, SHIPPED, {})
    assert snap["base"]["llm_provider"]["model_name"] == SHIPPED["llm_provider"]["model"]
    over = {"llm_provider": {"model_name": "x/override"}}
    assert S.validate_settings(Config, SHIPPED, over) == []
    merged = S.deep_merge(__import__("pai.config.strict", fromlist=["x"]).canonicalize(SHIPPED, Config), over)
    assert Config.model_validate(merged).llm_provider.model_name == "x/override"
    assert Config.model_json_schema()["$defs"]["LLMProviderConfig"]["properties"]["model_name"]["x-open-enum"] is True
