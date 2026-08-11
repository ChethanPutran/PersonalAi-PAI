"""Autonomous Planning Engine: LLM-based multi-step task planning and reasoning."""

import asyncio
import logging
import json
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
import re

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Status of a task in plan."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ReasoningType(Enum):
    """Types of reasoning used."""
    STEP_BY_STEP = "step_by_step"
    CHAIN_OF_THOUGHT = "chain_of_thought"
    TREE_OF_THOUGHT = "tree_of_thought"
    CONSTRAINT_BASED = "constraint_based"


@dataclass
class PlanStep:
    """A single step in a plan."""
    step_id: int
    description: str
    action: str  # Action to take
    required_capabilities: List[str]
    estimated_duration: float  # in seconds
    dependencies: List[int] = field(default_factory=list)  # Depends on other step IDs
    expected_output: str = ""
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ExecutionPlan:
    """Complete execution plan for a goal."""
    plan_id: str
    goal: str
    steps: List[PlanStep]
    reasoning: str
    reasoning_type: ReasoningType
    total_estimated_duration: float
    created_at: str
    reasoning_chain: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    success_criteria: List[str] = field(default_factory=list)


class PlanningEngine:
    """Autonomous planning engine using LLM reasoning."""
    
    def __init__(self, llm_model: str = "gpt-4", temperature: float = 0.7):
        """Initialize planning engine.
        
        Args:
            llm_model: LLM model to use
            temperature: Temperature for LLM (0.0-1.0)
        """
        self.llm_model = llm_model
        self.temperature = temperature
        self.client = None
        self.available_capabilities: List[str] = []
    
    async def initialize(self, api_key: Optional[str] = None) -> None:
        """Initialize LLM client.
        
        Args:
            api_key: API key for LLM service
        """
        try:
            if self.llm_model.startswith("gpt"):
                from openai import AsyncOpenAI
                self.client = AsyncOpenAI(api_key=api_key)
            else:
                logger.warning(f"LLM model {self.llm_model} not fully configured")
            logger.info(f"Planning engine initialized with {self.llm_model}")
        except ImportError:
            logger.error("OpenAI client not available")
            raise
    
    def register_capability(self, capability: str) -> None:
        """Register available system capability.
        
        Args:
            capability: Capability name (e.g., 'browser_automation', 'vision', 'text_generation')
        """
        if capability not in self.available_capabilities:
            self.available_capabilities.append(capability)
            logger.debug(f"Registered capability: {capability}")
    
    async def create_plan(self, goal: str, 
                         context: Optional[str] = None,
                         constraints: Optional[Dict[str, Any]] = None,
                         reasoning_type: ReasoningType = ReasoningType.CHAIN_OF_THOUGHT) -> ExecutionPlan:
        """Create execution plan for a goal.
        
        Args:
            goal: High-level goal to achieve
            context: Additional context information
            constraints: Constraints on execution
            reasoning_type: Type of reasoning to use
            
        Returns:
            Execution plan with steps
        """
        if not self.client:
            logger.error("LLM client not initialized")
            raise RuntimeError("Planning engine not initialized")
        
        logger.info(f"Creating plan for goal: {goal}")
        
        # Build prompt for planning
        prompt = self._build_planning_prompt(
            goal=goal,
            context=context,
            constraints=constraints,
            reasoning_type=reasoning_type
        )
        
        # Get plan from LLM
        try:
            response = await self.client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are an expert task planning AI. Create detailed, step-by-step execution plans with clear actions and dependencies."},
                    {"role": "user", "content": prompt}
                ],
                temperature=self.temperature,
                max_tokens=2000
            )
            
            plan_text = response.choices[0].message.content
            
            # Parse plan from LLM response
            plan = self._parse_plan(plan_text, goal, reasoning_type)
            
            logger.info(f"Created plan with {len(plan.steps)} steps")
            return plan
            
        except Exception as e:
            logger.error(f"Plan creation failed: {e}")
            raise
    
    async def analyze_constraint(self, goal: str, constraint: str) -> Dict[str, Any]:
        """Analyze a constraint and its implications.
        
        Args:
            goal: The goal being constrained
            constraint: Constraint description
            
        Returns:
            Analysis of constraint implications
        """
        if not self.client:
            return {"error": "LLM client not initialized"}
        
        try:
            response = await self.client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are an expert at analyzing constraints and their implications."},
                    {"role": "user", "content": f"For goal: {goal}\nConstraint: {constraint}\n\nAnalyze this constraint and its implications for task execution."}
                ],
                temperature=self.temperature,
                max_tokens=500
            )
            
            return {
                "constraint": constraint,
                "analysis": response.choices[0].message.content,
                "implications": self._extract_implications(response.choices[0].message.content)
            }
        except Exception as e:
            logger.error(f"Constraint analysis failed: {e}")
            return {"error": str(e)}
    
    async def replan_on_failure(self, original_plan: ExecutionPlan,
                                failed_step_id: int,
                                error: str) -> Optional[ExecutionPlan]:
        """Replan when a step fails.
        
        Args:
            original_plan: Original execution plan
            failed_step_id: ID of failed step
            error: Error message
            
        Returns:
            Revised plan or None if unrecoverable
        """
        if not self.client:
            return None
        
        failed_step = next((s for s in original_plan.steps if s.step_id == failed_step_id), None)
        if not failed_step:
            return None
        
        logger.warning(f"Replanning due to failure in step {failed_step_id}: {error}")
        
        # Get alternative approaches from LLM
        try:
            response = await self.client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are expert at finding alternative approaches when plans fail."},
                    {"role": "user", "content": f"""
                    Original goal: {original_plan.goal}
                    Failed step: {failed_step.description}
                    Error: {error}
                    Available capabilities: {', '.join(self.available_capabilities)}
                    
                    Create alternative approach to achieve the goal given this failure.
                    """}
                ],
                temperature=self.temperature + 0.2,  # Higher temperature for creative alternatives
                max_tokens=1500
            )
            
            alternative_text = response.choices[0].message.content
            new_plan = self._parse_plan(alternative_text, original_plan.goal, original_plan.reasoning_type)
            
            logger.info(f"Created alternative plan with {len(new_plan.steps)} steps")
            return new_plan
            
        except Exception as e:
            logger.error(f"Replanning failed: {e}")
            return None
    
    async def optimize_plan(self, plan: ExecutionPlan) -> ExecutionPlan:
        """Optimize plan for efficiency.
        
        Args:
            plan: Original plan
            
        Returns:
            Optimized plan
        """
        if not self.client:
            return plan
        
        logger.info("Optimizing plan for efficiency")
        
        try:
            # Serialize current plan
            plan_text = json.dumps([asdict(step) for step in plan.steps], default=str)
            
            response = await self.client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are expert at optimizing execution plans for efficiency."},
                    {"role": "user", "content": f"""
                    Current plan steps:
                    {plan_text}
                    
                    Optimize this plan for:
                    1. Parallelization where possible
                    2. Reducing total execution time
                    3. Removing redundant steps
                    4. Improving error handling
                    
                    Return optimized step sequence.
                    """}
                ],
                temperature=self.temperature,
                max_tokens=1500
            )
            
            optimized_text = response.choices[0].message.content
            optimized_plan = self._parse_plan(optimized_text, plan.goal, plan.reasoning_type)
            
            logger.info(f"Optimized plan: {plan.total_estimated_duration:.1f}s → {optimized_plan.total_estimated_duration:.1f}s")
            return optimized_plan
            
        except Exception as e:
            logger.error(f"Plan optimization failed: {e}")
            return plan
    
    def _build_planning_prompt(self, goal: str, context: Optional[str],
                               constraints: Optional[Dict[str, Any]],
                               reasoning_type: ReasoningType) -> str:
        """Build planning prompt for LLM."""
        
        prompt = f"""
        Goal: {goal}
        
        Available capabilities: {', '.join(self.available_capabilities)}
        
        Reasoning approach: {reasoning_type.value}
        
        """
        
        if context:
            prompt += f"\nContext: {context}\n"
        
        if constraints:
            prompt += f"\nConstraints:\n"
            for key, value in constraints.items():
                prompt += f"- {key}: {value}\n"
        
        prompt += """
        Create a detailed execution plan with:
        1. Clear sequential steps
        2. Dependencies between steps
        3. Required capabilities for each step
        4. Estimated duration
        5. Expected output for each step
        
        Format as JSON array of steps.
        """
        
        return prompt
    
    def _parse_plan(self, plan_text: str, goal: str, reasoning_type: ReasoningType) -> ExecutionPlan:
        """Parse LLM response into ExecutionPlan."""
        
        from datetime import datetime
        
        # Extract JSON from response
        json_match = re.search(r'\[.*\]', plan_text, re.DOTALL)
        if not json_match:
            # Fallback: create simple plan
            steps = [
                PlanStep(
                    step_id=1,
                    description=goal,
                    action=goal,
                    required_capabilities=self.available_capabilities[:1] if self.available_capabilities else ["general"],
                    estimated_duration=60.0,
                    expected_output="Task completed"
                )
            ]
        else:
            try:
                steps_data = json.loads(json_match.group())
                steps = []
                for i, step_data in enumerate(steps_data):
                    steps.append(PlanStep(
                        step_id=i + 1,
                        description=step_data.get("description", ""),
                        action=step_data.get("action", ""),
                        required_capabilities=step_data.get("required_capabilities", []),
                        estimated_duration=step_data.get("estimated_duration", 60.0),
                        dependencies=step_data.get("dependencies", []),
                        expected_output=step_data.get("expected_output", "")
                    ))
            except json.JSONDecodeError:
                steps = [
                    PlanStep(
                        step_id=1,
                        description=goal,
                        action=goal,
                        required_capabilities=["general"],
                        estimated_duration=60.0
                    )
                ]
        
        total_duration = sum(s.estimated_duration for s in steps)
        
        plan = ExecutionPlan(
            plan_id=f"plan_{datetime.utcnow().timestamp()}",
            goal=goal,
            steps=steps,
            reasoning=plan_text[:500],  # First 500 chars
            reasoning_type=reasoning_type,
            total_estimated_duration=total_duration,
            created_at=datetime.utcnow().isoformat(),
            reasoning_chain=[plan_text]
        )
        
        return plan
    
    def _extract_implications(self, analysis_text: str) -> List[str]:
        """Extract key implications from analysis text."""
        # Simple extraction: split by newlines and filter
        lines = analysis_text.split('\n')
        implications = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
        return implications[:5]  # Return top 5
    
    def get_plan_summary(self, plan: ExecutionPlan) -> str:
        """Get summary of execution plan."""
        summary = f"""
        Plan ID: {plan.plan_id}
        Goal: {plan.goal}
        Total Steps: {len(plan.steps)}
        Estimated Duration: {plan.total_estimated_duration:.1f} seconds
        
        Steps:
        """
        for step in plan.steps:
            summary += f"\n  {step.step_id}. {step.description}"
            if step.dependencies:
                summary += f" (depends on: {', '.join(map(str, step.dependencies))})"
        
        return summary
