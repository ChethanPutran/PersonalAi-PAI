import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, Union, List
from collections import defaultdict
import json
import redis.asyncio as redis
from redis.typing import ExpiryT
from .models import UserSession, SessionStatus

class SessionManager:
    """
    Manages user sessions without modifying core code
    Supports both in-memory and Redis backends
    """
    
    def __init__(self, use_redis: bool = False, redis_url: str = "redis://localhost:6379"):
        self.use_redis = use_redis
        self.sessions: Dict[str, UserSession] = {}  # In-memory fallback
        
        if use_redis:
            self.redis_client = redis.from_url(redis_url)
        else:
            self.redis_client = None
        
        # Track WebSocket connections per session
        self.connections: Dict[str, Any] = {}
        
        # Track pending responses per session
        self.pending_responses: Dict[str, asyncio.Future] = {}
        
        # Session timeout (30 minutes)
        self.session_timeout = timedelta(minutes=30)
    
    async def create_session(self, user_id: str = "anonymous") -> str:
        """Create a new session"""
        session_id = str(uuid.uuid4())
        session = UserSession(
            session_id=session_id,
            user_id=user_id,
            status=SessionStatus.IDLE,
            created_at=datetime.now(),
            last_active=datetime.now(),
            conversation_history=[],
            pending_approvals=[]
        )
        
        if self.use_redis and self.redis_client:
            await self.redis_client.setex(
                f"session:{session_id}",
                self.session_timeout,
                session.model_dump_json()
            )
        else:
            self.sessions[session_id] = session
        
        return session_id
    
    async def get_session(self, session_id: str) -> Optional[UserSession]:
        """Get session by ID"""
        if self.use_redis and self.redis_client:
            data = await self.redis_client.get(f"session:{session_id}")
            if data:
                return UserSession.model_validate_json(data)
        else:
            return self.sessions.get(session_id)
        
        return None
    
    async def update_session(self, session: UserSession):
        """Update session data"""
        session.last_active = datetime.now()
        
        if self.use_redis and self.redis_client:
            await self.redis_client.setex(
                f"session:{session.session_id}",
                self.session_timeout,
                session.model_dump_json()
            )
        else:
            self.sessions[session.session_id] = session
    
    async def update_status(self, session_id: str, status: SessionStatus):
        """Update session status"""
        session = await self.get_session(session_id)
        if session:
            session.status = status
            await self.update_session(session)
    
    async def add_to_history(self, session_id: str, user_msg: str, assistant_msg: str):
        """Add conversation to history"""
        session = await self.get_session(session_id)
        if session:
            session.conversation_history.append({
                "user": user_msg,
                "assistant": assistant_msg,
                "timestamp": datetime.now().isoformat()
            })
            
            # Keep last 100 messages only
            if len(session.conversation_history) > 100:
                session.conversation_history = session.conversation_history[-100:]
            
            await self.update_session(session)
    
    async def set_pending_approval(self, session_id: str, action: Dict[str, Any]) -> str:
        """Set a pending approval action"""
        approval_id = str(uuid.uuid4())
        session = await self.get_session(session_id)
        if session:
            session.pending_approvals.append({
                "id": approval_id,
                "action": action,
                "timestamp": datetime.now().isoformat()
            })
            await self.update_session(session)
        return approval_id
    
    async def get_pending_approval(self, session_id: str, approval_id: str) -> Optional[Dict]:
        """Get pending approval"""
        session = await self.get_session(session_id)
        if session:
            for approval in session.pending_approvals:
                if approval["id"] == approval_id:
                    return approval
        return None
    
    async def resolve_approval(self, session_id: str, approval_id: str, approved: bool):
        """Resolve a pending approval"""
        session = await self.get_session(session_id)
        if session:
            session.pending_approvals = [
                a for a in session.pending_approvals if a["id"] != approval_id
            ]
            await self.update_session(session)
    
    async def register_connection(self, session_id: str, websocket):
        """Register WebSocket connection for session"""
        self.connections[session_id] = websocket
    
    async def unregister_connection(self, session_id: str):
        """Unregister WebSocket connection"""
        if session_id in self.connections:
            del self.connections[session_id]
    
    async def send_to_session(self, session_id: str, message: Any):
        """Send message to session's WebSocket"""
        if session_id in self.connections:
            payload = message.model_dump_json() if hasattr(message, "model_dump_json") else json.dumps(message)
            await self.connections[session_id].send_text(payload)
    
    async def cleanup_expired(self):
        """Clean up expired sessions (Redis handles this automatically)"""
        if not self.use_redis:
            now = datetime.now()
            expired = [
                sid for sid, session in self.sessions.items()
                if now - session.last_active > self.session_timeout
            ]
            for sid in expired:
                del self.sessions[sid]

    async def queue_notification(self, session_id: str, notification: Dict[str, Any]):
        """Queue a notification for offline user"""
        if self.use_redis and self.redis_client:
            await self.redis_client.lpush(f"notifications:{session_id}", json.dumps(notification))
            await self.redis_client.expire(f"notifications:{session_id}", 86400)  # 24 hours
        else:
            session = await self.get_session(session_id)
            if session:
                session.notifications.append(notification)
                await self.update_session(session)

    async def get_notifications(self, session_id: str) -> List[Dict]:
        """Get pending notifications"""
        if self.use_redis and self.redis_client:
            notifications = []
            while True:
                notif = await self.redis_client.rpop(f"notifications:{session_id}")
                if not notif:
                    break
                notifications.append(json.loads(notif))
            return notifications
        else:
            session = await self.get_session(session_id)
            if session:
                notifs = list(session.notifications)
                session.notifications = []
                await self.update_session(session)
                return notifs
            return []
# Singleton instance
session_manager = SessionManager(use_redis=False)  # Set to True if you have Redis