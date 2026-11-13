"""API Routes - Tasks management."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from pai.api.dependencies import get_current_user
from pai.api.routes.system import _serialize_task
from pai.app_context import get_orchestrator
from pai.orchestration.orchestrator import TaskOrchestrator
from pai.tasks.lifecycle import InvalidTaskTransition
from pai.tasks.models import Task, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ============================================================
# Helpers
# ============================================================

async def _require_owned_task(
    task_id: str,
    user_id: str,
    orchestrator: TaskOrchestrator,
) -> Task:
    """Fetch `task_id` and verify the caller owns it.

    Returns the Task if the caller may access it.
    Raises 404 if it doesn't exist; 403 if it belongs to someone
    else. Note: 403 rather than 404 on the cross-user case, so a
    caller cannot distinguish "doesn't exist" from "exists but is
    someone else's" by probing ids.
    """
    task = await orchestrator.task_manager.get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.user_id and task.user_id != user_id:
        raise HTTPException(403, "Task belongs to another user")
    return task


# ============================================================
# List
# ============================================================

@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """List tasks belonging to the authenticated user.

    Optional `?status=running` filter — must match a `TaskStatus`
    value or the route 400s. The previous version accepted a bare
    string, so `?status=RUNNING` (wrong case) or `?status=garbage`
    silently passed through to the store, which returned an empty
    list instead of telling the caller the filter was invalid.
    """
    parsed_status: Optional[TaskStatus] = None
    if status:
        try:
            parsed_status = TaskStatus(status)
        except ValueError as exc:
            raise HTTPException(
                400,
                f"Unknown status {status!r}. "
                f"Valid values: {[s.value for s in TaskStatus]}",
            ) from exc

    tm = orchestrator.task_manager
    try:
        # Was `tm.list_tasks(status=None, user_id=user_id)` — list_tasks()
        # never accepted user_id, so the call raised TypeError, the route
        # raised 500, and the Flutter TasksScreen swallowed the exception
        # and rendered an empty list. `list()` is the user-scoped method.
        raw = await tm.list(
            status=parsed_status,
            user_id=user_id,
        )
    except Exception as exc:
        logger.exception("list_tasks failed for user %s", user_id)
        raise HTTPException(500, f"Failed to list tasks: {exc}")

    return {"tasks": [_serialize_task(t) for t in (raw or [])]}


# ============================================================
# Get
# ============================================================

@router.get("/{task_id}")
async def get_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Get task details. Enforces ownership."""
    task = await _require_owned_task(task_id, user_id, orchestrator)
    return {"task": _serialize_task(task)}


# ============================================================
# Pause / resume / cancel
# ============================================================

@router.post("/{task_id}/pause")
async def pause_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Pause a task. Only the owner may pause."""
    await _require_owned_task(task_id, user_id, orchestrator)

    try:
        task = await orchestrator.task_manager.pause(task_id)
    except InvalidTaskTransition as exc:
        # Wrong state — e.g. trying to pause a completed task.
        # 409 (conflict) so the client can distinguish this from a
        # generic 400 validation error.
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("pause failed for task %s", task_id)
        raise HTTPException(500, f"Pause failed: {exc}")

    if task is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Resume a paused task. Only the owner may resume."""
    await _require_owned_task(task_id, user_id, orchestrator)

    try:
        task = await orchestrator.task_manager.resume(task_id)
    except InvalidTaskTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("resume failed for task %s", task_id)
        raise HTTPException(500, f"Resume failed: {exc}")

    if task is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    user_id: str = Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
) -> Dict[str, Any]:
    """Cancel a task. Only the owner may cancel.

    Note the `await` below — the previous version did
    `task = orchestrator.task_manager.cancel(task_id)` without it, which
    assigned a coroutine object to `task`, then the truthiness check on
    that coroutine passed, and FastAPI tried to serialize a coroutine as
    the response body — silently producing `{}` or erroring during JSON
    encoding depending on the FastAPI version.
    """
    await _require_owned_task(task_id, user_id, orchestrator)

    try:
        task = await orchestrator.task_manager.cancel(task_id)
    except Exception as exc:
        logger.exception("cancel failed for task %s", task_id)
        raise HTTPException(500, f"Cancel failed: {exc}")

    if task is None:
        raise HTTPException(404, "Task not found")
    return _serialize_task(task)