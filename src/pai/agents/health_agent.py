from typing import Dict, Any, List
from loguru import logger
from pai.agents.base_agent import BaseAgent

class HealthAgent(BaseAgent):
    name = "health_agent"

    def __init__(self, kernel):
        super().__init__(self.name, kernel)
        self._capabilities = ["health.track_exercise", "health.analyze_form", "health.daily_summary"]
    
    async def initialize(self) -> None:
        self._capabilities = ["health.track_exercise", "health.analyze_form", "health.daily_summary"]
    
    async def process_goal(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if "track" in goal.lower() or "exercise" in goal.lower():
            return await self._track_exercise(context)
        elif "form" in goal.lower():
            return await self._analyze_form(context.get('image'))
        elif "summary" in goal.lower():
            return await self._daily_summary()
        return {"error": "Unsupported health goal"}
    
    async def _track_exercise(self, context: Dict) -> Dict:
        reps = await self.use_plugin("health", "count_reps", {"image": context.get('image'), "exercise_type": context.get('exercise')})
        return {"reps": reps, "exercise": context.get('exercise')}
    
    async def _analyze_form(self, image) -> Dict:
        form = await self.use_plugin("health", "check_form", {"image": image, "exercise_type": "squat"})
        return form
    
    async def _daily_summary(self) -> Dict:
        summary = await self.use_plugin("health", "get_daily_summary", {"date": "today"})
        return summary
    
    async def handle_event(self, event_type: str, data: Dict) -> None:
        pass