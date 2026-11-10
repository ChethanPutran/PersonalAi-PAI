"""Plan validation and safety checks."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.observability.tracer import get_tracer
from pai.orchestration.capability_router import (
    CapabilityNotAvailable,
    CapabilityRouter,
)
from pai.planning.models import Plan, TaskSpec


class PlanVerifier(Lifecycle):

    def __init__(self, capability_router: CapabilityRouter):
        self.capability_router = capability_router

    async def initialize(self) -> None:
        if self.capability_router:
            await self.capability_router.initialize()
        logger.info("PlanVerifier initialized")

    async def shutdown(self) -> None:
        if self.capability_router:
            await self.capability_router.shutdown()
        logger.info("PlanVerifier shutdown complete")

    def is_initialized(self) -> bool:
        if self.capability_router:
            return self.capability_router.is_initialized()
        return True
    
    async def verify(self, plan: Plan) -> Dict[str, Any]:
        tracer = get_tracer()

        async with tracer.span(
            "plan_verifier.verify",
            plan_id=plan.id,
            task_count=len(plan.tasks),
        ) as span:

            issues: List[str] = []

            logger.debug(
                "Verifying plan {} ({} tasks)", plan.id, len(plan.tasks),
            )

            async with tracer.span(
                "plan_verifier.validate_tasks",
                task_count=len(plan.tasks),
            ) as tasks_span:
                task_issues = self._validate_tasks(plan.tasks)
                issues.extend(task_issues)
                tasks_span.set_attribute("issue_count", len(task_issues))

            async with tracer.span(
                "plan_verifier.validate_dependencies",
                task_count=len(plan.tasks),
            ) as dep_span:
                dep_issues = self._validate_dependencies(plan)
                issues.extend(dep_issues)
                dep_span.set_attribute("issue_count", len(dep_issues))

            if self.capability_router is not None:
                user_id = (plan.context or {}).get("user_id")
                async with tracer.span(
                    "plan_verifier.validate_capabilities",
                    user_id=user_id,
                    task_count=len(plan.tasks),
                ) as cap_span:
                    cap_issues = await self._validate_capabilities(plan, user_id)
                    issues.extend(cap_issues)
                    cap_span.set_attribute("issue_count", len(cap_issues))

            valid = not issues
            span.set_attribute("valid", valid)
            span.set_attribute("issue_count", len(issues))
            if not valid:
                span.set_attribute("issues", issues)

            if valid:
                logger.info("Plan {} passed verification", plan.id)
            else:
                logger.warning(
                    "Plan {} failed verification: {}", plan.id, issues,
                )

            return {
                "valid": valid,
                "issues": issues,
                "plan_id": plan.id,
            }

    # ------------------------------------------------------------------
    # Task validation
    # ------------------------------------------------------------------

    def _validate_tasks(self, tasks: List[TaskSpec]) -> List[str]:
        issues = []
        ids: Set[str] = set()

        for task in tasks:
            if task.id in ids:
                issues.append(f"Duplicate task ID: {task.id}")
            ids.add(task.id)

            if not task.type:
                issues.append(f"Task {task.id} has no type")

            if not task.capability:
                issues.append(f"Task {task.id} has no capability")

        return issues

    # ------------------------------------------------------------------
    # Dependency validation
    # ------------------------------------------------------------------

    def _validate_dependencies(self, plan: Plan) -> List[str]:
        issues = []
        task_ids = {task.id for task in plan.tasks}

        for task in plan.tasks:
            for dependency in task.depends_on:
                if dependency not in task_ids:
                    issues.append(
                        f"Task {task.id} depends on unknown task {dependency}"
                    )
                if dependency == task.id:
                    issues.append(f"Task {task.id} depends on itself")

        if self._has_cycle(plan):
            issues.append("Plan contains a cyclic dependency")

        return issues

    def _has_cycle(self, plan: Plan) -> bool:
        graph = {task.id: task.depends_on for task in plan.tasks}
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for dependency in graph.get(node, []):
                if visit(dependency):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(task_id) for task_id in graph)

    # ------------------------------------------------------------------
    # Capability validation — now user-aware
    # ------------------------------------------------------------------

    async def _validate_capabilities(
        self,
        plan: Plan,
        user_id: str | None,
    ) -> List[str]:
        from pai.planning.goal_decomposer import CHAT_CAPABILITY_ID

        issues: List[str] = []
        router = self.capability_router

        if router is None:
            return issues

        if not user_id:
            logger.warning(
                "PlanVerifier: plan {} has no user_id in context — "
                "skipping capability validation", plan.id,
            )
            return issues

        for task in plan.tasks:
            capability = task.capability
            if not capability:
                continue

            # Virtual capability, handled by the orchestrator directly
            if capability == CHAT_CAPABILITY_ID:
                continue

            try:
                targets = await router.resolve(
                    capability=capability,
                    user_id=user_id,
                    preferred_device_id=task.preferred_device,
                )
                if not targets:
                    issues.append(
                        f"Task {task.id}: no routable target for capability "
                        f"'{capability}'"
                    )
            except Exception as exc:
                # Import locally to avoid a circular import
                from pai.orchestration.capability_router import (
                    CapabilityNotAvailable,
                )
                if isinstance(exc, CapabilityNotAvailable):
                    issues.append(
                        f"Task {task.id}: capability '{capability}' "
                        f"unavailable ({exc.error.detail.get('stage')}) — "
                        f"{exc.error.reason}"
                    )
                else:
                    logger.exception(
                        "PlanVerifier: capability check failed for {}: {}",
                        capability, exc,
                    )
                    issues.append(
                        f"Task {task.id}: capability check errored: {exc}"
                    )

        return issues