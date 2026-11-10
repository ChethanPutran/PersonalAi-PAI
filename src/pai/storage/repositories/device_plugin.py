"""Device plugin repository — per-device plugin install state."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.observability.tracer import get_tracer
from pai.storage.models import DevicePluginModel
from pai.storage.repositories.base import BaseRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DevicePluginRepository(BaseRepository[DevicePluginModel]):
    """Tracks which plugins are installed on which device."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(
        self, device_id: str, plugin_id: str,
    ) -> DevicePluginModel | None:
        """Single-row lookup. Not traced (fast path)."""
        result = await self.session.execute(
            select(DevicePluginModel).where(
                DevicePluginModel.device_id == device_id,
                DevicePluginModel.plugin_id == plugin_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_installed(self, device_id: str, plugin_id: str) -> bool:
        """Boolean wrapper around `get`. Not traced."""
        record = await self.get(device_id, plugin_id)
        return record is not None and record.is_installed

    async def list_for_device(self, device_id: str) -> list[DevicePluginModel]:
        """All plugin records for a device.

        Traced — the CapabilityRouter calls this once per device
        per index rebuild. On a user with N devices this fires N
        times per chat request until the router cache warms.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.list_for_device",
            device_id=device_id,
        ) as span:
            result = await self.session.execute(
                select(DevicePluginModel)
                .where(DevicePluginModel.device_id == device_id)
                .order_by(DevicePluginModel.plugin_id)
            )
            rows = list(result.scalars().all())
            span.set_attribute("count", len(rows))
            span.set_attribute(
                "plugin_ids", [r.plugin_id for r in rows],
            )
            span.set_attribute(
                "installed_and_enabled",
                [
                    r.plugin_id for r in rows
                    if r.is_installed and r.enabled_on_device
                ],
            )
            return rows

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def mark_installed(
        self,
        device_id: str,
        plugin_id: str,
        version: str,
        artifact_sha256: Optional[str] = None,
    ) -> DevicePluginModel:
        """Record a plugin as installed on a device.

        Called after the mobile app reports a successful download +
        checksum verification, so the DB state should match what's
        actually on the device's disk.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.mark_installed",
            device_id=device_id,
            plugin_id=plugin_id,
            version=version,
        ) as span:
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
                span.set_attribute("was_insert", True)
            else:
                record.version = version
                record.is_installed = True
                record.artifact_sha256 = artifact_sha256
                record.installed_at = now
                record.last_error = None
                span.set_attribute("was_insert", False)

            await self.session.flush()
            return record

    async def mark_uninstalled(
        self, device_id: str, plugin_id: str,
    ) -> bool:
        """Record a plugin as uninstalled on a device.

        Also clears `enabled_on_device` because you can't have an
        enabled-but-uninstalled plugin.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.mark_uninstalled",
            device_id=device_id,
            plugin_id=plugin_id,
        ) as span:
            record = await self.get(device_id, plugin_id)
            if record is None:
                span.set_attribute("found", False)
                return False
            record.is_installed = False
            record.enabled_on_device = False
            record.installed_at = None
            await self.session.flush()
            span.set_attribute("found", True)
            return True

    async def set_device_enabled(
        self, device_id: str, plugin_id: str, enabled: bool,
    ) -> DevicePluginModel | None:
        """Flip the per-device enabled flag.

        Distinct from user-level enablement: the user authorizes the
        plugin (`user_plugins` table), and this table records whether
        the .so is actually loaded on this specific device right now.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.set_device_enabled",
            device_id=device_id,
            plugin_id=plugin_id,
            enabled=enabled,
        ) as span:
            record = await self.get(device_id, plugin_id)
            if record is None:
                span.set_attribute("found", False)
                return None
            record.enabled_on_device = enabled
            await self.session.flush()
            span.set_attribute("found", True)
            return record

    async def record_error(
        self, device_id: str, plugin_id: str, error: str,
    ) -> None:
        """Attach an error message to a plugin row (creating it if absent).

        Used by the plugin installation flow when a device reports a
        failed download/extract. The error is truncated to 512 chars
        so a giant stack trace doesn't blow up the row size.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.record_error",
            device_id=device_id,
            plugin_id=plugin_id,
            error_length=len(error),
        ) as span:
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
                span.set_attribute("was_insert", True)
            else:
                record.last_error = error[:512]
                span.set_attribute("was_insert", False)

            await self.session.flush()

    async def delete(self, device_id: str, plugin_id: str) -> bool:
        """Delete a device-plugin row. Returns True if one existed."""
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.delete",
            device_id=device_id,
            plugin_id=plugin_id,
        ) as span:
            result = await self.session.execute(
                delete(DevicePluginModel).where(
                    DevicePluginModel.device_id == device_id,
                    DevicePluginModel.plugin_id == plugin_id,
                )
            )
            deleted = result.rowcount > 0
            span.set_attribute("deleted", deleted)
            return deleted

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

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

        Span attributes expose the diff so you can tell at a glance
        whether a reconcile was a no-op or a bulk change.
        """
        tracer = get_tracer()
        async with tracer.span(
            "device_plugin.reconcile",
            device_id=device_id,
            reported_count=len(device_plugins),
        ) as span:
            # Build lookup maps once — O(n) instead of O(n²) if we
            # called get() per plugin inside the loop.
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
                    # New plugin the device reports that we didn't know about
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

                # Only count as "updated" if something actually changed
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

            span.set_attribute("added", added)
            span.set_attribute("updated", updated)
            span.set_attribute("removed", removed)
            span.set_attribute(
                "unchanged",
                len(report) - len(added) - len(updated),
            )

            return {
                "added": added,
                "updated": updated,
                "removed": removed,
                "reported": len(device_plugins),
            }