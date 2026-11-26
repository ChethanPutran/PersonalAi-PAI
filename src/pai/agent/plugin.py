"""The `agent` plugin: provided by the backend itself, enabled per user.

There is nothing to install: the agent runtime (``pai.agent``) runs in the backend. What the user controls is
whether the assistant may start and steer agents for them (``agent.start`` ...) and whether ``POST /agents``
works. Turn it on in Settings or Plugins like any other plugin; it shows as built in.
"""
from __future__ import annotations

from pai.common.enums.device import DevicePlatform
from pai.plugins.manifest_enums import PluginLanguage, RuntimeKind
from pai.plugins.manifest_info import PluginInfo
from pai.plugins.manifest_models import PluginRuntime
from pai.version import RUNTIME_VERSION

AGENT_PLUGIN_ID = "agent"
AGENT_CAPABILITIES = ("agent.start", "agent.message", "agent.status", "agent.cancel", "agent.list")
AGENT_PLUGIN_DISABLED_MESSAGE = (
    "The Agent plugin is turned off. Turn it on in Settings > Plugins to let PAI run agents for you.")


def agent_plugin_info() -> PluginInfo:
    return PluginInfo(
        id=AGENT_PLUGIN_ID,
        name="Agent",
        version="1.0.0",
        description=("Lets PAI work step by step toward a goal on your devices (observe, act, check) and lets "
                     "you talk to it while it works. Provided by the PAI backend; nothing to install."),
        type=PluginLanguage.PYTHON,
        runtimeKind=RuntimeKind.SERVER,
        runtimeSpec=PluginRuntime(kind=RuntimeKind.SERVER, nativeModule="pai.agent",
                                  minPaiVersion=RUNTIME_VERSION),
        capabilities=list(AGENT_CAPABILITIES),
        requiresConfirmation=False,
        builtin=True,
    )
