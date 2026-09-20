from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProceduralMemory:
    """
    Stores reusable workflows and their outcomes.
    """

    def __init__(
        self,
        path: str = "./data/procedural.json",
    ):
        self.path = Path(path)

        self.workflows: Dict[str, Dict[str, Any]] = {}

        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.workflows = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}

        try:
            return json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )
        except (json.JSONDecodeError, OSError):
            return {}

    async def _save(self) -> None:
        temporary_path = self.path.with_suffix(
            ".tmp"
        )

        temporary_path.write_text(
            json.dumps(
                self.workflows,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        temporary_path.replace(self.path)

    async def add_workflow(
        self,
        goal_pattern: str,
        steps: List[Dict[str, Any]],
        *,
        success: bool = True,
    ) -> None:

        if not goal_pattern:
            raise ValueError(
                "goal_pattern cannot be empty"
            )

        async with self._lock:
            workflow = self.workflows.setdefault(
                goal_pattern,
                {
                    "steps": steps,
                    "successes": 0,
                    "failures": 0,
                },
            )

            workflow["steps"] = steps

            if success:
                workflow["successes"] += 1
            else:
                workflow["failures"] += 1

            await self._save()

    async def get_workflow(
        self,
        goal: str,
    ) -> Optional[Dict[str, Any]]:

        goal_lower = goal.lower()

        best_workflow = None
        best_score = -1.0

        for pattern, workflow in self.workflows.items():

            if pattern.lower() not in goal_lower:
                continue

            successes = workflow.get(
                "successes",
                0,
            )

            failures = workflow.get(
                "failures",
                0,
            )

            total = successes + failures

            success_rate = (
                successes / total
                if total > 0
                else 0.0
            )

            if success_rate > best_score:
                best_score = success_rate

                best_workflow = {
                    "pattern": pattern,
                    "steps": workflow.get(
                        "steps",
                        [],
                    ),
                    "successes": successes,
                    "failures": failures,
                    "success_rate": success_rate,
                }

        return best_workflow

    async def shutdown(self) -> None:
        async with self._lock:
            await self._save()