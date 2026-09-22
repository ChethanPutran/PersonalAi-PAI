from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.storage.models import DeviceModel
from pai.storage.repositories.base import BaseRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceRepository(BaseRepository[DeviceModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, device_id: str) -> DeviceModel | None:
        result = await self.session.execute(
            select(DeviceModel).where(DeviceModel.id == device_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[DeviceModel]:
        result = await self.session.execute(
            select(DeviceModel).order_by(DeviceModel.created_at)
        )
        return list(result.scalars().all())

    async def list_for_user(self, user_id: str) -> list[DeviceModel]:
        result = await self.session.execute(
            select(DeviceModel)
            .where(DeviceModel.user_id == user_id)
            .order_by(DeviceModel.created_at)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        device_id: str,
        *,
        name: str = "",
        device_type: str = "unknown",
        platform: str = "unknown",
        platform_version: str = "",
        os_version: str = "",
        architecture: str = "",
        hostname: str = "",
        app_version: str = "",
        runtime_version: str = "",
        status: str = "offline",
        capabilities: Optional[list[Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ) -> DeviceModel:
        record = await self.get(device_id)

        if record is None:
            record = DeviceModel(
                id=device_id,
                name=name,
                device_type=device_type,
                platform=platform,
                platform_version=platform_version,
                os_version=os_version,
                architecture=architecture,
                hostname=hostname,
                app_version=app_version,
                runtime_version=runtime_version,
                status=status,
                capabilities=capabilities or [],
                metadata_json=metadata or {},
                user_id=user_id,
                last_seen=last_seen or _utc_now(),
            )
            self.session.add(record)
        else:
            # Only overwrite non-empty fields so a partial update
            # doesn't wipe existing data.
            if name:
                record.name = name
            if device_type and device_type != "unknown":
                record.device_type = device_type
            if platform and platform != "unknown":
                record.platform = platform
            if platform_version:
                record.platform_version = platform_version
            if os_version:
                record.os_version = os_version
            if architecture:
                record.architecture = architecture
            if hostname:
                record.hostname = hostname
            if app_version:
                record.app_version = app_version
            if runtime_version:
                record.runtime_version = runtime_version
            if status:
                record.status = status
            if capabilities is not None:
                record.capabilities = capabilities
            if metadata is not None:
                record.metadata_json = metadata
            if user_id is not None:
                record.user_id = user_id
            record.last_seen = last_seen or _utc_now()

        await self.session.flush()
        return record

    async def update_status(self, device_id: str, status: str) -> bool:
        record = await self.get(device_id)
        if record is None:
            return False
        record.status = status
        record.last_seen = _utc_now()
        await self.session.flush()
        return True

    async def touch(self, device_id: str) -> bool:
        """Update last_seen without changing status."""
        record = await self.get(device_id)
        if record is None:
            return False
        record.last_seen = _utc_now()
        await self.session.flush()
        return True

    async def delete(self, device_id: str) -> bool:
        result = await self.session.execute(
            delete(DeviceModel).where(DeviceModel.id == device_id)
        )
        return result.rowcount > 0