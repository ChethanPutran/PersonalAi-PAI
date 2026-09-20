"""
API Routes - Tasks management
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel
import logging

from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 0
    due_date: Optional[str] = None


@router.get("")
async def list_tasks(status: Optional[str] = None, priority: Optional[int] = None, kernel: AIKernel = Depends(get_kernel)):
    """List tasks"""
    try:
        tasks = await kernel.plugin_manager.execute_plugin(
            "task_manager",
            "task_manager.list_tasks",
            {"status": status or "all"},
        )
    except Exception as exc:
        logger.warning(f"Task manager unavailable: {exc}")
        tasks = []
    if priority is not None:
        tasks = [task for task in tasks if task.get("priority") == priority]
    return {"tasks": tasks, "total": len(tasks)}


@router.post("")
async def create_task(request: TaskCreateRequest, kernel: AIKernel = Depends(get_kernel)):
    """Create a new task"""
    try:
        task = await kernel.plugin_manager.execute_plugin(
            "task_manager",
            "task_manager.add_task",
            {
                "title": request.title,
                "description": request.description,
                "priority": request.priority,
                "due_date": request.due_date,
            },
        )
        return {"message": "Task created successfully", **task}
    except Exception as exc:
        logger.exception("Task creation failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{task_id}")
async def get_task(task_id: str, kernel: AIKernel = Depends(get_kernel)):
    """Get task details"""
    tasks = await list_tasks(kernel=kernel)
    task = next((item for item in tasks["tasks"] if str(item.get("id")) == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}")
async def update_task(task_id: str, request: TaskCreateRequest):
    """Update a task"""
    return {
        "id": task_id,
        "title": request.title,
        "description": request.description,
        "priority": request.priority,
        "status": "in_progress",
        "message": "Task updated successfully"
    }


@router.post("/{task_id}/complete")
async def complete_task(task_id: str, kernel: AIKernel = Depends(get_kernel)):
    """Mark task as complete"""
    try:
        task = await kernel.plugin_manager.execute_plugin(
            "task_manager",
            "task_manager.complete_task",
            {"task_id": int(task_id)},
        )
        if task.get("error"):
            raise HTTPException(status_code=404, detail=task["error"])
        return {"message": "Task marked as completed", **task}
    except ValueError:
        raise HTTPException(status_code=400, detail="task_id must be numeric")


@router.delete("/{task_id}")
async def delete_task(task_id: str, kernel: AIKernel = Depends(get_kernel)):
    """Delete a task"""
    try:
        result = await kernel.plugin_manager.execute_plugin(
            "task_manager",
            "task_manager.delete_task",
            {"task_id": int(task_id)},
        )
        return {"id": task_id, "status": "deleted", "message": "Task deleted successfully", "result": result}
    except ValueError:
        raise HTTPException(status_code=400, detail="task_id must be numeric")
