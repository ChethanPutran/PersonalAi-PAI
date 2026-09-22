from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.storage.models import UserPluginModel
from pai.storage.repositories.base import BaseRepository



class UserPluginRepository(BaseRepository[UserPluginModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, user_id: str, plugin_id: str) -> UserPluginModel | None:
        result = await self.session.execute(
            select(UserPluginModel).where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.plugin_id == plugin_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_enabled(self, user_id: str, plugin_id: str) -> bool:
        record = await self.get(user_id, plugin_id)
        return record is not None and record.is_enabled

    async def set_enabled(
        self, user_id: str, plugin_id: str, enabled: bool,
    ) -> UserPluginModel:
        record = await self.get(user_id, plugin_id)
        if record is None:
            record = UserPluginModel(
                user_id=user_id, plugin_id=plugin_id, is_enabled=enabled,
            )
            self.session.add(record)
        else:
            record.is_enabled = enabled
        await self.session.flush()
        return record

    async def list_for_user(self, user_id: str) -> list[UserPluginModel]:
        result = await self.session.execute(
            select(UserPluginModel)
            .where(UserPluginModel.user_id == user_id)
            .order_by(UserPluginModel.plugin_id)
        )
        return list(result.scalars().all())

    async def list_enabled(self, user_id: str) -> list[UserPluginModel]:
        result = await self.session.execute(
            select(UserPluginModel).where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.is_enabled.is_(True),
            )
        )
        return list(result.scalars().all())

    # ---------------- config ----------------

    async def get_config(self, user_id: str, plugin_id: str) -> dict[str, Any]:
        record = await self.get(user_id, plugin_id)
        return dict(record.metadata_json or {}) if record else {}

    async def set_config(
        self, user_id: str, plugin_id: str, config: dict[str, Any],
    ) -> UserPluginModel:
        record = await self.get(user_id, plugin_id)
        if record is None:
            record = UserPluginModel(
                user_id=user_id, plugin_id=plugin_id, is_enabled=False,
            )
            self.session.add(record)
        record.metadata_json = dict(config)
        await self.session.flush()
        return record

    async def delete(self, user_id: str, plugin_id: str) -> bool:
        result = await self.session.execute(
            delete(UserPluginModel).where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.plugin_id == plugin_id,
            )
        )
        return result.rowcount > 0