"""Plan validation and safety checks."""

from __future__ import annotations

from typing import Any, Dict, List, Set
from loguru import logger
from pai.planning.models import Plan, TaskSpec


class PlanVerifier:
    """
    Validates plans before they are handed to the task system.

    No task execution occurs here.
    """

    def __init__(self, capability_router=None):
        self.capability_router = capability_router

    async def verify(self, plan: Plan) -> Dict[str, Any]:
        issues: List[str] = []

        issues.extend(self._validate_tasks(plan.tasks))
        issues.extend(self._validate_dependencies(plan))


        if self.capability_router is not None:
            issues.extend(
                await self._validate_capabilities(plan)
            )

        valid = not issues

        if valid:
            logger.info(
                f"Plan {plan.id} passed verification"
            )
        else:
            logger.warning(
                f"Plan {plan.id} failed verification: {issues}"
            )

        return {
            "valid": valid,
            "issues": issues,
            "plan_id": plan.id,
        }

    def _validate_tasks(
        self,
        tasks: List[TaskSpec],
    ) -> List[str]:
        issues = []
        ids: Set[str] = set()

        for task in tasks:
            if task.id in ids:
                issues.append(
                    f"Duplicate task ID: {task.id}"
                )

            ids.add(task.id)

            if not task.type:
                issues.append(
                    f"Task {task.id} has no type"
                )

        return issues

    def _validate_dependencies(
        self,
        plan: Plan,
    ) -> List[str]:
        issues = []

        task_ids = {
            task.id
            for task in plan.tasks
        }

        for task in plan.tasks:
            for dependency in task.depends_on:
                if dependency not in task_ids:
                    issues.append(
                        f"Task {task.id} depends on "
                        f"unknown task {dependency}"
                    )

                if dependency == task.id:
                    issues.append(
                        f"Task {task.id} depends on itself"
                    )

        if self._has_cycle(plan):
            issues.append(
                "Plan contains a cyclic dependency"
            )

        return issues

    def _has_cycle(self, plan: Plan) -> bool:
        graph = {
            task.id: task.depends_on
            for task in plan.tasks
        }

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

        return any(
            visit(task_id)
            for task_id in graph
        )

    async def _validate_capabilities(
        self,
        plan: Plan,
    ) -> List[str]:
        issues = []

        router = self.capability_router
        
        if router is None:
            return issues

        for task in plan.tasks:
            capability = task.capability

            if not capability:
                continue

            try:
                available = await router.has_capability(
                    capability
                )
            except TypeError:
                available = router.has_capability(
                    capability
                )

            if not available:
                issues.append(
                    f"Missing capability: {capability}"
                )

        return issues