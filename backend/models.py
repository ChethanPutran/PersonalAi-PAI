from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime

class MessageType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    COMMAND = "command"
    RESPONSE = "response"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"

class SessionStatus(str, Enum):
    ACTIVE = "active"
    WAITING_FOR_WAKE = "waiting_for_wake"
    PROCESSING = "processing"
    IDLE = "idle"

class ClientMessage(BaseModel):
    type: MessageType
    content: str
    session_id: str
    timestamp: datetime = datetime.now()
    metadata: Optional[Dict[str, Any]] = {}

class ServerMessage(BaseModel):
    type: MessageType
    content: str
    session_id: str
    timestamp: datetime = datetime.now()
    processing_time_ms: Optional[float] = None
    requires_approval: bool = False
    pending_action: Optional[Dict[str, Any]] = None

class UserSession(BaseModel):
    session_id: str
    user_id: str
    status: SessionStatus
    created_at: datetime
    last_active: datetime
    conversation_history: List[Dict[str, Any]]
    pending_approvals: List[Dict[str, Any]]

class WakeWordConfig(BaseModel):
    wake_word: str = "hey bot"
    sensitivity: float = 0.7  # 0-1, higher = more sensitive
    cooldown_seconds: int = 2  # Seconds after response before listening again