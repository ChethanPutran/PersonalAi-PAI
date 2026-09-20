from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.storage.models import UserPluginModel
from pai.storage.repositories.base import BaseRepository


class UserPluginRepository(BaseRepository[UserPluginModel]):
    """
    Repository for per-user plugin state.

    This repository knows nothing about:
    - plugin implementations
    - plugin lifecycle
    - plugin execution
    - devices
    - authorization policy

    It only persists user/plugin state.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(
        self,
        user_id: str,
        plugin_id: str,
    ) -> UserPluginModel | None:
        """Get a user's plugin record."""
        result = await self.session.execute(
            select(UserPluginModel).where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.plugin_id == plugin_id,
            )
        )

        return result.scalar_one_or_none()

    async def is_enabled(
        self,
        user_id: str,
        plugin_id: str,
    ) -> bool:
        """
        Return whether the user has enabled the plugin.

        If no explicit record exists, return False.
        """
        record = await self.get(user_id, plugin_id)

        return record is not None and record.is_enabled

    async def set_enabled(
        self,
        user_id: str,
        plugin_id: str,
        enabled: bool,
    ) -> UserPluginModel:
        """
        Enable or disable a plugin for a user.

        This ONLY changes persistent user state.

        It does not start or stop a plugin runtime.
        """
        record = await self.get(user_id, plugin_id)

        if record is None:
            record = UserPluginModel(
                user_id=user_id,
                plugin_id=plugin_id,
                is_enabled=enabled,
            )

            self.session.add(record)
        else:
            record.is_enabled = enabled

        await self.session.flush()

        return record

    async def enable(
        self,
        user_id: str,
        plugin_id: str,
    ) -> UserPluginModel:
        """Enable a plugin for a user."""
        return await self.set_enabled(
            user_id=user_id,
            plugin_id=plugin_id,
            enabled=True,
        )

    async def disable(
        self,
        user_id: str,
        plugin_id: str,
    ) -> UserPluginModel:
        """Disable a plugin for a user."""
        return await self.set_enabled(
            user_id=user_id,
            plugin_id=plugin_id,
            enabled=False,
        )

    async def list_for_user(
        self,
        user_id: str,
    ) -> list[UserPluginModel]:
        """Return all plugin records belonging to a user."""
        result = await self.session.execute(
            select(UserPluginModel)
            .where(UserPluginModel.user_id == user_id)
            .order_by(UserPluginModel.plugin_id)
        )

        return list(result.scalars().all())

    async def list_enabled(
        self,
        user_id: str,
    ) -> list[UserPluginModel]:
        """Return only enabled plugins for a user."""
        result = await self.session.execute(
            select(UserPluginModel)
            .where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.is_enabled.is_(True),
            )
            .order_by(UserPluginModel.plugin_id)
        )

        return list(result.scalars().all())

    async def delete(
        self,
        user_id: str,
        plugin_id: str,
    ) -> bool:
        """Delete a user's plugin record."""
        result = await self.session.execute(
            delete(UserPluginModel).where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.plugin_id == plugin_id,
            )
        )

        return result.rowcount > 0