"""
Core database models for Personal AI system
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, String, DateTime, Boolean, Integer, JSON, ForeignKey, Text, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field

Base = declarative_base()

# Association table for users and devices
user_devices = Table(
    'user_devices',
    Base.metadata,
    Column('user_id', String, ForeignKey('users.id')),
    Column('device_id', String, ForeignKey('devices.id'))
)

# Association table for agents and plugins
agent_plugins = Table(
    'agent_plugins',
    Base.metadata,
    Column('agent_id', String, ForeignKey('agents.id')),
    Column('plugin_id', String, ForeignKey('plugins.id'))
)


class User(Base):
    """User model"""
    __tablename__ = "users"
    
    id = Column(String, primary_key=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    preferences = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    devices = relationship("Device", secondary=user_devices, back_populates="users")
    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="user", cascade="all, delete-orphan")
    contexts = relationship("Context", back_populates="user", cascade="all, delete-orphan")


class Device(Base):
    """Device model for distributed executors"""
    __tablename__ = "devices"
    
    id = Column(String, primary_key=True)
    device_type = Column(String)  # mobile, desktop, server, edge
    device_name = Column(String)
    platform = Column(String)  # android, ios, windows, linux, macos
    is_online = Column(Boolean, default=False)
    last_heartbeat = Column(DateTime, nullable=True)
    capabilities = Column(JSON, default={})
    config = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    users = relationship("User", secondary=user_devices, back_populates="devices")
    executors = relationship("Executor", back_populates="device", cascade="all, delete-orphan")


class Task(Base):
    """Task/Todo model"""
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), index=True)
    title = Column(String)
    description = Column(Text, nullable=True)
    status = Column(String, default="pending")  # pending, in_progress, completed, cancelled
    priority = Column(Integer, default=0)  # 0-5, higher is more urgent
    due_date = Column(DateTime, nullable=True)
    reminders = Column(JSON, default=[])
    metadata = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="tasks")


class Memory(Base):
    """Long-term memory model"""
    __tablename__ = "memories"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), index=True)
    memory_type = Column(String)  # episodic, semantic, procedural
    content = Column(Text)
    embedding = Column(JSON, nullable=True)  # vector embedding for similarity
    importance = Column(Integer, default=1)
    tags = Column(JSON, default=[])
    metadata = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    last_accessed = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="memories")


class Plugin(Base):
    """Plugin model"""
    __tablename__ = "plugins"
    
    id = Column(String, primary_key=True)
    name = Column(String, unique=True, index=True)
    version = Column(String, default="1.0.0")
    plugin_type = Column(String)  # sensor, intelligence, action, integration
    description = Column(Text, nullable=True)
    entry_point = Column(String)
    is_enabled = Column(Boolean, default=True)
    permissions = Column(JSON, default=[])
    dependencies = Column(JSON, default=[])
    config = Column(JSON, default={})
    metadata = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agents = relationship("Agent", secondary=agent_plugins, back_populates="plugins")


class Agent(Base):
    """Agent model"""
    __tablename__ = "agents"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), index=True)
    name = Column(String)
    agent_type = Column(String)  # research, productivity, communication, etc.
    description = Column(Text, nullable=True)
    is_enabled = Column(Boolean, default=True)
    config = Column(JSON, default={})
    state = Column(JSON, default={})
    performance_metrics = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="agents")
    plugins = relationship("Plugin", secondary=agent_plugins, back_populates="agents")
    workflows = relationship("Workflow", back_populates="agent", cascade="all, delete-orphan")


class Executor(Base):
    """Distributed executor model"""
    __tablename__ = "executors"
    
    id = Column(String, primary_key=True)
    device_id = Column(String, ForeignKey('devices.id'), index=True)
    executor_type = Column(String)  # mobile, desktop, server, edge
    is_active = Column(Boolean, default=True)
    resource_usage = Column(JSON, default={})  # CPU, memory, etc.
    metadata = Column(JSON, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    device = relationship("Device", back_populates="executors")
    tasks = relationship("TaskExecution", back_populates="executor", cascade="all, delete-orphan")


class Workflow(Base):
    """Workflow model for multi-step automation"""
    __tablename__ = "workflows"
    
    id = Column(String, primary_key=True)
    agent_id = Column(String, ForeignKey('agents.id'), index=True)
    name = Column(String)
    description = Column(Text, nullable=True)
    steps = Column(JSON)  # Array of workflow steps
    is_active = Column(Boolean, default=True)
    trigger = Column(JSON, nullable=True)  # Event-based trigger
    status = Column(String, default="active")
    execution_count = Column(Integer, default=0)
    last_execution = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    agent = relationship("Agent", back_populates="workflows")


class TaskExecution(Base):
    """Task execution log"""
    __tablename__ = "task_executions"
    
    id = Column(String, primary_key=True)
    executor_id = Column(String, ForeignKey('executors.id'), index=True)
    task_name = Column(String)
    status = Column(String)  # pending, running, completed, failed
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    
    # Relationships
    executor = relationship("Executor", back_populates="tasks")


class Context(Base):
    """Context management for multi-session awareness"""
    __tablename__ = "contexts"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), index=True)
    context_type = Column(String)  # session, device, workflow
    data = Column(JSON)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="contexts")


# Pydantic models for API requests/responses

class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 0
    due_date: Optional[datetime] = None


class TaskResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    status: str
    priority: int
    due_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class AgentCreate(BaseModel):
    name: str
    agent_type: str
    description: Optional[str] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    agent_type: str
    description: Optional[str]
    is_enabled: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class PluginResponse(BaseModel):
    id: str
    name: str
    version: str
    plugin_type: str
    description: Optional[str]
    is_enabled: bool
    
    class Config:
        from_attributes = True


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[Dict[str, Any]]
    trigger: Optional[Dict[str, Any]] = None


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    is_active: bool
    execution_count: int
    
    class Config:
        from_attributes = True
