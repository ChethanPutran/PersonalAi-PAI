from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from ..agents.assistant import AssistantAgent
from ..agents.manager import AgentManager
from ..config import load_config
from ..executors.local import LocalExecutor
from ..executors.manager import ExecutorManager
from ..memory.store import MemoryStore
from ..plugins.registry import PluginRegistry
from .event_bus import EventBus
from .planner import RuleBasedPlanner


@dataclass
class AIKernel:
    config: object
    memory: MemoryStore
    plugins: PluginRegistry
    events: EventBus
    agents: AgentManager

    def handle_message(self, message: str) -> str:
        self.events.publish("message.received", {"message": message})
        response = self.agents.reply(message)
        self.events.publish("message.responded", {"message": message, "response": response})
        return response

    def list_plugins(self) -> List[str]:
        return self.plugins.list()

    def snapshot(self) -> Dict[str, object]:
        return self.memory.snapshot()


def build_kernel() -> AIKernel:
    config = load_config()
    memory = MemoryStore(config.data_dir / "memory.json")
    plugins = PluginRegistry(config.workspace_dir)
    executors = ExecutorManager(LocalExecutor(plugins))
    planner = RuleBasedPlanner(plugins)
    agent = AssistantAgent(planner=planner, executors=executors, memory=memory)
    agent_manager = AgentManager(agent)
    return AIKernel(config=config, memory=memory, plugins=plugins, events=EventBus(), agents=agent_manager)
