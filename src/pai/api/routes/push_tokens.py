"""Device push-token registration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db
from pai.app_context import PAIAppContext, get_app_context
from pai.notifications.push_service import PushTokenRepository


router = APIRouter(prefix="/devices", tags=["devices"])


class PushTokenIn(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    platform: str = Field(pattern="^(android|ios)$")


@router.post("/{device_id}/push-token")
async def register_push_token(
    device_id: str,
    payload: PushTokenIn,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    if getattr(device, "user_id", None) != user_id:
        raise HTTPException(403, "Not your device")

    repo = PushTokenRepository(session)
    row = await repo.upsert(
        device_id=device_id,
        user_id=user_id,
        token=payload.token,
        platform=payload.platform,
    )
    await session.commit()
    return {"ok": True, "id": row.id, "token": row.token}


@router.delete("/{device_id}/push-token", status_code=204)
async def unregister_push_token(
    device_id: str,
    token: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:
        raise HTTPException(404, "Device not found")

    repo = PushTokenRepository(session)
    await repo.delete_token(token)
    await session.commit()
    return Response(status_code=204)