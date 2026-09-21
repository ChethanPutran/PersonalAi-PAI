"""
API Routes - Tasks management
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel
import logging

from pai.app_context import get_orchestrator
from pai.orchestration.orchestrator import TaskOrchestrator
from pai.tasks.models import TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])

class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 0
    due_date: Optional[str] = None


@router.get("")
async def list_tasks(status: Optional[str] = None, priority: Optional[int] = None, orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """List tasks"""
    try:
        tasks = await orchestrator.task_manager.list_tasks(status=TaskStatus(status) if status else None)
        return {"tasks": tasks, "total": len(tasks)}
    except Exception as exc:
        logger.error(f"Error listing tasks: {exc}")
        raise HTTPException(
                status_code=404,
                detail="Error listing tasks: " + str(exc),
            )

@router.post("/{task_id}/pause")
async def pause_task(task_id: str,orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """Pause a task"""
    task = orchestrator.task_manager.pause(task_id)

    if not task:
        raise HTTPException(404, "Task not found")

    return task

@router.post("/{task_id}/resume")
async def resume_task(task_id: str,orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """Resume a task"""
    task = orchestrator.task_manager.resume(task_id)

    if not task:
        raise HTTPException(404, "Task not found")

    return task



@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str,orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """Cancel a task"""

    task = orchestrator.task_manager.cancel(task_id)

    if not task:
        raise HTTPException(404, "Task not found")

    return task


@router.get("/{task_id}")
async def get_task(task_id: str, orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """Get task details"""
    tasks = await list_tasks(orchestrator=orchestrator)
    task = next((item for item in tasks["tasks"] if str(item.get("id")) == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task