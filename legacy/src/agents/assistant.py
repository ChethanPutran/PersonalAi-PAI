from __future__ import annotations

from dataclasses import dataclass

from ..core.planner import RuleBasedPlanner
from ..executors.manager import ExecutorManager
from ..memory.store import MemoryStore
from ..plugins.base import PluginResult


@dataclass
class AssistantAgent:
    planner: RuleBasedPlanner
    executors: ExecutorManager
    memory: MemoryStore

    def reply(self, message: str) -> str:
        plan = self.planner.plan(message, self.memory)
        if plan["type"] == "direct":
            answer = plan["answer"]
            self.memory.add_turn(message, answer)
            return answer

        result: PluginResult = self.executors.execute(plan["plugin"], **plan["params"])
        answer = result.content if not result.requires_approval else f"{result.content} (approval required)"
        self.memory.add_turn(message, answer, {"plugin": plan["plugin"], **result.metadata})
        return answer
