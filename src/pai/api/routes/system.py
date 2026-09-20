"""
API Routes - System status and health
"""
from fastapi import APIRouter, Depends
import logging

from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status")
async def get_system_status(kernel: AIKernel = Depends(get_kernel)):
    """Get overall system status"""
    kernel_status = kernel.get_status()
    return {
        "status": "operational" if kernel_status.get("running") else "initialized",
        "components": {
            "kernel": kernel_status,
            "event_bus": "running" if kernel.event_bus._running else "initialized",
            "plugin_manager": {
                "initialized": kernel.plugin_manager._initialized,
                "plugins": len(kernel.plugin_manager._plugins),
            },
            "agent_manager": {
                "agents": len(kernel.agent_manager._agents),
            },
            "executor_manager": {
                "executors": len(kernel.executor_scheduler._executors),
            },
            "memory_service": {
                "initialized": kernel.memory_manager._initialized,
            },
        },
        "message": "Personal AI System is operational"
    }


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "system": "Personal AI",
        "version": "1.0.0"
    }


@router.get("/info")
async def get_system_info():
    """Get system information"""
    return {
        "name": "Personal AI (PAI)",
        "version": "1.0.0",
        "description": "Context-Aware Autonomous Personal Agent System",
        "components": [
            "AI Kernel",
            "Event Bus",
            "Plugin Manager",
            "Agent Manager",
            "Executor Manager",
            "Memory Service"
        ],
        "documentation": "/docs"
    }
