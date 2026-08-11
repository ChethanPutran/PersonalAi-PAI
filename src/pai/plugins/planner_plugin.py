from typing import Dict, Any, List
from datetime import datetime, timedelta
from pai.plugins.base_plugin import BasePlugin

class PlannerPlugin(BasePlugin):
    """Goal decomposition and daily planning."""
    name = "planner"
    
    def get_capabilities(self) -> List[str]:
        return ["planner.decompose_goal", "planner.create_daily_plan", "planner.optimize_schedule"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "planner.decompose_goal":
            return await self._decompose_goal(params.get('goal'))
        elif action == "planner.create_daily_plan":
            return await self._create_daily_plan(params.get('tasks', []))
        elif action == "planner.optimize_schedule":
            return await self._optimize_schedule(params.get('events', []))
        raise ValueError(f"Unknown action: {action}")
    
    async def _decompose_goal(self, goal: str) -> List[Dict]:
        # In production, use LLM
        if "write report" in goal.lower():
            return [
                {"task": "research", "duration": 60},
                {"task": "outline", "duration": 30},
                {"task": "write", "duration": 120},
                {"task": "review", "duration": 30}
            ]
        return [{"task": goal, "duration": 30, "subtasks": []}]
    
    async def _create_daily_plan(self, tasks: List[str]) -> Dict:
        # Simple time blocking
        slots = []
        start = datetime.now().replace(hour=9, minute=0)
        for task in tasks[:5]:
            slots.append({"time": start.strftime("%H:%M"), "task": task})
            start += timedelta(minutes=60)
        return {"plan": slots}
    
    async def _optimize_schedule(self, events: List[Dict]) -> List[Dict]:
        # Sort by priority or duration
        return sorted(events, key=lambda x: x.get('priority', 0), reverse=True)