from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.memory.utils import _json_default
from pai.observability.tracer import get_tracer


class ProceduralMemory:
    """
    Stores reusable workflows and their success/failure statistics.
    """

    def __init__(self, path: str = "./data/procedural.json"):
        self.path = Path(path)
        self.workflows: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "procedural.initialize",
            path=str(self.path),
        ) as span:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.workflows = self._load()
            span.set_attribute("workflow_count", len(self.workflows))

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    async def _save(self) -> None:
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(self.workflows, indent=2, ensure_ascii=False, default=_json_default),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add_workflow(
        self,
        goal_pattern: str,
        steps: List[Dict[str, Any]],
        *,
        success: bool = True,
    ) -> None:
        if not goal_pattern:
            raise ValueError("goal_pattern cannot be empty")

        tracer = get_tracer()
        async with tracer.span(
            "procedural.add_workflow",
            pattern_length=len(goal_pattern),
            step_count=len(steps),
            success=success,
        ) as span:
            t0 = time.perf_counter()
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

                is_new = (
                    workflow["successes"] == 0 and workflow["failures"] == 0
                )
                if success:
                    workflow["successes"] += 1
                else:
                    workflow["failures"] += 1

                try:
                    await self._save()
                except Exception as exc:
                    span.set_error(f"{type(exc).__name__}: {exc}")
                    raise

            span.set_attribute("is_new_pattern", is_new)
            span.set_attribute(
                "total_workflows", len(self.workflows),
            )
            span.set_attribute(
                "persist_ms", round((time.perf_counter() - t0) * 1000, 1),
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_workflow(
        self,
        goal: str,
    ) -> Optional[Dict[str, Any]]:
        tracer = get_tracer()

        async with tracer.span(
            "procedural.get_workflow",
            goal_length=len(goal),
        ) as span:
            goal_lower = goal.lower()
            best_workflow = None
            best_score = -1.0
            considered = 0

            for pattern, workflow in self.workflows.items():
                considered += 1
                if pattern.lower() not in goal_lower:
                    continue

                successes = workflow.get("successes", 0)
                failures = workflow.get("failures", 0)
                total = successes + failures
                success_rate = (
                    successes / total if total > 0 else 0.0
                )

                if success_rate > best_score:
                    best_score = success_rate
                    best_workflow = {
                        "pattern": pattern,
                        "steps": workflow.get("steps", []),
                        "successes": successes,
                        "failures": failures,
                        "success_rate": success_rate,
                    }

            span.set_attribute("considered", considered)
            span.set_attribute("matched", best_workflow is not None)
            if best_workflow:
                span.set_attribute(
                    "matched_pattern", best_workflow["pattern"],
                )
                span.set_attribute(
                    "success_rate",
                    round(best_workflow["success_rate"], 3),
                )
            return best_workflow

    async def shutdown(self) -> None:
        async with self._lock:
            try:
                await self._save()
            except Exception as exc:
                logger.warning(
                    "Failed to persist procedural memory on shutdown: {}", exc,
                )