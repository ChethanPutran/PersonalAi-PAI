from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pai.storage.models import ChatSessionModel, ChatMessageModel


import json
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID

def _json_default(value):
    """Fallback encoder for SQLAlchemy JSON columns."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )

def _json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- sessions ----

    async def get_or_create_session(
        self,
        user_id: str,
        session_id: str,
        *,
        title: Optional[str] = None,
    ) -> ChatSessionModel:
        """Fetch the session if it belongs to `user_id`, else create it.

        If `session_id` exists but belongs to a different user, raises
        PermissionError — prevents session id guessing across users.
        """
        result = await self.session.execute(
            select(ChatSessionModel).where(
                ChatSessionModel.id == session_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            if existing.user_id != user_id:
                raise PermissionError(
                    f"session {session_id} does not belong to user"
                )
            return existing

        session = ChatSessionModel(
            id=session_id,
            user_id=user_id,
            title=(title or "New chat")[:256],
            message_count=0,
            last_message_at=_now(),
            metadata_json={},
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def list_sessions(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSessionModel]:
        result = await self.session.execute(
            select(ChatSessionModel)
            .where(ChatSessionModel.user_id == user_id)
            .order_by(
                ChatSessionModel.last_message_at.desc().nullslast(),
                ChatSessionModel.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_session_with_messages(
        self,
        session_id: str,
        user_id: str,
    ) -> Optional[ChatSessionModel]:
        result = await self.session.execute(
            select(ChatSessionModel)
            .where(
                ChatSessionModel.id == session_id,
                ChatSessionModel.user_id == user_id,
            )
            .options(selectinload(ChatSessionModel.messages))
        )
        return result.scalar_one_or_none()

    async def rename_session(
        self,
        session_id: str,
        user_id: str,
        title: str,
    ) -> bool:
        result = await self.session.execute(
            select(ChatSessionModel).where(
                ChatSessionModel.id == session_id,
                ChatSessionModel.user_id == user_id,
            )
        )
        session = result.scalar_one_or_none()
        if session is None:
            return False
        session.title = title[:256]
        await self.session.flush()
        return True

    async def delete_session(
        self,
        session_id: str,
        user_id: str,
    ) -> bool:
        result = await self.session.execute(
            select(ChatSessionModel).where(
                ChatSessionModel.id == session_id,
                ChatSessionModel.user_id == user_id,
            )
        )
        session = result.scalar_one_or_none()
        if session is None:
            return False
        await self.session.delete(session)
        await self.session.flush()
        return True

    # ---- messages ----

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        plan_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ChatMessageModel:
        msg = ChatMessageModel(
            id=_new_id(),
            session_id=session_id,
            role=role,
            content=content,
            plan_id=plan_id,
            task_id=task_id,
            status=status,
            metadata_json=_json_safe(metadata or {}),
            created_at=_now(),
        )
        self.session.add(msg)

        # bump the session's counters
        result = await self.session.execute(
            select(ChatSessionModel).where(
                ChatSessionModel.id == session_id,
            )
        )
        session = result.scalar_one_or_none()
        if session is not None:
            session.message_count = (session.message_count or 0) + 1
            session.last_message_at = msg.created_at

        await self.session.flush()
        return msg