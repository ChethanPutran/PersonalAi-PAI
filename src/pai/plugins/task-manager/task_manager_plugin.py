from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from pai.plugins.base import BasePlugin


class PlannerPlugin(BasePlugin):
    """Goal decomposition and daily planning."""

    def get_capabilities(self) -> list[str]:
        return [
            "planner.decompose_goal",
            "planner.create_daily_plan",
            "planner.optimize_schedule",
        ]

    async def execute(
        self,
        action: str,
        params: Dict[str, Any],
    ) -> Any:

        if action == "planner.decompose_goal":
            goal = params.get("goal")

            if not goal:
                raise ValueError(
                    "'goal' is required"
                )

            return await self._decompose_goal(goal)

        if action == "planner.create_daily_plan":
            tasks = params.get("tasks", [])

            return await self._create_daily_plan(tasks)

        if action == "planner.optimize_schedule":
            events = params.get("events", [])

            return await self._optimize_schedule(events)

        raise ValueError(
            f"Unknown planner action: {action}"
        )

    async def _decompose_goal(
        self,
        goal: str,
    ) -> List[Dict[str, Any]]:

        # Temporary implementation.
        # Later this can call the planning/LLM layer.
        if "write report" in goal.lower():
            return [
                {
                    "task": "research",
                    "duration": 60,
                },
                {
                    "task": "outline",
                    "duration": 30,
                },
                {
                    "task": "write",
                    "duration": 120,
                },
                {
                    "task": "review",
                    "duration": 30,
                },
            ]

        return [
            {
                "task": goal,
                "duration": 30,
                "subtasks": [],
            }
        ]

    async def _create_daily_plan(
        self,
        tasks: List[Any],
    ) -> Dict[str, Any]:

        slots = []

        start = datetime.now().replace(
            hour=9,
            minute=0,
            second=0,
            microsecond=0,
        )

        for task in tasks[:5]:
            slots.append(
                {
                    "time": start.strftime("%H:%M"),
                    "task": task,
                }
            )

            start += timedelta(minutes=60)

        return {
            "plan": slots,
        }

    async def _optimize_schedule(
        self,
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:

        return sorted(
            events,
            key=lambda event: event.get(
                "priority",
                0,
            ),
            reverse=True,
        )