"""An install must not be undone by the sync loop that runs while its dependencies install."""
import asyncio

import pytest

from pai_device.agent.agent import DeviceAgent
from pai_device.config import DeviceConfig


@pytest.mark.asyncio
async def test_sync_waits_for_a_running_install_and_never_interleaves(tmp_path):
    agent = DeviceAgent(DeviceConfig(backend_url="http://x", email="e", password="p", device_name="d",
                                     state_dir=tmp_path / "s", plugin_dir=tmp_path / "p"))
    agent.device_id = "B"
    agent.remote_access = {"install": True, "invoke": False}
    order = []

    async def slow_install(pid, ver, stage=None):
        order.append("install:start")
        await asyncio.sleep(0.15)                       # the dependency step
        order.append("install:end")

    async def sync_once():
        order.append("sync")

    async def noop(*a, **k):
        return None
    agent._install_plugin, agent._sync_once = slow_install, sync_once
    agent._sha_for = lambda pid: "x"
    agent.api.report_install = noop
    agent.ws.send = noop

    install = asyncio.create_task(agent._handle_ws_message(
        {"type": "plugin.install", "requested_by_device": "A", "plugin": {"id": "browser", "version": "1"}}))
    await asyncio.sleep(0.02)                           # the install is running...

    async def one_sync_tick():                          # ...when the sync loop's tick arrives
        async with agent._plugin_lock:
            await agent._sync_once()
    await one_sync_tick()
    await install
    assert order == ["install:start", "install:end", "sync"]


@pytest.mark.asyncio
async def test_a_long_install_step_keeps_reporting_so_the_backend_does_not_give_up(tmp_path):
    agent = DeviceAgent(DeviceConfig(backend_url="http://x", email="e", password="p", device_name="d",
                                     state_dir=tmp_path / "s", plugin_dir=tmp_path / "p"))
    agent.device_id = "B"
    agent.remote_access = {"install": True, "invoke": False}
    agent.config.progress_keepalive_seconds = 0.03
    sent = []

    async def ws_send(frame):
        sent.append(frame["status"])

    async def noop(*a, **k):
        return None

    async def slow_install(pid, ver, stage=None):
        await stage("installing")
        await asyncio.sleep(0.2)                         # e.g. pip downloading dependencies
    agent.ws.send, agent.api.report_install, agent._install_plugin = ws_send, noop, slow_install
    agent._sha_for = lambda pid: "x"
    await agent._handle_ws_message({"type": "plugin.install", "operation_id": "op", "requested_by_device": "A",
                                    "plugin": {"id": "browser", "version": "1"}})
    assert sent.count("installing") >= 4                 # the stage, then repeated sign-of-life frames
    await asyncio.sleep(0.1)
    count = len(sent)
    await asyncio.sleep(0.1)
    assert len(sent) == count                            # the keepalive stops when the install ends
