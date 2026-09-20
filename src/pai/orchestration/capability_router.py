       
from typing import Dict, Any, Optional
from loguru import logger

class CapabilityRouter:
    """Routes tasks to the best executor based on capabilities."""
    
    def __init__(self):
        self._capability_map = {
            "search": "server_executor",
            "browser_navigate": "desktop_executor",
            "calendar_event": "server_executor",
            "notification": "mobile_executor",
            "llm_reason": "server_executor",
            "camera_capture": "mobile_executor",
            "voice_synthesis": "mobile_executor",
            "file_operation": "desktop_executor",
            "open_app": "desktop_executor",
            "ahk": "desktop_executor",
            "desktop_click": "desktop_executor",
            "screenshot": "desktop_executor",
        }
    
    async def initialize(self) -> None:
        logger.info("CapabilityRouter initialized")
    
    async def route_task(self, task):
        task_type = task.get("type", "default")

        executor = self._capability_map.get(
            task_type,
            "server_executor"
        )

        logger.debug(
            f"Routed {task_type} -> {executor}"
        )

        return executor

    async def shutdown(self) -> None:
        logger.info("CapabilityRouter shutdown complete")
