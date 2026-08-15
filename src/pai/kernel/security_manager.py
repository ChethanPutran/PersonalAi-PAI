import asyncio
from datetime import datetime
from typing import Dict, Any, List
from pai.utils.audit_log import AuditLog
import uuid
from loguru import logger

class SecurityManager:
    """Manages permissions, authentication, and sandboxing."""
    def __init__(self, kernel):
        self.kernel = kernel
        self.pending_approvals = {}
        self.user_consent_store = {}
        
        self._permissions: Dict[str, List[str]] = {}  # plugin -> allowed actions
        self.audit_log = AuditLog()
    
    async def log_action(self, user_id, action, details):
        self.audit_log.log(action, user_id, details)
        
    async def initialize(self) -> None:
        # Load permission policies from config
        self._permissions = {
            "browser": ["navigate", "click", "type", "screenshot"],
            "notification": ["send"],
            "vision": ["capture", "detect"],
        }
        logger.info("SecurityManager initialized")
    
    async def check_permission(self, plugin_name: str, action: str) -> bool:
        allowed = self._permissions.get(plugin_name, [])
        return action in allowed or "*" in allowed
    
    async def authenticate(self, token: str) -> bool:
        # Stub – integrate OAuth2 in production
        return token == "valid_token"
    
    async def authorize(self, user_id: str, resource: str) -> bool:
        # Stub – role‑based access control
        return True
    
    async def request_permission(self, plugin_name: str, action: str, user_id: str, details: Dict) -> bool:
        # Check if already approved
        if (plugin_name, action) in self.user_consent_store.get(user_id, set()):
            return True
        # Store pending approval
        approval_id = str(uuid.uuid4())
        self.pending_approvals[approval_id] = {
            "plugin": plugin_name,
            "action": action,
            "user": user_id,
            "details": details,
            "timestamp": datetime.now().timestamp()
        }
        # Notify user via event bus
        await self.kernel.event_bus.publish("permission.request", {
            "approval_id": approval_id,
            "plugin": plugin_name,
            "action": action,
            "details": details
        })
        # Wait for response (async)
        return await self._wait_for_approval(approval_id, timeout=30)
    
    async def approve_permission(self, approval_id: str, user_id: str, permanent: bool = False):
        approval = self.pending_approvals.get(approval_id)
        if not approval or approval["user"] != user_id:
            return False
        if permanent:
            self.user_consent_store.setdefault(user_id, set()).add((approval["plugin"], approval["action"]))
        del self.pending_approvals[approval_id]
        return True
    
    async def _wait_for_approval(self, approval_id: str, timeout: int = 30) -> bool:
        start_time = datetime.now().timestamp()
        while datetime.now().timestamp() - start_time < timeout:
            if approval_id not in self.pending_approvals:
                return True  # Approved
            await asyncio.sleep(1)
        # Timeout
        del self.pending_approvals[approval_id]
        return False

    async def shutdown(self) -> None:
        """Clean up resources."""
        self.pending_approvals.clear()
        self.user_consent_store.clear()
        logger.info("SecurityManager shutdown complete")