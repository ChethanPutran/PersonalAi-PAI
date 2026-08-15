"""AI Kernel - Central intelligence layer."""

from typing import Dict, Any, Optional
import logging

from pai.config import Config
from pai.kernel.context_manager import ContextManager
from pai.kernel.capability_router import CapabilityRouter
from pai.kernel.llm_provider import LLMProvider
from pai.kernel.security_manager import SecurityManager
from pai.kernel.executor_scheduler import ExecutorScheduler
from pai.memory.memory_manager import MemoryManager
from pai.planning.planning_engine import PlanningEngine
from pai.event_bus.event_bus import EventBus
from pai.agents.agent_manager import AgentManager
from pai.plugins.plugin_manager import PluginManager


class AIKernel:
    """
    Central AI Kernel orchestrating the entire system.
    
    Responsibilities:
    - Context management
    - Memory coordination
    - Planning and goal decomposition
    - Capability routing
    - Event orchestration
    - Security enforcement
    - Executor scheduling
    """
    
    def __init__(self,config: Config):
        self._initialized = False
        self._running = False
        
        # Core components
        self.context_manager = ContextManager(max_history=config.memory.context_history_size)
        self.memory_manager = MemoryManager(
            config.memory.long_term_db_url,
            config.memory.episodic_db_url,
            config.memory.vector_store_path,
            config.memory.procedural_path,
            config.memory.neo4j_url,
            config.memory.neo4j_user,
            config.memory.neo4j_password,
            short_term_size=config.memory.short_term_size,
            short_term_ttl=config.memory.short_term_ttl,
            embedding_model=config.memory.embedding_model
        )
        self.llm= LLMProvider(config.llm.provider,
                                        model=config.llm.model, 
                                        api_key=config.llm.api_key, 
                                        base_url=config.llm.base_url,
                                        temperature=config.llm.temperature,
                                        max_tokens=config.llm.max_tokens)
        self.planning_engine = PlanningEngine(self)
        self.capability_router = CapabilityRouter()
        self.security_manager = SecurityManager(self)
        self.executor_scheduler = ExecutorScheduler(config.executor.name, 
                                                    config.executor.host, 
                                                    config.executor.port, 
                                                    self, 
                                                    config.executor.executors)
        self.event_bus = EventBus(config.event_bus.nats.url, 
                                  max_queue_size=config.event_bus.max_queue_size)
        
        # Managers
        self.agent_manager = AgentManager(self)
        self.plugin_manager = PluginManager(self,enabled_plugins=config.plugins.enabled_plugins)
        
        # State
        self.user_id: Optional[str] = None
        self.session_id: Optional[str] = None
        
    async def initialize(self) -> None:
        """Initialize the kernel and all subsystems."""
        logging.info("Initializing AI Kernel...")
        
        # Initialize core components
        await self.context_manager.initialize()
        await self.memory_manager.initialize()
        await self.planning_engine.initialize()
        await self.capability_router.initialize()
        await self.security_manager.initialize()
        await self.executor_scheduler.initialize()
        await self.event_bus.initialize()
        
        # Initialize managers
        await self.agent_manager.initialize()
        await self.plugin_manager.initialize()
        
        self._initialized = True
        logging.info("AI Kernel initialized successfully")
    
    async def start(self) -> None:
        """Start the kernel and all services."""
        if not self._initialized:
            await self.initialize()
        
        logging.info("Starting AI Kernel...")
        
        # Start event bus
        await self.event_bus.start()
        
        # Start executor scheduler
        await self.executor_scheduler.start()
        
        # Start agent manager
        await self.agent_manager.start()
        
        self._running = True
        logging.info("AI Kernel started")
    
    async def shutdown(self) -> None:
        """
        Shutdown the kernel and all subsystems."""
        if not self._running:
            return
        logging.info("Stopping AI Kernel...")
        
        self._running = False
        
        await self.agent_manager.stop()
        await self.executor_scheduler.stop()
        await self.event_bus.shutdown()
        await self.plugin_manager.shutdown()
        await self.memory_manager.shutdown()
        await self.context_manager.shutdown()
        await self.planning_engine.shutdown()
        await self.capability_router.shutdown()
        await self.security_manager.shutdown()
        await self.executor_scheduler.shutdown()
        await self.event_bus.shutdown()

        
        self._initialized = False
        logging.info("AI Kernel stopped")
    
    async def process_goal(self, goal: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process a user goal through the complete pipeline.
        
        Args:
            goal: User's goal or request
            context: Additional context information
            
        Returns:
            Execution result
        """
        logging.info(f"Processing goal: {goal}")
        
        # Update context
        if context:
            await self.context_manager.update(context)
        
        # Create execution plan
        plan = await self.planning_engine.create_plan(goal, await self.context_manager.get_context())
        
        # Route capabilities and execute
        result = await self._execute_plan(plan)
        
        # Store in memory
        await self.memory_manager.store_interaction(goal, result)
        
        return result
    
    async def _execute_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a plan by routing tasks to appropriate executors."""
        tasks = plan.get("tasks", [])
        results = []
        
        for task in tasks:
            # Determine best executor for this task
            executor = await self.capability_router.route_task(task)
            
            # Execute task
            result = await self.executor_scheduler.schedule_task(task, executor)
            results.append(result)
            
            # Publish event
            logging.info(f"Task completed: {task}")
            await self.event_bus.publish("task.completed", {
                "task": task,
                "result": result
            })
        
        return {
            "plan_id": plan.get("id"),
            "status": "completed",
            "results": results
        }
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle incoming events."""
        await self.event_bus.publish(event_type, data)
        
        # Route to appropriate handlers
        if event_type.startswith("agent."):
            await self.agent_manager.handle_agent_event(event_type, data)
        elif event_type.startswith("plugin."):
            await self.plugin_manager.handle_plugin_event(event_type, data)
    
    def get_status(self) -> Dict[str, Any]:
        """Get kernel status."""
        return {
            "initialized": self._initialized,
            "running": self._running,
            "user_id": self.user_id,
            "session_id": self.session_id
        }