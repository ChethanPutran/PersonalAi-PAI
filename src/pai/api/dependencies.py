from __future__ import annotations

from select import select
from typing import AsyncIterator

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

async def get_current_admin(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> str:
    """
    Require an authenticated admin. Returns the user_id.

    We hit the DB on every admin request so that revoking is_admin
    takes effect immediately (no stale JWT claim).
    """
    result = await session.execute(
        select(UserModel).where(UserModel.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user.id