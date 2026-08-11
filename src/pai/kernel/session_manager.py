import json
from typing import Dict

class SessionManager:
    """Cross‑Device Session Migration
    When a device disconnects, save context; on reconnect from another device, restore"""
    def __init__(self, memory_manager):
        self.memory = memory_manager
    
    async def save_session(self, user_id: str, session_data: Dict):
        await self.memory.long_term.set_preference(f"session_{user_id}", session_data)
    
    async def restore_session(self, user_id: str) -> Dict:
        return await self.memory.long_term.get_preference(f"session_{user_id}", {})