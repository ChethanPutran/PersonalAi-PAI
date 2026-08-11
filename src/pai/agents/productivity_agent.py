from typing import Dict, Any, List
from loguru import logger
from pai.agents.base_agent import BaseAgent

class ProductivityAgent(BaseAgent):
    """Handles tasks, scheduling, and reminders."""
    
    def __init__(self, kernel=None):
        super().__init__("productivity_agent", kernel)
        self._capabilities = [
            "todo.create",
            "todo.list",
            "schedule.meeting",
            "reminder.set"
        ]
    
    async def initialize(self) -> None:
        logger.info("ProductivityAgent initialized")
    
    
    async def _schedule(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        # Use calendar plugin
        event = await self.use_plugin("calendar", "create_event", context)
        return {"event": event}
    
    
    async def _manage_todo(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        # Use task management plugin
        result = await self.use_plugin("task_manager", "add_task", {"task": goal})
        return result
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type == "calendar.reminder":
            await self.use_plugin("notification", "send", {"message": data["message"]})

    async def process_goal(self, goal: str, context: Dict) -> Dict:
        if "schedule" in goal.lower() or "meeting" in goal.lower():
            return await self._schedule_meeting(context)
        elif "todo" in goal.lower() or "task" in goal.lower():
            return await self._manage_task(goal, context)
        elif "remind" in goal.lower():
            return await self._set_reminder(context)
        return {"error": "Unknown productivity goal"}
    
    async def _schedule_meeting(self, context: Dict) -> Dict:
        event = await self.use_plugin("calendar", "create_event", {
            "title": context.get('title', 'Meeting'),
            "start": context.get('start'),
            "end": context.get('end')
        })
        return event
    
    async def _manage_task(self, goal: str, context: Dict) -> Dict:
        task = await self.use_plugin("task_manager", "add_task", {"title": goal})
        return task
    
    async def _set_reminder(self, context: Dict) -> Dict:
        reminder = await self.use_plugin("notification", "schedule", {
            "title": context.get('title'),
            "message": context.get('message'),
            "delay_seconds": context.get('delay', 3600)
        })
        return reminder