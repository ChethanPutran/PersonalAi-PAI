"""HTTP level: the agent plugin shows in the plugin list as built in, and starting an agent needs it enabled."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "leaks"))
from helpers import isolated_config  # noqa: E402

from pai.server.factory import create_application  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    app = create_application(isolated_config(tmp_path_factory.mktemp("agentplug")))
    with TestClient(app) as client:
        r = client.post("/api/v1/auth/register", json={"email": "agent@example.com", "password": "correct-horse-1"})
        yield client, {"Authorization": "Bearer " + r.json()["access_token"]}


def test_listed_as_built_in_and_off_by_default(world):
    client, auth = world
    plugins = client.get("/api/v1/account/plugins", headers=auth).json()["plugins"]
    agent = next(p for p in plugins if p["id"] == "agent")
    assert agent["builtin"] is True and agent["enabled"] is False
    assert not any(p["builtin"] for p in plugins if p["id"] != "agent")


def test_starting_an_agent_needs_the_plugin_enabled(world):
    client, auth = world
    body = {"goal": "say hi", "tools": ["browser.*"]}
    denied = client.post("/api/v1/agents", json=body, headers=auth)
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "agent_plugin_disabled"
    assert "Settings" in denied.json()["detail"]["message"]

    assert client.post("/api/v1/plugins/agent/enable", json={}, headers=auth).status_code == 200
    assert next(p for p in client.get("/api/v1/account/plugins", headers=auth).json()["plugins"]
                if p["id"] == "agent")["enabled"] is True
    after = client.post("/api/v1/agents", json=body, headers=auth)
    assert after.status_code != 403                               # allowed through (it may fail later for other reasons)

    assert client.post("/api/v1/plugins/agent/disable", json={}, headers=auth).status_code == 200
    assert client.post("/api/v1/agents", json=body, headers=auth).status_code == 403


def test_no_device_installs_for_a_built_in_plugin(world):
    client, auth = world
    r = client.get("/api/v1/plugins/agent", headers=auth)
    assert r.status_code == 200 and r.json()["builtin"] is True
