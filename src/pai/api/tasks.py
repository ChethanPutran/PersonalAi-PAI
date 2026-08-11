"""
API Routes - Tasks management
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 0
    due_date: Optional[str] = None


@router.get("")
async def list_tasks(status: Optional[str] = None, priority: Optional[int] = None):
    """List tasks"""
    return {
        "tasks": [
            {
                "id": "task-1",
                "title": "Research AI papers",
                "description": "Find latest papers on multimodal AI",
                "status": "in_progress",
                "priority": 2,
                "created_at": "2024-05-15T10:00:00Z"
            },
            {
                "id": "task-2",
                "title": "Schedule meeting",
                "description": "Schedule meeting with team",
                "status": "pending",
                "priority": 1,
                "due_date": "2024-05-16T14:00:00Z"
            }
        ],
        "total": 2
    }


@router.post("")
async def create_task(request: TaskCreateRequest):
    """Create a new task"""
    return {
        "id": "task-new-123",
        "title": request.title,
        "description": request.description,
        "priority": request.priority,
        "status": "pending",
        "created_at": "2024-05-15T14:59:00Z",
        "message": "Task created successfully"
    }


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get task details"""
    return {
        "id": task_id,
        "title": "Example Task",
        "description": "Task description",
        "status": "in_progress",
        "priority": 1,
        "created_at": "2024-05-15T10:00:00Z",
        "updated_at": "2024-05-15T14:00:00Z"
    }


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
async def complete_task(task_id: str):
    """Mark task as complete"""
    return {
        "id": task_id,
        "status": "completed",
        "completed_at": "2024-05-15T14:59:00Z",
        "message": "Task marked as completed"
    }


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """Delete a task"""
    return {
        "id": task_id,
        "status": "deleted",
        "message": "Task deleted successfully"
    }
