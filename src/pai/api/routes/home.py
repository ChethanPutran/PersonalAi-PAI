from fastapi import APIRouter, Depends

from pai.app_context import PAIAppContext, get_app_context

router = APIRouter()

# ============================================================
# Home
# ============================================================

@router.get("/")
async def root(context: PAIAppContext = Depends(get_app_context)):
    return {
        "name": "Personal AI System",
        "version": "1.0.0",
        "status": "running",
        "user_id": context.user_id,
        "session_id": context.session_id,
        "request_id": context.request_id,
        "source_device_id": context.source_device_id,
        "kernel": context.orchestrator.get_status(user_id=context.user_id),
    }