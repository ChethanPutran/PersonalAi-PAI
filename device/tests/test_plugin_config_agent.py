"""The agent applies, persists, syncs and ENFORCES per-plugin settings."""
import os
from types import SimpleNamespace

import pytest

from pai_device.agent.agent import DeviceAgent
from pai_device.config import DeviceConfig


@pytest.fixture
def agent(tmp_path):
    cfg = DeviceConfig(backend_url="http://x", email="e", password="p", device_name="d",
                       state_dir=tmp_path / "s", plugin_dir=tmp_path / "p")
    a = DeviceAgent(cfg)
    a.device_id = "B"
    a._sent = []

    async def ws_send(frame):
        a._sent.append(frame)
    a.ws.send = ws_send
    a._invoked = []

    async def fake_invoke(cap, params):
        a._invoked.append((cap, params))
        return {"ok": True}
    a.plugins.invoke = fake_invoke
    a.plugins.find_by_capability = lambda cap: SimpleNamespace(id="files") if cap.startswith("files.") else None
    return a


def cfg_for(tmp_path, **kw):
    d = tmp_path / "docs"
    d.mkdir(exist_ok=True)
    return {"allowedPaths": [str(d)], "deniedPaths": [], "readOnly": False, **kw}, d


@pytest.mark.asyncio
async def test_config_frame_from_this_device_is_applied_persisted_and_reloaded(agent, tmp_path, monkeypatch):
    monkeypatch.delenv("PAI_FILES_ROOTS", raising=False)
    conf, d = cfg_for(tmp_path)
    await agent._handle_ws_message({"type": "plugin.config", "plugin_id": "files", "device_id": "B",
                                    "requested_by_device": "B", "config": conf})
    assert agent.plugin_configs["files"] == conf
    assert os.environ["PAI_FILES_ROOTS"] == os.path.realpath(d)
    again = DeviceAgent(agent.config)                       # a restart: enforced before any network
    assert again.plugin_configs["files"] == conf


@pytest.mark.asyncio
async def test_remote_config_change_needs_the_install_switch(agent, tmp_path):
    conf, _ = cfg_for(tmp_path)
    frame = {"type": "plugin.config", "plugin_id": "files", "device_id": "B",
             "requested_by_device": "A", "config": conf}
    await agent._handle_ws_message(frame)
    assert "files" not in agent.plugin_configs              # refused (fail closed)
    await agent._handle_ws_message({**frame, "requested_by_device": None})
    assert "files" not in agent.plugin_configs              # the web counts as remote
    agent._apply_remote_access({"install": True})
    await agent._handle_ws_message(frame)
    assert agent.plugin_configs["files"] == conf


@pytest.mark.asyncio
async def test_frame_for_another_device_or_malformed_is_ignored(agent, tmp_path):
    conf, _ = cfg_for(tmp_path)
    await agent._handle_ws_message({"type": "plugin.config", "plugin_id": "files", "device_id": "Z",
                                    "requested_by_device": "B", "config": conf})
    await agent._handle_ws_message({"type": "plugin.config", "plugin_id": "files", "device_id": "B",
                                    "requested_by_device": "B", "config": "nope"})
    assert agent.plugin_configs == {}


@pytest.mark.asyncio
async def test_install_config_is_applied_only_when_the_remote_rule_passes(agent, tmp_path):
    conf, _ = cfg_for(tmp_path)

    async def fake_install(*a, **k):
        pass

    async def report(*a, **k):
        pass
    agent._install_plugin = fake_install
    agent.api.report_install = report
    msg = {"type": "plugin.install", "operation_id": "o", "requested_by_device": "A",
           "plugin": {"id": "files", "version": "1.1.0"}, "config": conf}
    await agent._handle_ws_message(msg)
    assert "files" not in agent.plugin_configs
    agent._apply_remote_access({"install": True})
    await agent._handle_ws_message(msg)
    assert agent.plugin_configs["files"] == conf


@pytest.mark.asyncio
async def test_invoke_is_refused_outside_allowed_and_runs_inside(agent, tmp_path):
    conf, d = cfg_for(tmp_path)
    agent._set_plugin_config("files", conf)
    await agent._handle_task({"request_id": "r1", "capability": "files.read",
                              "source_device_id": "B", "parameters": {"path": str(tmp_path / "other")}})
    assert agent._invoked == []
    assert agent._sent[-1]["result"]["ok"] is False and "outside" in agent._sent[-1]["result"]["error"]
    await agent._handle_task({"request_id": "r2", "capability": "files.read",
                              "source_device_id": "B", "parameters": {"path": str(d / "a.txt")}})
    assert agent._invoked and agent._sent[-1]["result"]["ok"] is True


@pytest.mark.asyncio
async def test_read_only_blocks_write(agent, tmp_path):
    conf, d = cfg_for(tmp_path, readOnly=True)
    agent._set_plugin_config("files", conf)
    await agent._handle_task({"request_id": "r", "capability": "files.write",
                              "source_device_id": "B", "parameters": {"path": str(d / "a")}})
    assert agent._invoked == [] and "read-only" in agent._sent[-1]["result"]["error"]


@pytest.mark.asyncio
async def test_plugins_without_settings_are_untouched(agent):
    agent.plugins.find_by_capability = lambda cap: SimpleNamespace(id="camera")
    await agent._handle_task({"request_id": "r", "capability": "camera.capture",
                              "source_device_id": "B", "parameters": {"path": "/anything"}})
    assert agent._invoked


@pytest.mark.asyncio
async def test_sync_on_connect_fetches_effective_config_and_survives_failures(agent, tmp_path):
    conf, _ = cfg_for(tmp_path)
    agent.plugins.list_ids = lambda: ["files", "broken"]

    async def get_cfg(device_id, plugin_id):
        if plugin_id == "broken":
            raise RuntimeError("offline")
        return {"effective": conf}
    agent.api.get_plugin_config = get_cfg
    await agent._sync_plugin_configs()
    assert agent.plugin_configs == {"files": conf}


@pytest.mark.asyncio
async def test_uninstall_forgets_settings(agent, tmp_path):
    conf, _ = cfg_for(tmp_path)
    agent._set_plugin_config("files", conf)

    async def un(pid):
        pass

    async def rep(*a, **k):
        pass
    agent.plugins.uninstall = un
    agent.api.report_uninstall = rep
    await agent._handle_ws_message({"type": "plugin.uninstall", "plugin_id": "files",
                                    "requested_by_device": "B", "operation_id": "o"})
    assert "files" not in agent.plugin_configs and "files" not in agent.config_store.load_all()
