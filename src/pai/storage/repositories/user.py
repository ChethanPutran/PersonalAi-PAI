"""User repository — persistent user records."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.observability.tracer import get_tracer
from pai.storage.models import UserModel
from pai.storage.repositories.base import BaseRepository


class UserRepository(BaseRepository[UserModel]):
    """Repository for persistent user records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, user_id: str) -> UserModel | None:
        """Get a user by ID.

        Not traced — this is a single-row indexed lookup.
        Callers wrap it in a span if they need to distinguish
        present vs. missing (e.g. PluginManager.set_user_plugin_config).
        """
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        return result.scalar_one_or_none()

    async def exists(self, user_id: str) -> bool:
        """Return whether a user exists."""
        # Intentionally not traced — delegates to `get`, and callers
        # that care about the answer already have their own span.
        return await self.get(user_id) is not None

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create(self, user_id: str) -> UserModel:
        """Create a new user.

        Traced so you can spot the actual INSERT + flush duration
        when auth flows start getting slow.
        """
        tracer = get_tracer()
        async with tracer.span(
            "user.create",
            user_id=user_id,
        ) as span:
            user = UserModel(id=user_id)
            self.session.add(user)
            await self.session.flush()
            span.set_attribute("row_id", user.id)
            return user

    async def get_or_create(self, user_id: str) -> UserModel:
        """Return an existing user or create one.

        The `was_created` attribute tells you whether this call
        hit the INSERT path — useful for auditing auth flows.
        """
        tracer = get_tracer()
        async with tracer.span(
            "user.get_or_create",
            user_id=user_id,
        ) as span:
            user = await self.get(user_id)

            if user is not None:
                span.set_attribute("was_created", False)
                return user

            user = await self.create(user_id)
            span.set_attribute("was_created", True)
            return user

    async def delete(self, user_id: str) -> bool:
        """Delete a user. Returns True if a user existed."""
        tracer = get_tracer()
        async with tracer.span(
            "user.delete",
            user_id=user_id,
        ) as span:
            user = await self.get(user_id)

            if user is None:
                span.set_attribute("deleted", False)
                return False

            await self.session.delete(user)
            await self.session.flush()
            span.set_attribute("deleted", True)
            return True