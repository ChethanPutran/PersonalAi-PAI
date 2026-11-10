"""User plugin repository — per-user plugin enablement and config."""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.observability.tracer import get_tracer
from pai.storage.models import UserPluginModel
from pai.storage.repositories.base import BaseRepository


class UserPluginRepository(BaseRepository[UserPluginModel]):
    """Persists per-user plugin state (is_enabled + config metadata)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(
        self, user_id: str, plugin_id: str,
    ) -> UserPluginModel | None:
        """Single-row lookup by (user_id, plugin_id).

        Not traced — this is the fast path called from every
        enable/disable/config operation, and those already have
        their own span.
        """
        result = await self.session.execute(
            select(UserPluginModel).where(
                UserPluginModel.user_id == user_id,
                UserPluginModel.plugin_id == plugin_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_enabled(self, user_id: str, plugin_id: str) -> bool:
        """Boolean wrapper around `get`. Not traced for the same reason."""
        record = await self.get(user_id, plugin_id)
        return record is not None and record.is_enabled

    async def list_for_user(self, user_id: str) -> list[UserPluginModel]:
        """All plugin records for a user (enabled and disabled).

        Traced because the result count drives downstream work — the
        CapabilityRouter calls this on every cache rebuild, so a slow
        query here shows up as slow capability resolution.
        """
        tracer = get_tracer()
        async with tracer.span(
            "user_plugin.list_for_user",
            user_id=user_id,
        ) as span:
            result = await self.session.execute(
                select(UserPluginModel)
                .where(UserPluginModel.user_id == user_id)
                .order_by(UserPluginModel.plugin_id)
            )
            rows = list(result.scalars().all())
            span.set_attribute("count", len(rows))
            return rows

    async def list_enabled(self, user_id: str) -> list[UserPluginModel]:
        """Only the enabled plugins for a user.

        Traced — this is the exact query the CapabilityRouter uses
        when building its per-user index.
        """
        tracer = get_tracer()
        async with tracer.span(
            "user_plugin.list_enabled",
            user_id=user_id,
        ) as span:
            result = await self.session.execute(
                select(UserPluginModel).where(
                    UserPluginModel.user_id == user_id,
                    UserPluginModel.is_enabled.is_(True),
                )
            )
            rows = list(result.scalars().all())
            span.set_attribute("count", len(rows))
            span.set_attribute(
                "plugin_ids", [r.plugin_id for r in rows],
            )
            return rows

    async def get_config(
        self, user_id: str, plugin_id: str,
    ) -> dict[str, Any]:
        """Return the stored config dict for a plugin (or {} if unset)."""
        # Not traced — one indexed lookup + dict copy.
        record = await self.get(user_id, plugin_id)
        return dict(record.metadata_json or {}) if record else {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def set_enabled(
        self, user_id: str, plugin_id: str, enabled: bool,
    ) -> UserPluginModel:
        """Enable or disable a plugin for a user.

        Traced because this is the write path triggered by the mobile
        app's toggle switch. Slow toggles = slow UI.
        """
        tracer = get_tracer()
        async with tracer.span(
            "user_plugin.set_enabled",
            user_id=user_id,
            plugin_id=plugin_id,
            enabled=enabled,
        ) as span:
            record = await self.get(user_id, plugin_id)

            if record is None:
                # First enable for this user/plugin combo → insert
                record = UserPluginModel(
                    user_id=user_id,
                    plugin_id=plugin_id,
                    is_enabled=enabled,
                )
                self.session.add(record)
                span.set_attribute("was_insert", True)
            else:
                record.is_enabled = enabled
                span.set_attribute("was_insert", False)

            await self.session.flush()
            return record

    async def set_config(
        self, user_id: str, plugin_id: str, config: dict[str, Any],
    ) -> UserPluginModel:
        """Persist plugin config for a user (creates the row if absent).

        Traced with a config_keys attribute so you can see which
        capabilities are being configured without dumping the payload.
        """
        tracer = get_tracer()
        async with tracer.span(
            "user_plugin.set_config",
            user_id=user_id,
            plugin_id=plugin_id,
            config_keys=list(config.keys()),
        ) as span:
            record = await self.get(user_id, plugin_id)

            if record is None:
                record = UserPluginModel(
                    user_id=user_id,
                    plugin_id=plugin_id,
                    is_enabled=False,
                )
                self.session.add(record)
                span.set_attribute("was_insert", True)
            else:
                span.set_attribute("was_insert", False)

            record.metadata_json = dict(config)
            await self.session.flush()
            return record

    async def delete(self, user_id: str, plugin_id: str) -> bool:
        """Delete the record. Returns True if a row existed."""
        tracer = get_tracer()
        async with tracer.span(
            "user_plugin.delete",
            user_id=user_id,
            plugin_id=plugin_id,
        ) as span:
            result = await self.session.execute(
                delete(UserPluginModel).where(
                    UserPluginModel.user_id == user_id,
                    UserPluginModel.plugin_id == plugin_id,
                )
            )
            deleted = result.rowcount > 0
            span.set_attribute("deleted", deleted)
            return deleted