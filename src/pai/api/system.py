"""
API Routes - System status and health
"""
from fastapi import APIRouter, Depends
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status")
async def get_system_status():
    """Get overall system status"""
    return {
        "status": "operational",
        "components": {
            "kernel": "ready",
            "event_bus": "connected",
            "plugin_manager": "initialized",
            "agent_manager": "initialized",
            "executor_manager": "initialized",
            "memory_service": "initialized"
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
