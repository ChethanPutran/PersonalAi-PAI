"""The agent is a built-in plugin: provided by the backend, offered to a user only once they enable it."""
from types import SimpleNamespace

import pytest

from pai.agent.builtins import build_agent_capabilities
from pai.agent.plugin import AGENT_CAPABILITIES, AGENT_PLUGIN_ID, agent_plugin_info
from pai.orchestration.builtin_capabilities import BuiltinCapability, BuiltinCapabilityRegistry
from pai.orchestration.capability_router import CapabilityRouter


async def _noop(*a, **k):
    return {}


def test_every_agent_capability_belongs_to_the_agent_plugin_and_needs_enabling():
    caps = build_agent_capabilities(SimpleNamespace())
    assert tuple(c.id for c in caps) == AGENT_CAPABILITIES
    assert all(c.plugin_id == AGENT_PLUGIN_ID and c.requires_user_enable for c in caps)


def test_the_catalog_entry_matches_the_capabilities_and_is_marked_built_in():
    info = agent_plugin_info()
    assert info.id == "agent" and info.builtin and tuple(info.capabilities) == AGENT_CAPABILITIES
    assert info.packageUrl is None                               # nothing to download or install


def _router(enabled: set[str]) -> CapabilityRouter:
    registry = BuiltinCapabilityRegistry()
    for cap in build_agent_capabilities(SimpleNamespace()):
        registry.register(cap)
    registry.register(BuiltinCapability(id="tasks.list", description="d", parameters={}, handler=_noop))   # plain built-in
    pm = SimpleNamespace(get_catalog=lambda: [agent_plugin_info()])
    router = CapabilityRouter(device_manager=SimpleNamespace(), plugin_manager=pm, session_factory=None,
                              builtin_registry=registry)

    async def enabled_ids(user_id):
        return set(enabled)
    router._user_enabled_plugin_ids = enabled_ids
    return router


@pytest.mark.asyncio
async def test_agent_capabilities_are_hidden_until_the_user_enables_the_plugin():
    off = await _router(set())._build_index("u1")
    assert "tasks.list" in off                                    # ordinary built-ins stay available
    assert not [c for c in off if c.startswith("agent.")]

    on = await _router({"agent"})._build_index("u1")
    assert {c for c in on if c.startswith("agent.")} == set(AGENT_CAPABILITIES)
    assert all(not t.requires_device and t.plugin_id == "agent" for t in on["agent.start"])
    other = await _router({"camera_linux"})._build_index("u1")    # some OTHER plugin enabled: still hidden
    assert not [c for c in other if c.startswith("agent.")]
