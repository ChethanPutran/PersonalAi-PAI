from typing import Dict, Any, List
from loguru import logger
import json


class GoalDecomposer:
    """Uses LLM to break high-level goals into executable tasks."""
    def __init__(self, kernel=None):
        self.kernel = kernel

    async def initialize(self) -> None:
        logger.info("LLM GoalDecomposer initialized")
    
    async def decompose(self, goal: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        prompt = f"""You are an AI planning engine. Break the following user goal into a list of concrete tasks.
        Each task must have a 'type' and relevant parameters. Possible task types:
        - search (query: string)
        - browser_navigate (url: string)
        - click (selector: string)
        - type (selector: string, text: string)
        - screenshot (path: optional)
        - llm_reason (goal: string)
        - calendar_event (title, start, end)
        - notification (title, message)
        - transport_booking (pickup, dropoff, provider)
        - file_operation (operation, path, content)
        - vision_analysis (image_data, task)

        Goal: "{goal}"
        Context: {json.dumps(context)}
        Return only a JSON array of tasks, no extra text."""

        # Use LLM plugin
        if hasattr(self, 'kernel') and self.kernel:
            response = await self.kernel.plugin_manager.execute_plugin(
                "llm", "complete", {"prompt": prompt, "max_tokens": 1000}
            )
            try:
                tasks = json.loads(response.get("text", "[]"))
            except:
                tasks = []
        else:
            # Fallback to rule‑based
            tasks = await self._fallback_decompose(goal, context)
        
        for i, task in enumerate(tasks):
            task["id"] = f"task_{i}"
            task["status"] = "pending"
        return tasks
    

    async def _fallback_decompose(self, goal: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Simple decomposition rule‑based; can be replaced with LLM
        tasks = []
        
        if "search" in goal.lower():
            tasks.append({"type": "search", "query": goal})
        if "browser" in goal.lower() or "website" in goal.lower():
            tasks.append({"type": "browser_navigate", "url": context.get("url")})
        if "schedule" in goal.lower() or "meeting" in goal.lower():
            tasks.append({"type": "calendar_event"})
        if "notify" in goal.lower():
            tasks.append({"type": "notification"})
        
        if not tasks:
            # Generic fallback: use LLM plugin to decompose
            tasks = [{"type": "llm_reason", "goal": goal}]
        
        for i, task in enumerate(tasks):
            task["id"] = f"task_{i}"
            task["status"] = "pending"
        
        return tasks