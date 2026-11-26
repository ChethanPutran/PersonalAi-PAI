"""The device config file: precedence, validation, and the remote-access starting values."""
import logging
import os
import stat

import pytest

from pai_device.agent.agent import DeviceAgent
from pai_device.config import DeviceConfig
from pai_device.config_file import ConfigFileError, find_config_file, load_config_file

ENV_KEYS = ("PAI_BACKEND_URL", "PAI_SERVER_EMAIL", "PAI_USERNAME", "PAI_SERVER_PASSWORD", "PAI_DEVICE_NAME",
            "PAI_DEVICE_TYPE", "PAI_HOME", "PAI_STATE_DIR", "PAI_PLUGIN_DIR", "PAI_LOG_DIR", "PAI_DEVICE_CONFIG",
            "PAI_REMOTE_INSTALL", "PAI_REMOTE_INVOKE")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)                           # no stray ./pai-device.yaml
    monkeypatch.setenv("HOME", str(tmp_path / "home"))    # no stray ~/.pai/pai-device.yaml


def write(path, text):
    path.write_text(text)
    return str(path)


FULL = """
backend: {url: "http://backend:9000"}
account: {email: "me@example.com", password: "from-file"}
device: {name: "worker-7", type: "desktop"}
paths: {home: "/data/pai", log_dir: "/var/log/pai"}
timing: {heartbeat_interval: 5, reconcile_interval: 30, reconnect_min: 2, reconnect_max: 9, progress_keepalive: 7}
remote_access: {install: true, invoke: false}
"""


def test_everything_comes_from_the_file(tmp_path):
    cfg = DeviceConfig.load(write(tmp_path / "c.yaml", FULL))
    assert (cfg.backend_url, cfg.email, cfg.password) == ("http://backend:9000", "me@example.com", "from-file")
    assert (cfg.device_name, cfg.device_type) == ("worker-7", "desktop")
    assert str(cfg.state_dir) == "/data/pai/state" and str(cfg.plugin_dir) == "/data/pai/plugins"
    assert str(cfg.log_dir) == "/var/log/pai"
    assert (cfg.heartbeat_interval, cfg.reconcile_interval, cfg.reconnect_min, cfg.reconnect_max) == (5, 30, 2, 9)
    assert cfg.progress_keepalive_seconds == 7
    assert (cfg.remote_access_install, cfg.remote_access_invoke) == (True, False)
    assert cfg.config_file == tmp_path / "c.yaml"


def test_environment_beats_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PAI_BACKEND_URL", "http://env:1")
    monkeypatch.setenv("PAI_SERVER_PASSWORD", "from-env")
    monkeypatch.setenv("PAI_DEVICE_NAME", "env-name")
    monkeypatch.setenv("PAI_REMOTE_INSTALL", "false")
    monkeypatch.setenv("PAI_REMOTE_INVOKE", "yes")
    monkeypatch.setenv("PAI_STATE_DIR", "/elsewhere")
    cfg = DeviceConfig.load(write(tmp_path / "c.yaml", FULL))
    assert (cfg.backend_url, cfg.password, cfg.device_name) == ("http://env:1", "from-env", "env-name")
    assert (cfg.remote_access_install, cfg.remote_access_invoke) == (False, True)     # env flips both
    assert str(cfg.state_dir) == "/elsewhere" and str(cfg.plugin_dir) == "/data/pai/plugins"


def test_no_file_means_env_only_and_fail_closed_defaults(monkeypatch):
    monkeypatch.setenv("PAI_BACKEND_URL", "http://x")
    monkeypatch.setenv("PAI_SERVER_EMAIL", "a@b.c")
    monkeypatch.setenv("PAI_SERVER_PASSWORD", "pw")
    cfg = DeviceConfig.from_env()                         # the old entry point still works
    assert cfg.config_file is None
    assert (cfg.remote_access_install, cfg.remote_access_invoke) == (False, False)
    assert (cfg.heartbeat_interval, cfg.progress_keepalive_seconds) == (20.0, 20.0)


