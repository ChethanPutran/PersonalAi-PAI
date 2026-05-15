from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from .assistant import AssistantAgent


@dataclass
class AgentManager:
    default_agent: AssistantAgent
    agents: Dict[str, AssistantAgent] = field(default_factory=dict)

    def __post_init__(self):
        self.agents["default"] = self.default_agent

    def register(self, name: str, agent: AssistantAgent) -> None:
        self.agents[name] = agent

    def reply(self, message: str, agent_name: str = "default") -> str:
        return self.agents.get(agent_name, self.default_agent).reply(message)

