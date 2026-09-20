from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Dict, Any

from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

router = APIRouter(prefix="/api/v1/preferences", tags=["preferences"])


@router.post("/set")
async def set_preference(payload: Dict[str, Any] = Body(...), kernel: AIKernel = Depends(get_kernel)):
    """Set a user preference: expects {user_id, key, value} in body."""
    user_id = payload.get("user_id")
    key = payload.get("key")
    value = payload.get("value")
    if not user_id or not key:
        raise HTTPException(status_code=400, detail="user_id and key required")
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    rec = await pm.set_user_preference(user_id, key, value)
    if rec is None:
        raise HTTPException(status_code=500, detail='Failed to set preference')
    # also update kernel context
    try:
        await kernel.context_manager.update({'preferences': {key: value}})
    except Exception:
        pass
    return {"status": "ok", "key": key, "value": value}


@router.get('/get/{user_id}')
async def get_preferences(user_id: str, kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    prefs = await pm.get_user_preferences(user_id)
    return {"user_id": user_id, "preferences": prefs}
