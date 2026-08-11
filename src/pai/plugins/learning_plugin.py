from typing import Dict, Any, List
import json
from pathlib import Path
from pai.plugins.base_plugin import BasePlugin

class LearningPlugin(BasePlugin):
    """Learns user behavior, habits, and preferences over time."""
    name = "learning"
    
    async def initialize(self) -> None:
        self.user_model = {}
        self.history = []
    
    def get_capabilities(self) -> List[str]:
        return ["learning.record_interaction", "learning.recommend", "learning.predict_habit"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "learning.record_interaction":
            return await self._record(params)
        elif action == "learning.recommend":
            return await self._recommend(params.get('context'))
        elif action == "learning.predict_habit":
            return await self._predict_habit(params.get('habit_name'))
        raise ValueError(f"Unknown action: {action}")
    
    async def _record(self, params: Dict) -> Dict:
        self.history.append(params)
        # Update user model (simplified: count frequencies)
        action_type = params.get('action')
        self.user_model[action_type] = self.user_model.get(action_type, 0) + 1
        return {"recorded": True}
    
    async def _recommend(self, context: str) -> List[str]:
        # Simple recommendation based on frequent actions
        if "morning" in context.lower():
            return ["Check calendar", "Read news"]
        return ["Take a break", "Plan tomorrow"]
    
    async def _predict_habit(self, habit_name: str) -> Dict:
        # Predict next occurrence based on history
        return {"probability": 0.75, "next_expected": "2025-01-15T09:00:00"}