"""Built-in capabilities for task queries."""
from __future__ import annotations

from typing import Any, Dict

from pai.orchestration.builtin_capabilities import BuiltinCapability
from pai.tasks.models import TaskStatus


def build_task_capabilities(task_manager) -> list[BuiltinCapability]:
    async def _list(params: Dict[str, Any], *, user_id: str, **_):
        status = params.get("status")
        tasks = await task_manager.list(
            user_id=user_id,
            status=TaskStatus(status) if status else None,
            limit=params.get("limit", 50),
        )
        return {
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "capability": t.capability,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tasks
            ]
        }

    async def _get(params: Dict[str, Any], *, user_id: str, **_):
        t = await task_manager.get(params["task_id"])
        if t.user_id != user_id:
            raise ValueError(f"Task {params['task_id']} not found")
        return t.to_dict()

    return [
        BuiltinCapability(
            id="task.list",
            description="List the user's existing tasks (past and current).",
            parameters={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
            handler=_list,
        ),
        BuiltinCapability(
            id="task.get",
            description="Get details of a specific task by id.",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
            handler=_get,
        ),
    ]