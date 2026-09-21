"""High-level planning engine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from pai.planning.goal_decomposer import GoalDecomposer
from pai.planning.models import (
    Plan,
    PlanStatus,
    TaskSpec,
)
from pai.planning.plan_verifier import PlanVerifier


class Planner:
    """
    Converts a user goal into a validated Plan.

    Responsibilities:
        user input
            ↓
        goal understanding
            ↓
        task decomposition
            ↓
        dependency construction
            ↓
        plan verification

    It does NOT execute tasks.
    """

    def __init__(
        self,
        llm_router: Any,
        capability_router: Any,
    ):
        self.llm = llm_router

        if self.llm is None:
            raise ValueError(
                "Planner requires an LLM provider"
            )

        self.goal_decomposer = GoalDecomposer(
            self.llm
        )

        self.verifier = PlanVerifier(
            capability_router=capability_router
        )

        self.active_plans: Dict[str, Plan] = {}
        self._initialized = False

    async def initialize(self) -> None:
        await self.goal_decomposer.initialize()

        self._initialized = True

        logger.info("Planner initialized")


    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "active_plans": len(self.active_plans),
        }
    async def create_plan(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        """
        Create and verify a plan from user input.
        """

        if not goal or not goal.strip():
            raise ValueError(
                "Goal cannot be empty"
            )

        context = context or {}

        logger.info(
            f"Creating plan for goal: {goal}"
        )

        task_specs = (
            await self.goal_decomposer.decompose(
                goal,
                context,
            )
        )

        self._normalize_dependencies(
            task_specs
        )

        plan = Plan(
            id=f"plan_{uuid.uuid4().hex[:12]}",
            goal=goal,
            tasks=task_specs,
            context=context,
            metadata={
                "planner": "default",
            },
        )

        verification = await self.verifier.verify(
            plan
        )

        if not verification["valid"]:
            plan.status = PlanStatus.FAILED
            plan.verification_issues = (
                verification["issues"]
            )

            raise ValueError(
                "Invalid plan: "
                + "; ".join(
                    verification["issues"]
                )
            )

        plan.status = PlanStatus.VERIFIED
        plan.updated_at = datetime.now(
            timezone.utc
        )

        self.active_plans[plan.id] = plan

        logger.info(
            f"Created verified plan {plan.id} "
            f"with {len(plan.tasks)} tasks"
        )

        return plan

    async def replan(
        self,
        plan_id: str,
        feedback: Dict[str, Any],
    ) -> Plan:
        """
        Recreate a plan after execution feedback.

        This is useful when the TaskManager reports
        that execution cannot continue.
        """

        plan = self.active_plans.get(plan_id)

        if plan is None:
            raise ValueError(
                f"Plan not found: {plan_id}"
            )

        context = {
            **plan.context,
            "previous_plan_id": plan.id,
            "feedback": feedback,
        }

        new_plan = await self.create_plan(
            goal=plan.goal,
            context=context,
        )

        new_plan.status = PlanStatus.ADAPTED
        new_plan.metadata["replanned_from"] = (
            plan.id
        )

        return new_plan

    async def get_plan(
        self,
        plan_id: str,
    ) -> Optional[Plan]:
        return self.active_plans.get(plan_id)

    async def cancel_plan(
        self,
        plan_id: str,
    ) -> bool:
        plan = self.active_plans.get(plan_id)

        if plan is None:
            return False

        plan.status = PlanStatus.CANCELLED
        plan.updated_at = datetime.now(
            timezone.utc
        )

        return True

    @staticmethod
    def _normalize_dependencies(
        tasks: list[TaskSpec],
    ) -> None:
        """
        Ensure dependencies reference valid task IDs.

        If the LLM omitted dependencies, tasks remain
        independent and may run concurrently.
        """

        task_ids = {
            task.id
            for task in tasks
        }

        for task in tasks:
            task.depends_on = [
                dependency
                for dependency in task.depends_on
                if dependency in task_ids
            ]