"""High-level planning engine."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.observability.tracer import get_tracer
from pai.planning.goal_decomposer import GoalDecomposer
from pai.planning.models import (
    Plan,
    PlanStatus,
    TaskSpec,
)
from pai.planning.plan_verifier import PlanVerifier


class Planner(Lifecycle):
    def __init__(
        self,
        llm_router: Any,
        capability_router: Any,
        memory_manager: Any
    ):
        self.llm = llm_router
        if self.llm is None:
            raise ValueError("Planner requires an LLM provider")

        self.goal_decomposer = GoalDecomposer(
            self.llm,
            capability_router=capability_router,
            memory_manager=memory_manager
        )
        self.verifier = PlanVerifier(capability_router=capability_router)
        self.active_plans: Dict[str, Plan] = {}
        self._initialized = False

    async def initialize(self) -> None:
        await self.goal_decomposer.initialize()
        self._initialized = True
        logger.info("Planner initialized")

    async def shutdown(self) -> None:
        self.active_plans.clear()
        await self.goal_decomposer.shutdown()
        self._initialized = False
        logger.info("Planner shutdown complete")

    def is_initialized(self) -> bool:
        return self._initialized
    
    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "active_plans": len(self.active_plans),
        }

    async def create_plan(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        if not goal or not goal.strip():
            raise ValueError("Goal cannot be empty")

        context = context or {}
        tracer = get_tracer()

        async with tracer.span(
            "planner.create_plan_internal",
            goal=goal[:200],
        ) as span:

            logger.info("Creating plan for goal: {}", goal)

            # ---- 1. Decompose (already has its own span) ----
            t0 = time.perf_counter()
            task_specs = await self.goal_decomposer.decompose(goal, context)
            span.set_attribute(
                "decompose_ms",
                round((time.perf_counter() - t0) * 1000, 1),
            )
            span.set_attribute("task_count", len(task_specs))

            # ---- 2. Normalize dependencies ----
            t0 = time.perf_counter()
            self._normalize_dependencies(task_specs)
            span.set_attribute(
                "normalize_ms",
                round((time.perf_counter() - t0) * 1000, 1),
            )

            # ---- 3. Build Plan ----
            plan = Plan(
                id=f"plan_{uuid.uuid4().hex[:12]}",
                goal=goal,
                tasks=task_specs,
                context=context,
                metadata={"planner": "default"},
            )
            span.set_attribute("plan_id", plan.id)

            logger.info(
                "Created plan {} with {} tasks",
                plan.id, len(plan.tasks),
            )

            # ---- 4. Verify ----
            async with tracer.span(
                "planner.verify",
                plan_id=plan.id,
                task_count=len(plan.tasks),
            ) as verify_span:
                t0 = time.perf_counter()
                verification = await self.verifier.verify(plan)
                verify_span.set_attribute(
                    "verify_ms",
                    round((time.perf_counter() - t0) * 1000, 1),
                )
                verify_span.set_attribute("valid", verification["valid"])
                if not verification["valid"]:
                    verify_span.set_attribute(
                        "issues", verification["issues"],
                    )

            if not verification["valid"]:
                plan.status = PlanStatus.FAILED
                plan.verification_issues = verification["issues"]
                span.set_error(
                    "invalid_plan: " + "; ".join(verification["issues"])
                )
                raise ValueError(
                    "Invalid plan: " + "; ".join(verification["issues"])
                )

            plan.status = PlanStatus.VERIFIED
            plan.updated_at = datetime.now(timezone.utc)
            self.active_plans[plan.id] = plan

            logger.info(
                "Created verified plan {} with {} tasks",
                plan.id, len(plan.tasks),
            )

            return plan

    async def replan(
        self,
        plan_id: str,
        feedback: Dict[str, Any],
    ) -> Plan:
        plan = self.active_plans.get(plan_id)
        if plan is None:
            raise ValueError(f"Plan not found: {plan_id}")

        tracer = get_tracer()
        async with tracer.span(
            "planner.replan",
            original_plan_id=plan_id,
            feedback_keys=list(feedback.keys()),
        ):
            context = {
                **plan.context,
                "previous_plan_id": plan.id,
                "feedback": feedback,
            }

            new_plan = await self.create_plan(goal=plan.goal, context=context)
            new_plan.status = PlanStatus.ADAPTED
            new_plan.metadata["replanned_from"] = plan.id

            return new_plan

    async def get_plan(self, plan_id: str) -> Optional[Plan]:
        return self.active_plans.get(plan_id)

    async def cancel_plan(self, plan_id: str) -> bool:
        plan = self.active_plans.get(plan_id)
        if plan is None:
            return False
        plan.status = PlanStatus.CANCELLED
        plan.updated_at = datetime.now(timezone.utc)
        return True

    @staticmethod
    def _normalize_dependencies(tasks: list[TaskSpec]) -> None:
        task_ids = {task.id for task in tasks}
        for task in tasks:
            task.depends_on = [
                dependency
                for dependency in task.depends_on
                if dependency in task_ids
            ]