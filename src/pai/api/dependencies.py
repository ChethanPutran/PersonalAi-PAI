from __future__ import annotations

from select import select
from typing import AsyncIterator, Optional

from sqlalchemy import select
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pai.app_context import PAIAppContext, get_app_context
from fastapi import Depends, HTTPException, Request

from pai.storage.models import UserModel


async def get_db(
    context: PAIAppContext = Depends(get_app_context),
) -> AsyncIterator[AsyncSession]:
    """
    Yield a per-request AsyncSession.

    Commits on successful response, rolls back on exception, always closes.
    """
    async with context.database.session() as session:
        yield session



async def get_current_user(request: Request) -> str:
    """
    Return the authenticated user_id.

    The AuthMiddleware parses the JWT and stores the user_id on
    request.state.user_id before the route runs. If it's missing,
    the request was unauthenticated — reject.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id

async def user_is_admin(session: AsyncSession, user_id: str) -> bool:
    """The one admin determination (DB, so revoking is_admin takes effect immediately)."""
    result = await session.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    return bool(user is not None and user.is_admin)


async def get_current_admin(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> str:
    """
    Require an authenticated admin. Returns the user_id.

    We hit the DB on every admin request so that revoking is_admin
    takes effect immediately (no stale JWT claim).
    """
    if not await user_is_admin(session, user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id


SOURCE_DEVICE_HEADER = "X-PAI-Device-Id"


async def get_source_device_id(request: Request) -> Optional[str]:
    """The device this request comes FROM (clients send it on every call), or None (web, scripts).

    Only used to tell "a device acting on itself" (local) from "acting on another device"
    (remote, needs the target's opt-in). It is never trusted for ownership: the target device is
    always checked against the logged-in user separately.
    """
    return (request.headers.get(SOURCE_DEVICE_HEADER) or "").strip() or None
