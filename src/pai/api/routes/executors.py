"""
API Routes - Executor management
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import logging

from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/executors", tags=["executors"])
"""
GET  /api/v1/executors
GET  /api/v1/executors/{id}
POST /internal/executors/{id}/execute
"""

@router.get("")
async def list_executors(kernel: AIKernel = Depends(get_kernel)):
    """List all executors"""
    configured = getattr(kernel.executor_scheduler, "_executors", {}) or {}
    executors = [
        {
            "id": executor_id,
            "device_name": executor_id.title(),
            "type": executor_id,
            "endpoint": endpoint,
            "status": "configured",
            "capabilities": [],
            "resources": {},
        }
        for executor_id, endpoint in configured.items()
    ]
    return {
        "executors": executors,
        "total": len(executors),
        "online": sum(1 for item in executors if item["status"] in {"online", "configured"}),
    }


@router.get("/{executor_id}")
async def get_executor(executor_id: str, kernel: AIKernel = Depends(get_kernel)):
    """Get executor details"""
    endpoint = getattr(kernel.executor_scheduler, "_executors", {}).get(executor_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Executor not found")
    return {
        "id": executor_id,
        "device_name": executor_id.title(),
        "type": executor_id,
        "endpoint": endpoint,
        "status": "configured",
        "capabilities": [],
        "resources": {},
    }


@router.get("/{executor_id}/tasks")
async def get_executor_tasks(executor_id: str):
    """Get tasks running on executor"""
    return {
        "executor_id": executor_id,
        "running_tasks": [
            {
                "task_id": "task-1",
                "type": "browser_automation",
                "status": "executing",
                "started_at": "2024-05-15T14:55:00Z"
            }
        ],
        "completed_tasks": 42,
        "failed_tasks": 1
    }


@router.post("/{executor_id}/submit-task")
async def submit_task(
    executor_id: str,
    task_data: dict,
    kernel: AIKernel = Depends(get_kernel),
):
    scheduler = kernel.executor_scheduler

    result = await scheduler.schedule_task(
        task_data,
        executor_id,
    )

    return result