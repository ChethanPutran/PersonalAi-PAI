from typing import Dict, Any, Optional
from loguru import logger

class CapabilityRouter:
    """Routes tasks to the best executor based on capabilities."""
    
    def __init__(self):
        self._capability_map = {
            "search": "server",
            "browser_navigate": "desktop",
            "calendar_event": "server",
            "notification": "mobile",
            "llm_reason": "server",
            "camera_capture": "mobile",
            "voice_synthesis": "mobile"
        }
    
    async def initialize(self) -> None:
        logger.info("CapabilityRouter initialized")
    
    async def route_task(self, task: Dict[str, Any]) -> str:
        task_type = task.get("type", "default")
        executor = self._capability_map.get(task_type, "server")
        logger.debug(f"Routed {task_type} -> {executor}")
        return executor

    async def shutdown(self) -> None:
        logger.info("CapabilityRouter shutdown complete")