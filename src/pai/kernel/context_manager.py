from typing import Dict, Any, Optional
from collections import deque
from datetime import datetime
import asyncio
from loguru import logger

class ContextManager:
    """
    Manages user, session, and device context across the system.
    Maintains conversation state, active tasks, and device awareness.
    """
    def __init__(self, max_history: int = 50):
        self.user_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.conversation_history: deque = deque(maxlen=max_history)
        self.active_tasks: Dict[str, Dict] = {}
        self.device_states: Dict[str, Dict] = {}
        self.user_preferences: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        logger.info("ContextManager initialized")
    
    async def set_user(self, user_id: str, session_id: Optional[str] = None) -> None:
        async with self._lock:
            self.user_id = user_id
            self.session_id = session_id or f"session_{datetime.now().timestamp()}"
            logger.info(f"Context: user={user_id}, session={self.session_id}")
    
    async def update(self, new_context: Dict[str, Any]) -> None:
        async with self._lock:
            if "user_id" in new_context:
                self.user_id = new_context["user_id"]
            if "session_id" in new_context:
                self.session_id = new_context["session_id"]
            if "conversation" in new_context:
                self.conversation_history.append(new_context["conversation"])
            if "device_state" in new_context:
                device = new_context["device_state"].get("device_id", "unknown")
                self.device_states[device] = new_context["device_state"]
            if "preferences" in new_context:
                self.user_preferences.update(new_context["preferences"])
    
    async def get_context(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "user_id": self.user_id,
                "session_id": self.session_id,
                "conversation_history": list(self.conversation_history),
                "active_tasks": self.active_tasks,
                "device_states": self.device_states,
                "user_preferences": self.user_preferences
            }
    
    async def register_active_task(self, task_id: str, task_info: Dict) -> None:
        async with self._lock:
            self.active_tasks[task_id] = {**task_info, "started_at": datetime.now().isoformat()}
    
    async def complete_task(self, task_id: str) -> None:
        async with self._lock:
            if task_id in self.active_tasks:
                self.active_tasks[task_id]["completed_at"] = datetime.now().isoformat()
                # Optionally move to history
                del self.active_tasks[task_id]