"""Planning Engine for autonomous goal decomposition and execution."""

import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from loguru import logger

from pai.planning.goal_decomposer import GoalDecomposer
from pai.planning.plan_verifier import PlanVerifier
from pai.planning.workflow_executor import WorkflowExecutor, TaskFailedError
from pai.planning.long_horizon import LongHorizonPlanner
from pai.memory.procedural import ProceduralMemory


class PlanningEngine:
    """
    Planning Engine for autonomous reasoning.
    
    Responsibilities:
    - Goal decomposition into subtasks
    - Workflow generation
    - Dependency resolution
    - Dynamic plan adaptation
    """
    
    def __init__(self, kernel):
        self.kernel = kernel
        self.goal_decomposer = GoalDecomposer(kernel.llm)
        self.workflow_executor = WorkflowExecutor(kernel)
        self.procedural_memory = ProceduralMemory()
        self.long_horizon = LongHorizonPlanner(kernel, self.goal_decomposer, self.workflow_executor)
        self.active_plans: Dict[str, Dict[str, Any]] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the planning engine."""
        await self.goal_decomposer.initialize()
        await self.workflow_executor.initialize()
        self._initialized = True
        logger.info("Planning engine initialized")
    
    async def create_plan(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create an execution plan for a goal.
        
        Args:
            goal: User goal or request
            context: Current context
            
        Returns:
            Execution plan
        """

        # Check procedural memory first
        existing = await self.procedural_memory.get_workflow(goal)
        if existing:
            return {"id": str(uuid.uuid4()), "tasks": existing, "source": "procedural"}
        
        if "deadline" in context:
            return await self.long_horizon.create_long_plan(goal, context["deadline"], context)
    
        logger.info(f"Creating plan for goal: {goal}")
        
        # Decompose goal into tasks
        tasks = await self.goal_decomposer.decompose(goal, context)
        
        # Create plan structure
        plan = {
            "id": str(uuid.uuid4()),
            "goal": goal,
            "tasks": tasks,
            "status": "created",
            "created_at": datetime.now().isoformat(),
            "context": context
        }
        
        self.active_plans[plan["id"]] = plan
        logger.info(f"Created plan {plan['id']} with {len(tasks)} tasks")
        
        return plan
    

    
    async def execute_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a plan.
        
        Args:
            plan: The plan to execute
            
        Returns:
            Execution results
        """

        # Verify plan before execution
        verifier = PlanVerifier(self.kernel)
        verification_result = await verifier.verify(plan)
        if not verification_result["valid"]:
            logger.error(f"Plan verification failed: {verification_result['issues']}")
            raise ValueError(f"Plan verification failed: {verification_result['issues']}")
        
        plan_id = plan["id"]
        logger.info(f"Executing plan {plan_id}")
        
        # Update status
        plan["status"] = "executing"
        
        try:
            results = await self.workflow_executor.execute(plan["tasks"])
            
            plan["status"] = "completed"
            plan["completed_at"] = datetime.now().isoformat()
            plan["results"] = results
            
            return plan
            
        except TaskFailedError as e:
            logger.error(f"Plan execution failed: {e}. Triggering adaptation...") 
            
            # Auto‑adapt
            adapted_plan = await self.adapt_plan(plan_id, {"error": str(e), "failed_task": e.failed_task if hasattr(e, 'failed_task') else None})
            # Retry execution once
            return await self.execute_plan(adapted_plan)
    
    async def adapt_plan(self, plan_id: str, feedback: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adapt a plan based on feedback or failures.
        
        Args:
            plan_id: ID of the plan to adapt
            feedback: Feedback information
            
        Returns:
            Adapted plan
        """
        if plan_id not in self.active_plans:
            raise ValueError(f"Plan {plan_id} not found")
        
        plan = self.active_plans[plan_id]
        logger.info(f"Adapting plan {plan_id}")
        
        # Re-decompose remaining tasks with new context
        remaining_context = {**plan["context"], **feedback}
        remaining_goal = plan["goal"]
        
        new_tasks = await self.goal_decomposer.decompose(remaining_goal, remaining_context)
        
        # Update plan
        plan["tasks"] = new_tasks
        plan["status"] = "adapted"
        plan["adapted_at"] = datetime.now().isoformat()
        
        return plan
    
    async def get_plan_status(self, plan_id: str) -> Dict[str, Any]:
        """Get the status of a plan."""
        if plan_id not in self.active_plans:
            raise ValueError(f"Plan {plan_id} not found")
        
        return {
            "id": plan_id,
            "status": self.active_plans[plan_id]["status"],
            "tasks_completed": self.workflow_executor.get_progress(plan_id)
        }
    
    async def cancel_plan(self, plan_id: str) -> bool:
        """Cancel an active plan."""
        if plan_id not in self.active_plans:
            return False
        
        plan = self.active_plans[plan_id]
        plan["status"] = "cancelled"
        plan["cancelled_at"] = datetime.now().isoformat()
        
        await self.workflow_executor.cancel(plan_id)
        
        logger.info(f"Cancelled plan {plan_id}")
        return True
    
    async def shutdown(self) -> None:
        """Shutdown the planning engine."""
        await self.workflow_executor.shutdown()
        self._initialized = False
        logger.info("Planning engine shutdown")

