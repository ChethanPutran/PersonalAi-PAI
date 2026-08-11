"""Workflow Engine: Complex multi-step task orchestration and execution."""

import asyncio
import logging
import uuid
from typing import Dict, List, Optional, Any, Callable, Coroutine
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class WorkflowStatus(Enum):
    """Status of a workflow."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class StepStatus(Enum):
    """Status of a workflow step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRY = "retry"


@dataclass
class StepResult:
    """Result of a workflow step execution."""
    step_id: str
    status: StepStatus
    output: Optional[Any] = None
    error: Optional[str] = None
    duration: float = 0.0
    retry_count: int = 0


@dataclass
class WorkflowStep:
    """A single step in a workflow."""
    id: str
    name: str
    handler: Callable  # Async function to execute
    inputs: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    timeout: float = 300.0
    max_retries: int = 2
    on_error: str = "fail"  # "fail", "skip", "retry"
    metadata: Dict[str, Any] = field(default_factory=dict)
    result: Optional[StepResult] = None


@dataclass
class WorkflowConfig:
    """Workflow configuration."""
    id: str
    name: str
    description: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    parallelizable: List[List[str]] = field(default_factory=list)
    timeout: float = 3600.0  # 1 hour default
    allow_partial_completion: bool = False


@dataclass
class WorkflowExecution:
    """Workflow execution record."""
    workflow_id: str
    execution_id: str
    status: WorkflowStatus
    steps_executed: Dict[str, StepResult]
    start_time: str
    end_time: Optional[str] = None
    duration: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class WorkflowEngine:
    """Orchestrates complex multi-step workflows with error handling and parallelization."""
    
    def __init__(self, max_concurrent_steps: int = 5):
        """Initialize workflow engine.
        
        Args:
            max_concurrent_steps: Maximum parallel steps to run
        """
        self.max_concurrent_steps = max_concurrent_steps
        self.active_workflows: Dict[str, WorkflowExecution] = {}
        self.step_registry: Dict[str, Callable] = {}
    
    def register_step_handler(self, handler_name: str, handler: Callable) -> None:
        """Register a reusable step handler.
        
        Args:
            handler_name: Name to register handler under
            handler: Async callable
        """
        self.step_registry[handler_name] = handler
        logger.debug(f"Registered step handler: {handler_name}")
    
    async def execute_workflow(self, workflow: WorkflowConfig) -> WorkflowExecution:
        """Execute a workflow.
        
        Args:
            workflow: Workflow configuration to execute
            
        Returns:
            Workflow execution result
        """
        execution_id = str(uuid.uuid4())
        logger.info(f"Starting workflow: {workflow.name} (ID: {execution_id})")
        
        execution = WorkflowExecution(
            workflow_id=workflow.id,
            execution_id=execution_id,
            status=WorkflowStatus.RUNNING,
            steps_executed={},
            start_time=datetime.utcnow().isoformat()
        )
        
        self.active_workflows[execution_id] = execution
        
        try:
            # Execute workflow steps
            await self._execute_steps(workflow, execution)
            
            # Determine final status
            failed_steps = [s for s in execution.steps_executed.values() if s.status == StepStatus.FAILED]
            skipped_steps = [s for s in execution.steps_executed.values() if s.status == StepStatus.SKIPPED]
            
            if failed_steps and not workflow.allow_partial_completion:
                execution.status = WorkflowStatus.FAILED
                execution.error = f"{len(failed_steps)} step(s) failed"
            else:
                execution.status = WorkflowStatus.COMPLETED
            
            logger.info(f"Workflow {workflow.name} completed: {execution.status.value}")
            
        except asyncio.TimeoutError:
            execution.status = WorkflowStatus.FAILED
            execution.error = "Workflow timeout"
            logger.error(f"Workflow timeout: {workflow.name}")
        except Exception as e:
            execution.status = WorkflowStatus.FAILED
            execution.error = str(e)
            logger.error(f"Workflow error: {e}")
        finally:
            execution.end_time = datetime.utcnow().isoformat()
            execution.duration = (datetime.fromisoformat(execution.end_time) - 
                                 datetime.fromisoformat(execution.start_time)).total_seconds()
        
        return execution
    
    async def _execute_steps(self, workflow: WorkflowConfig, execution: WorkflowExecution) -> None:
        """Execute all steps in a workflow.
        
        Args:
            workflow: Workflow configuration
            execution: Execution record to update
        """
        step_by_id = {step.id: step for step in workflow.steps}
        
        # Topological sort to determine execution order
        executed = set()
        pending = set(step_by_id.keys())
        
        while pending:
            # Find steps with all dependencies executed
            ready = []
            for step_id in pending:
                step = step_by_id[step_id]
                if all(dep in executed for dep in step.dependencies):
                    ready.append(step_id)
            
            if not ready:
                # Check for circular dependencies
                logger.error("Circular dependency detected in workflow")
                break
            
            # Execute ready steps (respecting concurrency limit)
            for i in range(0, len(ready), self.max_concurrent_steps):
                batch = ready[i:i + self.max_concurrent_steps]
                tasks = [self._execute_step(step_by_id[sid], execution) for sid in batch]
                await asyncio.gather(*tasks, return_exceptions=True)
                executed.update(batch)
                pending -= set(batch)
    
    async def _execute_step(self, step: WorkflowStep, execution: WorkflowExecution) -> None:
        """Execute a single workflow step with retry logic.
        
        Args:
            step: Step to execute
            execution: Execution record
        """
        logger.info(f"Executing step: {step.name}")
        
        start_time = datetime.utcnow()
        retry_count = 0
        result = None
        
        while retry_count <= step.max_retries:
            try:
                # Prepare inputs (substitute previous step outputs)
                inputs = self._prepare_inputs(step.inputs, execution)
                
                # Execute with timeout
                if asyncio.iscoroutinefunction(step.handler):
                    result = await asyncio.wait_for(
                        step.handler(**inputs),
                        timeout=step.timeout
                    )
                else:
                    result = step.handler(**inputs)
                
                # Success
                duration = (datetime.utcnow() - start_time).total_seconds()
                step_result = StepResult(
                    step_id=step.id,
                    status=StepStatus.COMPLETED,
                    output=result,
                    duration=duration,
                    retry_count=retry_count
                )
                execution.steps_executed[step.id] = step_result
                logger.info(f"Step completed: {step.name} ({duration:.2f}s)")
                return
                
            except asyncio.TimeoutError:
                logger.warning(f"Step timeout: {step.name}")
                error = f"Timeout after {step.timeout}s"
                retry_count += 1
                
            except Exception as e:
                logger.warning(f"Step error: {step.name}: {e}")
                error = str(e)
                retry_count += 1
            
            if retry_count <= step.max_retries:
                logger.info(f"Retrying step {step.name} (attempt {retry_count + 1}/{step.max_retries + 1})")
                await asyncio.sleep(2 ** retry_count)  # Exponential backoff
        
        # All retries exhausted
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        if step.on_error == "skip":
            status = StepStatus.SKIPPED
        else:
            status = StepStatus.FAILED
        
        step_result = StepResult(
            step_id=step.id,
            status=status,
            error=error,
            duration=duration,
            retry_count=retry_count
        )
        execution.steps_executed[step.id] = step_result
        
        logger.error(f"Step {step.on_error}: {step.name} after {retry_count} retries")
    
    def _prepare_inputs(self, inputs: Dict[str, Any], execution: WorkflowExecution) -> Dict[str, Any]:
        """Prepare step inputs, substituting previous step outputs.
        
        Args:
            inputs: Raw inputs
            execution: Execution record with previous results
            
        Returns:
            Prepared inputs with substitutions
        """
        prepared = {}
        
        for key, value in inputs.items():
            if isinstance(value, str) and value.startswith("$"):
                # Reference to previous step output
                ref = value[1:].split(".")
                step_id = ref[0]
                
                if step_id in execution.steps_executed:
                    result = execution.steps_executed[step_id]
                    if result.output is not None:
                        if len(ref) > 1:
                            # Navigate nested output
                            obj = result.output
                            for attr in ref[1:]:
                                obj = obj.get(attr) if isinstance(obj, dict) else getattr(obj, attr)
                            prepared[key] = obj
                        else:
                            prepared[key] = result.output
                    else:
                        prepared[key] = None
                else:
                    prepared[key] = None
            else:
                prepared[key] = value
        
        return prepared
    
    async def pause_workflow(self, execution_id: str) -> bool:
        """Pause a running workflow.
        
        Args:
            execution_id: Execution ID to pause
            
        Returns:
            Success status
        """
        if execution_id in self.active_workflows:
            execution = self.active_workflows[execution_id]
            execution.status = WorkflowStatus.PAUSED
            logger.info(f"Paused workflow: {execution_id}")
            return True
        return False
    
    async def resume_workflow(self, execution_id: str) -> bool:
        """Resume a paused workflow.
        
        Args:
            execution_id: Execution ID to resume
            
        Returns:
            Success status
        """
        if execution_id in self.active_workflows:
            execution = self.active_workflows[execution_id]
            if execution.status == WorkflowStatus.PAUSED:
                execution.status = WorkflowStatus.RUNNING
                logger.info(f"Resumed workflow: {execution_id}")
                return True
        return False
    
    async def cancel_workflow(self, execution_id: str) -> bool:
        """Cancel a running workflow.
        
        Args:
            execution_id: Execution ID to cancel
            
        Returns:
            Success status
        """
        if execution_id in self.active_workflows:
            execution = self.active_workflows[execution_id]
            execution.status = WorkflowStatus.FAILED
            execution.error = "Cancelled by user"
            logger.info(f"Cancelled workflow: {execution_id}")
            return True
        return False
    
    def get_execution_status(self, execution_id: str) -> Optional[WorkflowExecution]:
        """Get status of a workflow execution.
        
        Args:
            execution_id: Execution ID
            
        Returns:
            Execution record or None
        """
        return self.active_workflows.get(execution_id)
    
    def get_execution_history(self, workflow_id: str, limit: int = 10) -> List[WorkflowExecution]:
        """Get execution history for a workflow.
        
        Args:
            workflow_id: Workflow ID
            limit: Maximum results
            
        Returns:
            List of execution records
        """
        # In production, would query from database
        matching = [e for e in self.active_workflows.values() if e.workflow_id == workflow_id]
        return sorted(matching, key=lambda x: x.start_time, reverse=True)[:limit]


# Convenience factory for creating workflows
def create_workflow(name: str, description: str = "") -> WorkflowConfig:
    """Create a new workflow configuration.
    
    Args:
        name: Workflow name
        description: Workflow description
        
    Returns:
        Workflow configuration
    """
    return WorkflowConfig(
        id=str(uuid.uuid4()),
        name=name,
        description=description
    )


def add_step(workflow: WorkflowConfig, step: WorkflowStep) -> None:
    """Add a step to a workflow.
    
    Args:
        workflow: Workflow to add to
        step: Step to add
    """
    workflow.steps.append(step)
