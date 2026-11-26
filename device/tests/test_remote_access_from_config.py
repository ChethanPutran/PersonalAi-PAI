"""The config file's remote_access value is APPLIED to the backend once per start, only for the keys it sets."""
import pytest

from pai_device.agent.agent import DeviceAgent
from pai_device.config import DeviceConfig


def make(tmp_path, install=None, invoke=None):
    cfg = DeviceConfig(backend_url="http://x", email="e", password="p", device_name="d", state_dir=tmp_path / "s",
                       plugin_dir=tmp_path / "p", remote_access_install=install, remote_access_invoke=invoke)
    agent = DeviceAgent(cfg)
    agent.device_id = "dev-1"
    calls = []

    async def set_remote_access(device_id, *, install=None, invoke=None):
        calls.append((device_id, install, invoke))
        return {"allow_remote_install": install if install is not None else agent.remote_access["install"],
                "allow_remote_invoke": invoke if invoke is not None else agent.remote_access["invoke"]}
    agent.api.set_remote_access = set_remote_access
    agent._calls = calls
    return agent


async def connect(agent, install=False, invoke=False):
    """The backend's `connected` frame, then let the config sync run."""
    await agent._handle_ws_message({"type": "connected", "remote_access": {"install": install, "invoke": invoke}})
    for t in list(agent._background):
        await t


@pytest.mark.asyncio
async def test_true_in_the_file_is_applied_to_the_backend_and_then_takes_effect(tmp_path):
    agent = make(tmp_path, install=True, invoke=True)
    await connect(agent, install=False, invoke=False)            # the owner's setting for this device is off
    assert agent._calls == [("dev-1", True, True)]
    assert agent.remote_access == {"install": True, "invoke": True}


@pytest.mark.asyncio
async def test_only_the_keys_the_file_sets_are_touched(tmp_path):
    agent = make(tmp_path, install=True, invoke=None)            # invoke: null = follow the owner
    await connect(agent, install=False, invoke=True)             # the owner turned invoke on in the app
    assert agent._calls == [("dev-1", True, None)]
    assert agent.remote_access == {"install": True, "invoke": True}


@pytest.mark.asyncio
async def test_false_in_the_file_turns_it_off(tmp_path):
    agent = make(tmp_path, install=False, invoke=False)
    await connect(agent, install=True, invoke=True)
    assert agent._calls == [("dev-1", False, False)]
    assert agent.remote_access == {"install": False, "invoke": False}


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_file_says_nothing_or_already_matches(tmp_path):
    for agent, frame in ((make(tmp_path), (True, True)), (make(tmp_path, install=True, invoke=False), (True, False))):
        await connect(agent, *frame)
        assert agent._calls == []
        assert agent.remote_access == {"install": frame[0], "invoke": frame[1]}      # the owner's setting is kept


@pytest.mark.asyncio
async def test_a_reconnect_never_undoes_a_change_the_owner_made_while_running(tmp_path):
    agent = make(tmp_path, install=True)
    await connect(agent, install=False)                          # start: the file turns it on
    assert agent._calls == [("dev-1", True, None)]
    agent._apply_remote_access({"install": False, "invoke": False})    # the owner turns it off in the app
    await connect(agent, install=False)                          # network blip: reconnect
    assert agent._calls == [("dev-1", True, None)]               # NOT applied again
    assert agent.remote_access["install"] is False


@pytest.mark.asyncio
async def test_if_the_backend_refuses_the_device_stays_closed(tmp_path):
    agent = make(tmp_path, install=True)

    async def refuse(*a, **k):
        raise RuntimeError("HTTP 403")
    agent.api.set_remote_access = refuse
    await connect(agent, install=False)
    assert agent.remote_access == {"install": False, "invoke": False}
