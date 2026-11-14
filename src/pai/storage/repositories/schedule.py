"""CRUD + due-query for schedules."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pai.storage.models import ScheduleModel


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, schedule_id: str) -> Optional[ScheduleModel]:
        return await self.session.get(ScheduleModel, schedule_id)

    async def list_for_user(self, user_id: str) -> list[ScheduleModel]:
        result = await self.session.execute(
            select(ScheduleModel)
            .where(ScheduleModel.user_id == user_id)
            .order_by(ScheduleModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_all(self, limit: int = 500) -> list[ScheduleModel]:
        result = await self.session.execute(
            select(ScheduleModel)
            .order_by(ScheduleModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def find_due(
        self, now: datetime, limit: int = 100,
    ) -> list[ScheduleModel]:
        """Atomically lock due rows so two workers can't double-fire."""
        result = await self.session.execute(
            select(ScheduleModel)
            .where(
                ScheduleModel.enabled.is_(True),
                ScheduleModel.next_fire_at.is_not(None),
                ScheduleModel.next_fire_at <= now,
            )
            .order_by(ScheduleModel.next_fire_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def create(self, row: ScheduleModel) -> ScheduleModel:
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, schedule_id: str) -> bool:
        row = await self.get(schedule_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def mark_fired(
        self, schedule_id: str,
        fired_at: datetime,
        next_fire_at: Optional[datetime],
    ) -> None:
        await self.session.execute(
            update(ScheduleModel)
            .where(ScheduleModel.id == schedule_id)
            .values(
                last_fired_at=fired_at,
                next_fire_at=next_fire_at,
                enabled=next_fire_at is not None,
                updated_at=datetime.now(timezone.utc),
            )
        )