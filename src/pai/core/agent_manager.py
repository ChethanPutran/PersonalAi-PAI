"""
Agent Manager - Multi-agent coordination using LangGraph
"""
import logging
import uuid
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Agent execution states"""
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING = "waiting"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass
class AgentConfig:
    """Agent configuration"""
    id: str
    name: str
    agent_type: str  # research, productivity, communication, coding, etc.
    description: str
    model: str = "gpt-4"
    temperature: float = 0.7
    tools: List[str] = None
    plugins: List[str] = None
    max_iterations: int = 10
    timeout: int = 300


@dataclass
class AgentInstance:
    """Running agent instance"""
    id: str
    config: AgentConfig
    state: AgentState
    task_id: Optional[str] = None
    context: Dict[str, Any] = None
    memory: List[Dict[str, Any]] = None
    performance_metrics: Dict[str, Any] = None
    created_at: datetime = None
    last_activity: datetime = None


class AgentManager:
    """
    Manages autonomous agents in the PAI system.
    
    Responsibilities:
    - Agent lifecycle management
    - Agent coordination
    - Resource allocation
    - Multi-agent collaboration
    - Workflow orchestration
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize agent manager"""
        self.config = config or {}
        self.agents: Dict[str, AgentInstance] = {}
        self.agent_configs: Dict[str, AgentConfig] = {}
        self.running_agents: List[str] = []
        self.event_bus = None
        self.kernel = None
        self.plugin_manager = None
        logger.info("Agent Manager initialized")
    
    async def initialize(self, event_bus=None, kernel=None, plugin_manager=None):
        """Initialize agent manager dependencies"""
        self.event_bus = event_bus
        self.kernel = kernel
        self.plugin_manager = plugin_manager
        logger.info("Agent Manager dependencies initialized")
    
    async def register_agent(self, config: AgentConfig) -> Optional[AgentInstance]:
        """Register a new agent"""
        if config.id in self.agent_configs:
            logger.warning(f"Agent already registered: {config.id}")
            return None
        
        self.agent_configs[config.id] = config
        logger.info(f"Agent registered: {config.name}")
        
        if self.event_bus:
            await self.event_bus.publish("agent/registered", {
                "agent_id": config.id,
                "name": config.name,
                "type": config.agent_type
            })
        
        return self.agent_configs[config.id]
    
    async def create_agent_instance(self, config_id: str) -> Optional[AgentInstance]:
        """Create a running instance of an agent"""
        if config_id not in self.agent_configs:
            logger.error(f"Agent config not found: {config_id}")
            return None
        
        config = self.agent_configs[config_id]
        agent_id = str(uuid.uuid4())
        
        instance = AgentInstance(
            id=agent_id,
            config=config,
            state=AgentState.IDLE,
            context={},
            memory=[],
            performance_metrics={
                "tasks_completed": 0,
                "tasks_failed": 0,
                "avg_execution_time": 0
            },
            created_at=datetime.utcnow(),
            last_activity=datetime.utcnow()
        )
        
        self.agents[agent_id] = instance
        logger.info(f"Agent instance created: {agent_id}")
        
        if self.event_bus:
            await self.event_bus.publish("agent/instance_created", {
                "agent_id": agent_id,
                "config_id": config_id
            })
        
        return instance
    
    async def execute_agent(self,
                           agent_id: str,
                           goal: str,
                           context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute an agent to achieve a goal"""
        if agent_id not in self.agents:
            logger.error(f"Agent instance not found: {agent_id}")
            return {"status": "failed", "error": "Agent not found"}
        
        instance = self.agents[agent_id]
        instance.state = AgentState.THINKING
        instance.task_id = str(uuid.uuid4())
        instance.context = context or {}
        instance.last_activity = datetime.utcnow()
        
        logger.info(f"Executing agent {instance.config.name}: {goal}")
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("agent/execution_started", {
                "agent_id": agent_id,
                "task_id": instance.task_id,
                "goal": goal
            })
        
        try:
            # Agent reasoning - planning and task decomposition
            plan = await self._plan_tasks(instance, goal)
            
            instance.state = AgentState.EXECUTING
            results = []
            
            # Execute plan
            for step_idx, step in enumerate(plan):
                logger.info(f"Executing step {step_idx + 1}: {step.get('action')}")
                
                # Execute step
                result = await self._execute_step(instance, step)
                results.append(result)
                
                # Update memory
                instance.memory.append({
                    "step": step_idx + 1,
                    "action": step.get("action"),
                    "result": result,
                    "timestamp": datetime.utcnow().isoformat()
                })
            
            instance.state = AgentState.COMPLETED
            instance.performance_metrics["tasks_completed"] += 1
            
            response = {
                "status": "completed",
                "agent_id": agent_id,
                "task_id": instance.task_id,
                "results": results
            }
            
            logger.info(f"Agent execution completed: {agent_id}")
            
            if self.event_bus:
                await self.event_bus.publish("agent/execution_completed", response)
            
            return response
            
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            instance.state = AgentState.FAILED
            instance.performance_metrics["tasks_failed"] += 1
            
            response = {
                "status": "failed",
                "agent_id": agent_id,
                "task_id": instance.task_id,
                "error": str(e)
            }
            
            if self.event_bus:
                await self.event_bus.publish("agent/execution_failed", response)
            
            return response
    
    async def _plan_tasks(self, instance: AgentInstance, goal: str) -> List[Dict[str, Any]]:
        """Plan tasks for an agent using reasoning"""
        # In production, this would use LLM to decompose goals
        # For now, return a simple plan structure
        
        plan = [
            {
                "step": 1,
                "action": "analyze_goal",
                "input": goal,
                "description": "Analyze and understand the goal"
            },
            {
                "step": 2,
                "action": "decompose_tasks",
                "description": "Break down goal into subtasks"
            },
            {
                "step": 3,
                "action": "execute_tasks",
                "description": "Execute planned tasks"
            }
        ]
        
        return plan
    
    async def _execute_step(self, instance: AgentInstance, step: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single step"""
        action = step.get("action")
        
        # Route to appropriate plugin or tool
        if self.plugin_manager:
            plugins = await self.plugin_manager.get_plugins_by_capability(action)
            if plugins:
                plugin = plugins[0]
                result = await self.plugin_manager.execute_plugin_method(
                    plugin.id,
                    action,
                    step
                )
                return {"action": action, "result": result}
        
        # Default execution
        return {"action": action, "status": "executed"}
    
    async def stop_agent(self, agent_id: str) -> bool:
        """Stop a running agent"""
        if agent_id not in self.agents:
            logger.warning(f"Agent not found: {agent_id}")
            return False
        
        instance = self.agents[agent_id]
        instance.state = AgentState.IDLE
        
        logger.info(f"Agent stopped: {agent_id}")
        
        if self.event_bus:
            await self.event_bus.publish("agent/stopped", {"agent_id": agent_id})
        
        return True
    
    async def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Get agent status"""
        if agent_id not in self.agents:
            return {"error": "Agent not found"}
        
        instance = self.agents[agent_id]
        return {
            "agent_id": agent_id,
            "name": instance.config.name,
            "type": instance.config.agent_type,
            "state": instance.state.value,
            "task_id": instance.task_id,
            "metrics": instance.performance_metrics,
            "last_activity": instance.last_activity.isoformat() if instance.last_activity else None
        }
    
    async def list_agents(self) -> List[Dict[str, Any]]:
        """List all agent configurations"""
        return [
            {
                "id": config.id,
                "name": config.name,
                "type": config.agent_type,
                "description": config.description
            }
            for config in self.agent_configs.values()
        ]
    
    async def get_manager_status(self) -> Dict[str, Any]:
        """Get agent manager status"""
        return {
            "total_agents": len(self.agent_configs),
            "running_instances": len([a for a in self.agents.values() if a.state != AgentState.IDLE]),
            "agents": await self.list_agents()
        }
