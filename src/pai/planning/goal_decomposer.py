"""Goal decomposition into declarative task specifications."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger

from pai.llm.provider import LLMProvider
from pai.planning.models import TaskSpec


class GoalDecomposer:
    """
    Converts a natural-language goal into a declarative task graph.

    It does NOT execute tasks and does NOT interact with executors.
    """

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def initialize(self) -> None:
        logger.info("GoalDecomposer initialized")

    async def decompose(
        self,
        goal: str,
        context: Dict[str, Any] | None = None,
    ) -> List[TaskSpec]:
        context = context or {}

        prompt = self._build_prompt(goal, context)

        try:
            response = await self.llm.complete(
                prompt,
                temperature=0.1,
                max_tokens=2000,
            )

            raw_tasks = self._parse_response(response)
            tasks = self._normalize_tasks(raw_tasks)

            if tasks:
                return tasks

            logger.warning(
                "LLM returned an empty plan; using fallback"
            )

        except Exception as exc:
            logger.warning(
                f"LLM goal decomposition failed: {exc}"
            )

        return self._fallback_decompose(goal, context)

    def _build_prompt(
        self,
        goal: str,
        context: Dict[str, Any],
    ) -> str:
        return f"""
You are the planning component of a personal AI agent.

Convert the user's goal into a small executable task graph.

IMPORTANT:
- Do NOT execute anything.
- Do NOT invent results.
- Every task must be concrete.
- Tasks may depend on previous tasks.
- Use "depends_on" for dependencies.
- Tasks should contain parameters required by the executor.
- Prefer the smallest number of tasks necessary.

Supported task types:

search:
  parameters: {{"query": "..."}}

browser_navigate:
  parameters: {{"url": "..."}}

browser_click:
  parameters: {{"selector": "..."}}

browser_type:
  parameters: {{"selector": "...", "text": "..."}}

screenshot:
  parameters: {{"path": "..."}}

llm_reason:
  parameters: {{"goal": "..."}}

calendar_event:
  parameters: {{"title": "...", "start": "...", "end": "..."}}

notification:
  parameters: {{"title": "...", "message": "..."}}

file_operation:
  parameters: {{"operation": "...", "path": "...", "content": "..."}}

vision_analysis:
  parameters: {{"image": "...", "task": "..."}}

Return ONLY valid JSON:

[
  {{
    "id": "task_1",
    "type": "search",
    "parameters": {{
      "query": "..."
    }},
    "depends_on": [],
    "capability": "search"
  }}
]

User goal:
{goal}

Context:
{json.dumps(context, default=str)}
""".strip()

    @staticmethod
    def _parse_response(response: str) -> List[Dict[str, Any]]:
        text = response.strip()

        # Handle accidental markdown fences.
        if text.startswith("```"):
            lines = text.splitlines()

            if lines and lines[0].startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            text = "\n".join(lines).strip()

        parsed = json.loads(text)

        if not isinstance(parsed, list):
            raise ValueError("Planner response must be a JSON array")

        return parsed

    @staticmethod
    def _normalize_tasks(
        raw_tasks: List[Dict[str, Any]],
    ) -> List[TaskSpec]:
        tasks: List[TaskSpec] = []

        for index, raw in enumerate(raw_tasks):
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Invalid task at index {index}"
                )

            task_type = raw.get("type")

            if not task_type:
                raise ValueError(
                    f"Task {index} has no type"
                )

            task = TaskSpec(
                id=raw.get("id") or f"task_{index + 1}",
                type=task_type,
                name=raw.get("name"),
                parameters=dict(
                    raw.get(
                        "parameters",
                        raw.get("params", {}),
                    )
                ),
                capability=raw.get(
                    "capability",
                    task_type,
                ),
                preferred_executor=raw.get(
                    "preferred_executor"
                ),
                preferred_device=raw.get(
                    "preferred_device"
                ),
                depends_on=list(
                    raw.get("depends_on", [])
                ),
                timeout=raw.get("timeout"),
                retry_count=int(
                    raw.get("retry_count", 0)
                ),
                metadata=dict(
                    raw.get("metadata", {})
                ),
            )

            tasks.append(task)

        return tasks

    def _fallback_decompose(
        self,
        goal: str,
        context: Dict[str, Any],
    ) -> List[TaskSpec]:
        goal_lower = goal.lower()

        if "search" in goal_lower:
            return [
                TaskSpec(
                    id="task_1",
                    type="search",
                    capability="search",
                    parameters={"query": goal},
                )
            ]

        if "website" in goal_lower or "browser" in goal_lower:
            url = context.get("url")

            if url:
                return [
                    TaskSpec(
                        id="task_1",
                        type="browser_navigate",
                        capability="browser",
                        parameters={"url": url},
                    )
                ]

        if (
            "notify" in goal_lower
            or "notification" in goal_lower
        ):
            return [
                TaskSpec(
                    id="task_1",
                    type="notification",
                    capability="notification",
                    parameters={
                        "title": "Notification",
                        "message": goal,
                    },
                )
            ]

        return [
            TaskSpec(
                id="task_1",
                type="llm_reason",
                capability="llm",
                parameters={"goal": goal},
            )
        ]