def test_missing_required_values_say_where_to_set_them(tmp_path):
    with pytest.raises(RuntimeError) as err:
        DeviceConfig.load(write(tmp_path / "c.yaml", "device: {name: x}\n"))
    msg = str(err.value)
    assert "PAI_BACKEND_URL or backend.url" in msg and "account.email" in msg and "account.password" in msg
    assert "c.yaml" in msg


def test_the_agent_starts_from_the_configured_remote_access_values(tmp_path):
    base = dict(backend_url="http://x", email="e", password="p", device_name="d",
                state_dir=tmp_path / "s", plugin_dir=tmp_path / "p")
    closed = DeviceAgent(DeviceConfig(**base))
    assert closed.remote_access == {"install": False, "invoke": False}            # the default: fail closed
    configured = DeviceAgent(DeviceConfig(**base, remote_access_install=True, remote_access_invoke=True))
    assert configured.remote_access == {"install": True, "invoke": True}
    configured._apply_remote_access({"install": False, "invoke": False})          # the backend's report replaces it
    assert configured.remote_access == {"install": False, "invoke": False}


def test_a_typo_or_bad_value_is_an_error_naming_the_key(tmp_path):
    for text, needle in (("remote_acces: {install: true}\n", "remote_acces"),
                         ("remote_access: {instal: true}\n", "remote_access.instal"),
                         ("timing: {heartbeat_interval: -1}\n", "timing.heartbeat_interval"),
                         ("remote_access: {install: maybe}\n", "remote_access.install")):
        with pytest.raises(ConfigFileError) as err:
            load_config_file(__import__("pathlib").Path(write(tmp_path / "bad.yaml", text)))
        assert needle in str(err.value)
    with pytest.raises(ConfigFileError, match="not valid YAML"):
        load_config_file(__import__("pathlib").Path(write(tmp_path / "bad.yaml", "a: [unclosed\n")))
    with pytest.raises(ConfigFileError, match="mapping"):
        load_config_file(__import__("pathlib").Path(write(tmp_path / "bad.yaml", "- just\n- a list\n")))


def test_an_empty_file_is_all_defaults(tmp_path):
    f = load_config_file(__import__("pathlib").Path(write(tmp_path / "e.yaml", "")))
    assert f.remote_access.install is False and f.timing.heartbeat_interval == 20


def test_lookup_order_and_a_requested_file_must_exist(tmp_path, monkeypatch):
    assert find_config_file() is None
    home_file = tmp_path / "home" / ".pai" / "pai-device.yaml"
    home_file.parent.mkdir(parents=True)
    home_file.write_text("")
    assert find_config_file() == home_file
    (tmp_path / "pai-device.yaml").write_text("")                                  # ./ beats ~/.pai
    assert find_config_file() == tmp_path / "pai-device.yaml"
    monkeypatch.setenv("PAI_DEVICE_CONFIG", str(home_file))                       # env beats ./
    assert find_config_file() == home_file
    assert find_config_file(str(tmp_path / "pai-device.yaml")) == tmp_path / "pai-device.yaml"   # argument beats env
    with pytest.raises(ConfigFileError, match="not found"):
        find_config_file(str(tmp_path / "missing.yaml"))


def test_a_password_in_a_readable_file_gets_a_warning(tmp_path, caplog):
    path = tmp_path / "c.yaml"
    path.write_text('account: {password: "secret"}\n')
    path.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        load_config_file(path)
    assert "chmod 600" in caplog.text
    caplog.clear()
    path.chmod(0o600)
    with caplog.at_level(logging.WARNING):
        load_config_file(path)
    assert "chmod 600" not in caplog.text


def test_the_shipped_default_file_is_valid_and_fail_closed():
    shipped = __import__("pathlib").Path(__file__).resolve().parents[1] / "pai-device.yaml"
    f = load_config_file(shipped)
    assert f.remote_access.install is False and f.remote_access.invoke is False
    assert f.backend.url == "http://localhost:8000" and f.account.password is None
