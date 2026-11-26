"""The device enforces the owner's remote-access switches itself (fail closed)."""
import pytest

from pai_device.agent.agent import DeviceAgent
from pai_device.config import DeviceConfig


@pytest.fixture
def agent(tmp_path):
    cfg = DeviceConfig(backend_url="http://x", email="e", password="p", device_name="d",
                       state_dir=tmp_path / "s", plugin_dir=tmp_path / "p")
    a = DeviceAgent(cfg)
    a.device_id = "B"
    sent, reported = [], []

    async def ws_send(frame):
        sent.append(frame)

    async def report_install(pid, body):
        reported.append(("install", pid, body))

    async def report_uninstall(pid, body):
        reported.append(("uninstall", pid, body))

    a.ws.send = ws_send
    a.api.report_install = report_install
    a.api.report_uninstall = report_uninstall
    a._sent, a._reported = sent, reported
    return a


def test_nothing_is_allowed_until_the_backend_says_so(agent):
    assert agent.remote_access == {"install": False, "invoke": False}
    agent._apply_remote_access({"install": True, "invoke": False})
    assert agent.remote_access == {"install": True, "invoke": False}
    agent._apply_remote_access("garbage")            # ignored, never widens access
    assert agent.remote_access == {"install": True, "invoke": False}


@pytest.mark.asyncio
async def test_remote_install_is_refused_when_off_and_reported_clearly(agent):
    msg = {"type": "plugin.install", "operation_id": "op1", "requested_by_device": "A",
           "plugin": {"id": "camera", "version": "1.0.0"}}
    called = []

    async def fake_install(*a, **k):
        called.append(a)
    agent._install_plugin = fake_install
    await agent._handle_ws_message(msg)
    assert not called                                           # nothing was downloaded or installed
    assert agent._sent[0]["status"] == "failed" and agent._sent[0]["error_code"] == "remote_install_disabled"
    kind, pid, body = agent._reported[0]
    assert body["success"] is False and body["operation_id"] == "op1"


@pytest.mark.asyncio
async def test_remote_install_runs_when_on_and_reports_each_stage(agent):
    agent._apply_remote_access({"install": True})

    async def fake_install(pid, ver, stage=None):
        for s in ("downloading", "verifying", "installing"):
            await stage(s)
    agent._install_plugin = fake_install
    agent._sha_for = lambda pid: "abc"
    await agent._handle_ws_message({"type": "plugin.install", "operation_id": "op1",
                                    "requested_by_device": "A",
                                    "plugin": {"id": "camera", "version": "1.0.0"}})
    assert [f["status"] for f in agent._sent] == ["downloading", "verifying", "installing"]
    assert agent._reported[0][2]["success"] is True and agent._reported[0][2]["operation_id"] == "op1"


@pytest.mark.asyncio
async def test_own_install_is_local_and_needs_no_switch(agent):
    ran = []

    async def fake_install(pid, ver, stage=None):
        ran.append(pid)
    agent._install_plugin = fake_install
    agent._sha_for = lambda pid: "abc"
    await agent._handle_ws_message({"type": "plugin.install", "requested_by_device": "B",
                                    "plugin": {"id": "camera", "version": "1"}})
    assert ran == ["camera"]


@pytest.mark.asyncio
async def test_remote_invoke_is_refused_when_off_and_local_is_not(agent):
    ran = []

    class Plugins:
        async def invoke(self, cap, params):
            ran.append(cap)
            return {"ok": 1}
    agent.plugins = Plugins()

    await agent._handle_ws_message({"type": "invoke", "request_id": "r1", "capability": "camera.capture",
                                    "source_device_id": "A"})
    assert not ran and "turned off" in agent._sent[-1]["error"]

    await agent._handle_ws_message({"type": "invoke", "request_id": "r2", "capability": "camera.capture",
                                    "source_device_id": None})          # web / schedule: remote too
    assert not ran

    await agent._handle_ws_message({"type": "invoke", "request_id": "r3", "capability": "camera.capture",
                                    "source_device_id": "B"})
    assert ran == ["camera.capture"]


@pytest.mark.asyncio
async def test_pushed_operation_updates_are_ignored_quietly(agent, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        await agent._handle_ws_message({"type": "plugin.operation", "operation": {"id": "op1", "status": "installing"}})
    assert not [r for r in caplog.records if "Unhandled" in r.getMessage()]
