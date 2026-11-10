"""Device repository — persistent device metadata."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.observability.tracer import get_tracer
from pai.storage.models import DeviceModel
from pai.storage.repositories.base import BaseRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceRepository(BaseRepository[DeviceModel]):
    """Persists device metadata and last-seen timestamps."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, device_id: str) -> DeviceModel | None:
        """Single-row lookup by device id. Not traced (fast path)."""
        result = await self.session.execute(
            select(DeviceModel).where(DeviceModel.id == device_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[DeviceModel]:
        """Every device in the database.

        Traced — used by `_load_from_db` during startup, where a slow
        query delays the whole server coming online.
        """
        tracer = get_tracer()
        async with tracer.span("device.list_all") as span:
            result = await self.session.execute(
                select(DeviceModel).order_by(DeviceModel.created_at)
            )
            rows = list(result.scalars().all())
            span.set_attribute("count", len(rows))
            return rows

    async def list_for_user(self, user_id: str) -> list[DeviceModel]:
        """Devices owned by a specific user.

        Traced — CapabilityRouter iterates this on every index rebuild,
        so a slow query directly slows down capability resolution.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device.list_for_user",
            user_id=user_id,
        ) as span:
            result = await self.session.execute(
                select(DeviceModel)
                .where(DeviceModel.user_id == user_id)
                .order_by(DeviceModel.created_at)
            )
            rows = list(result.scalars().all())
            span.set_attribute("count", len(rows))
            span.set_attribute(
                "device_ids", [r.id for r in rows],
            )
            return rows

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

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
        """Insert or update a device row.

        The update path intentionally only overwrites non-empty fields
        — this lets the mobile app send a partial payload on re-register
        without wiping fields it didn't include. The span's `was_insert`
        attribute tells you which path ran.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device.upsert",
            device_id=device_id,
            user_id=user_id,
            status=status,
        ) as span:
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
                span.set_attribute("was_insert", True)
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
                span.set_attribute("was_insert", False)

            await self.session.flush()
            return record

    async def update_status(self, device_id: str, status: str) -> bool:
        """Set a device's status (online/offline/error) and last_seen.

        Traced because this fires on every connect/disconnect —
        a burst of reconnects hits this hot path repeatedly.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device.update_status",
            device_id=device_id,
            status=status,
        ) as span:
            record = await self.get(device_id)
            if record is None:
                span.set_attribute("found", False)
                return False
            record.status = status
            record.last_seen = _utc_now()
            await self.session.flush()
            span.set_attribute("found", True)
            return True

    async def touch(self, device_id: str) -> bool:
        """Update last_seen without changing status.

        Traced separately from `update_status` so heartbeat traffic
        doesn't get confused with state transitions in the trace.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device.touch",
            device_id=device_id,
        ) as span:
            record = await self.get(device_id)
            if record is None:
                span.set_attribute("found", False)
                return False
            record.last_seen = _utc_now()
            await self.session.flush()
            span.set_attribute("found", True)
            return True

    async def delete(self, device_id: str) -> bool:
        """Remove a device row. Returns True if a row existed."""
        tracer = get_tracer()
        async with tracer.span(
            "device.delete",
            device_id=device_id,
        ) as span:
            result = await self.session.execute(
                delete(DeviceModel).where(DeviceModel.id == device_id)
            )
            deleted = result.rowcount > 0
            span.set_attribute("deleted", deleted)
            return deleted