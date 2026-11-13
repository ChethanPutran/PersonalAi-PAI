from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from sqlalchemy import select
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
    """Chat session + message persistence.

    Two construction modes:

      • ``ChatRepository(session)`` — uses the passed session for every
        operation. Suitable when the caller already owns an ``AsyncSession``
        (request handler, manager ``_with_session`` block).

      • ``ChatRepository(session_factory=factory)`` — opens a fresh session
        per operation. Suitable for long-lived holders like ``TaskOrchestrator``
        that outlive any single request.
    """

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        *,
        session_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        if session is None and session_factory is None:
            raise ValueError(
                "ChatRepository requires either `session` or `session_factory`"
            )
        self._bound_session = session
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        if self._session_factory is not None:
            async with self._session_factory() as s:
                yield s
        else:
            assert self._bound_session is not None
            yield self._bound_session

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
        async with self._session() as session:
            result = await session.execute(
                select(ChatSessionModel).where(ChatSessionModel.id == session_id)
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                if existing.user_id != user_id:
                    raise PermissionError(
                        f"session {session_id} does not belong to user"
                    )
                return existing

            new_session = ChatSessionModel(
                id=session_id,
                user_id=user_id,
                title=(title or "New chat")[:256],
                message_count=0,
                last_message_at=_now(),
                metadata_json={},
            )
            session.add(new_session)
            await session.flush()
            return new_session

    async def list_sessions(
        self, user_id: str, *, limit: int = 50, offset: int = 0,
    ) -> list[ChatSessionModel]:
        async with self._session() as session:
            result = await session.execute(
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
        self, session_id: str, user_id: str,
    ) -> Optional[ChatSessionModel]:
        async with self._session() as session:
            result = await session.execute(
                select(ChatSessionModel)
                .where(
                    ChatSessionModel.id == session_id,
                    ChatSessionModel.user_id == user_id,
                )
                .options(selectinload(ChatSessionModel.messages))
            )
            return result.scalar_one_or_none()

    async def rename_session(
        self, session_id: str, user_id: str, title: str,
    ) -> bool:
        async with self._session() as session:
            result = await session.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.id == session_id,
                    ChatSessionModel.user_id == user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            row.title = title[:256]
            await session.flush()
            return True

    async def delete_session(self, session_id: str, user_id: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.id == session_id,
                    ChatSessionModel.user_id == user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.flush()
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
        async with self._session() as session:
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
            session.add(msg)
            # bump the session's counters
            result = await session.execute(
                select(ChatSessionModel).where(ChatSessionModel.id == session_id)
            )
            chat_session = result.scalar_one_or_none()
            if chat_session is not None:
                chat_session.message_count = (chat_session.message_count or 0) + 1
                chat_session.last_message_at = msg.created_at

            await session.flush()
            return msg

    # ---- NEW: recent messages for prompt injection ----

    async def recent_messages(
        self,
        session_id: str,
        *,
        user_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[ChatMessageModel]:
        """Return the most recent messages for a session, oldest-first.

        Used by the orchestrator's chat reply path to inject conversation
        continuity into the LLM prompt. ``user_id`` is optional but
        recommended — it asserts ownership by joining through the parent
        session, preventing cross-user session-id guessing.

        Order is oldest-first so the caller can render a chronological
        transcript without reversing.
        """
        async with self._session() as session:
            q = select(ChatMessageModel).where(
                ChatMessageModel.session_id == session_id,
            )

            if user_id is not None:
                # Verify ownership by joining to the parent session row.
                q = (
                    q.join(
                        ChatSessionModel,
                        ChatSessionModel.id == ChatMessageModel.session_id,
                    )
                    .where(ChatSessionModel.user_id == user_id)
                )

            q = q.order_by(ChatMessageModel.created_at.desc()).limit(limit)
            result = await session.execute(q)
            messages = list(result.scalars().all())
            return list(reversed(messages))