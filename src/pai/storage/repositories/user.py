from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.storage.models import UserModel
from pai.storage.repositories.base import BaseRepository


class UserRepository(BaseRepository[UserModel]):
    """Repository for persistent user records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, user_id: str) -> UserModel | None:
        """Get a user by ID."""
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        return result.scalar_one_or_none()

    async def exists(self, user_id: str) -> bool:
        """Return whether a user exists."""
        return await self.get(user_id) is not None

    async def create(self, user_id: str) -> UserModel:
        """Create a new user."""
        user = UserModel(id=user_id)

        self.session.add(user)
        await self.session.flush()

        return user

    async def get_or_create(self, user_id: str) -> UserModel:
        """Return an existing user or create one."""
        user = await self.get(user_id)

        if user is not None:
            return user

        return await self.create(user_id)

    async def delete(self, user_id: str) -> bool:
        """Delete a user. Returns True if a user existed."""
        user = await self.get(user_id)

        if user is None:
            return False

        await self.session.delete(user)
        await self.session.flush()

        return True