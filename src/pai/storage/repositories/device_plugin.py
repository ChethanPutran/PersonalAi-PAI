from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.storage.models import DevicePluginModel
from pai.storage.repositories.base import BaseRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DevicePluginRepository(BaseRepository[DevicePluginModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(
        self, device_id: str, plugin_id: str,
    ) -> DevicePluginModel | None:
        result = await self.session.execute(
            select(DevicePluginModel).where(
                DevicePluginModel.device_id == device_id,
                DevicePluginModel.plugin_id == plugin_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_installed(self, device_id: str, plugin_id: str) -> bool:
        record = await self.get(device_id, plugin_id)
        return record is not None and record.is_installed

    async def mark_installed(
        self,
        device_id: str,
        plugin_id: str,
        version: str,
        artifact_sha256: Optional[str] = None,
    ) -> DevicePluginModel:
        record = await self.get(device_id, plugin_id)
        now = _utc_now()
        if record is None:
            record = DevicePluginModel(
                device_id=device_id,
                plugin_id=plugin_id,
                version=version,
                is_installed=True,
                enabled_on_device=False,
                artifact_sha256=artifact_sha256,
                installed_at=now,
                last_error=None,
            )
            self.session.add(record)
        else:
            record.version = version
            record.is_installed = True
            record.artifact_sha256 = artifact_sha256
            record.installed_at = now
            record.last_error = None
        await self.session.flush()
        return record

    async def mark_uninstalled(self, device_id: str, plugin_id: str) -> bool:
        record = await self.get(device_id, plugin_id)
        if record is None:
            return False
        record.is_installed = False
        record.enabled_on_device = False
        record.installed_at = None
        await self.session.flush()
        return True

    async def set_device_enabled(
        self, device_id: str, plugin_id: str, enabled: bool,
    ) -> DevicePluginModel | None:
        record = await self.get(device_id, plugin_id)
        if record is None:
            return None
        record.enabled_on_device = enabled
        await self.session.flush()
        return record

    async def record_error(
        self, device_id: str, plugin_id: str, error: str,
    ) -> None:
        record = await self.get(device_id, plugin_id)
        if record is None:
            record = DevicePluginModel(
                device_id=device_id,
                plugin_id=plugin_id,
                version="0.0.0",
                is_installed=False,
                enabled_on_device=False,
                last_error=error[:512],
            )
            self.session.add(record)
        else:
            record.last_error = error[:512]
        await self.session.flush()

    async def list_for_device(self, device_id: str) -> list[DevicePluginModel]:
        result = await self.session.execute(
            select(DevicePluginModel)
            .where(DevicePluginModel.device_id == device_id)
            .order_by(DevicePluginModel.plugin_id)
        )
        return list(result.scalars().all())

    async def delete(self, device_id: str, plugin_id: str) -> bool:
        result = await self.session.execute(
            delete(DevicePluginModel).where(
                DevicePluginModel.device_id == device_id,
                DevicePluginModel.plugin_id == plugin_id,
            )
        )
        return result.rowcount > 0

    async def reconcile(
        self,
        device_id: str,
        device_plugins: list[dict],
    ) -> dict:
        """
        Reconcile the backend's view against the device's report.

        The device is authoritative. Rules:
        - A plugin in the DB but not in the report → mark uninstalled.
        - A plugin in the report but not the DB → insert as installed.
        - A plugin in both → update version/enabled/sha to match the report.

        device_plugins: [{"id": str, "version": str, "enabled": bool, "sha256": str|None}]
        """
        report = {p["id"]: p for p in device_plugins}
        existing = await self.list_for_device(device_id)
        existing_map = {r.plugin_id: r for r in existing}

        added: list[str] = []
        updated: list[str] = []
        removed: list[str] = []
        now = _utc_now()

        # 1. Remove entries the device no longer has.
        for plugin_id, record in existing_map.items():
            if plugin_id not in report:
                if record.is_installed or record.enabled_on_device:
                    record.is_installed = False
                    record.enabled_on_device = False
                    record.installed_at = None
                    removed.append(plugin_id)

        # 2. Add or update entries from the report.
        for plugin_id, info in report.items():
            version = str(info.get("version", "0.0.0"))
            enabled = bool(info.get("enabled", False))
            sha = info.get("sha256")

            record = existing_map.get(plugin_id)
            if record is None:
                record = DevicePluginModel(
                    device_id=device_id,
                    plugin_id=plugin_id,
                    version=version,
                    is_installed=True,
                    enabled_on_device=enabled,
                    artifact_sha256=sha,
                    installed_at=now,
                    last_error=None,
                )
                self.session.add(record)
                added.append(plugin_id)
                continue

            changed = (
                record.version != version
                or record.enabled_on_device != enabled
                or not record.is_installed
                or record.artifact_sha256 != sha
            )
            record.version = version
            record.is_installed = True
            record.enabled_on_device = enabled
            record.artifact_sha256 = sha
            if record.installed_at is None:
                record.installed_at = now
            record.last_error = None
            if changed:
                updated.append(plugin_id)

        await self.session.flush()

        return {
            "added": added,
            "updated": updated,
            "removed": removed,
            "reported": len(device_plugins),
        }