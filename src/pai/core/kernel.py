"""
AI Kernel - Central intelligence orchestrator
"""
import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime
from dataclasses import dataclass, asdict
import json

logger = logging.getLogger(__name__)


@dataclass
class Context:
    """Context data structure"""
    user_id: str
    session_id: str
    device_id: str
    timestamp: datetime
    metadata: Dict[str, Any]


@dataclass
class Task:
    """Task data structure"""
    id: str
    goal: str
    agent_id: str
    priority: int = 0
    status: str = "pending"
    metadata: Dict[str, Any] = None


class AIKernel:
    """
    Central intelligence layer of the Personal AI system.
    
    Responsibilities:
    - Context management
    - Memory access
    - Planning and reasoning
    - Capability routing
    - Event orchestration
    - Security and permissions
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize AI Kernel"""
        self.config = config or {}
        self.id = str(uuid.uuid4())
        self.contexts: Dict[str, Context] = {}
        self.tasks: Dict[str, Task] = {}
        self.memory_service = None
        self.event_bus = None
        self.plugin_manager = None
        self.agent_manager = None
        logger.info(f"AI Kernel initialized: {self.id}")
    
    async def initialize(self, 
                        memory_service=None,
                        event_bus=None,
                        plugin_manager=None,
                        agent_manager=None):
        """Initialize kernel dependencies"""
        self.memory_service = memory_service
        self.event_bus = event_bus
        self.plugin_manager = plugin_manager
        self.agent_manager = agent_manager
        logger.info("AI Kernel dependencies initialized")
    
    async def create_context(self, 
                            user_id: str,
                            device_id: str,
                            metadata: Dict[str, Any] = None) -> Context:
        """Create a new execution context"""
        context = Context(
            user_id=user_id,
            session_id=str(uuid.uuid4()),
            device_id=device_id,
            timestamp=datetime.utcnow(),
            metadata=metadata or {}
        )
        self.contexts[context.session_id] = context
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("kernel/context_created", {
                "session_id": context.session_id,
                "user_id": user_id,
                "device_id": device_id
            })
        
        logger.info(f"Context created: {context.session_id}")
        return context
    
    async def get_context(self, session_id: str) -> Optional[Context]:
        """Get context by session ID"""
        return self.contexts.get(session_id)
    
    async def create_task(self,
                         goal: str,
                         agent_id: str,
                         priority: int = 0,
                         metadata: Dict[str, Any] = None) -> Task:
        """Create a new task"""
        task = Task(
            id=str(uuid.uuid4()),
            goal=goal,
            agent_id=agent_id,
            priority=priority,
            metadata=metadata or {}
        )
        self.tasks[task.id] = task
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("kernel/task_created", {
                "task_id": task.id,
                "goal": goal,
                "agent_id": agent_id
            })
        
        logger.info(f"Task created: {task.id}")
        return task
    
    async def route_capability(self,
                              capability_name: str,
                              context: Context) -> Optional[Dict[str, Any]]:
        """Route to appropriate capability/plugin"""
        if not self.plugin_manager:
            logger.warning("Plugin manager not initialized")
            return None
        
        plugin = await self.plugin_manager.get_plugin(capability_name)
        if plugin:
            logger.info(f"Routed capability {capability_name} to plugin {plugin.id}")
            return {"plugin_id": plugin.id, "name": plugin.name}
        
        logger.warning(f"Capability not found: {capability_name}")
        return None
    
    async def retrieve_memory(self,
                             user_id: str,
                             query: str,
                             limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieve memory for reasoning"""
        if not self.memory_service:
            logger.warning("Memory service not initialized")
            return []
        
        memories = await self.memory_service.search(user_id, query, limit)
        return memories
    
    async def store_memory(self,
                          user_id: str,
                          content: str,
                          memory_type: str = "episodic",
                          metadata: Dict[str, Any] = None):
        """Store long-term memory"""
        if not self.memory_service:
            logger.warning("Memory service not initialized")
            return None
        
        memory_id = await self.memory_service.store(
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            metadata=metadata
        )
        logger.info(f"Memory stored: {memory_id}")
        return memory_id
    
    async def execute_plan(self,
                          task_id: str,
                          plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a multi-step plan"""
        task = self.tasks.get(task_id)
        if not task:
            logger.error(f"Task not found: {task_id}")
            return {"status": "failed", "error": "Task not found"}
        
        task.status = "in_progress"
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("kernel/plan_execution_started", {
                "task_id": task_id,
                "steps": len(plan.get("steps", []))
            })
        
        results = []
        for i, step in enumerate(plan.get("steps", [])):
            logger.info(f"Executing step {i+1}: {step.get('action')}")
            
            if self.event_bus:
                await self.event_bus.publish("kernel/step_executing", {
                    "task_id": task_id,
                    "step": i + 1,
                    "action": step.get("action")
                })
            
            # Step execution would be implemented here
            results.append({"step": i+1, "status": "completed"})
        
        task.status = "completed"
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("kernel/plan_execution_completed", {
                "task_id": task_id,
                "results": results
            })
        
        logger.info(f"Plan execution completed: {task_id}")
        return {"status": "completed", "results": results}
    
    async def get_status(self) -> Dict[str, Any]:
        """Get kernel status"""
        return {
            "kernel_id": self.id,
            "active_contexts": len(self.contexts),
            "active_tasks": len([t for t in self.tasks.values() if t.status == "in_progress"]),
            "total_tasks": len(self.tasks),
            "timestamp": datetime.utcnow().isoformat()
        }
