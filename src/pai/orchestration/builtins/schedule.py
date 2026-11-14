"""Built-in capabilities for schedule management."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

from pai.orchestration.builtin_capabilities import BuiltinCapability
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository


def _parse_when(when: str, tz_name: str) -> datetime:
    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc)


def build_schedule_capabilities(
    session_factory,
    scheduler_service,
) -> list[BuiltinCapability]:
    async def _create(params: Dict[str, Any], *, user_id: str, **_):
        tz_name = params.get("timezone", "UTC")

        row = ScheduleModel(
            user_id=user_id,
            target_device_id=params.get("target_device_id"),
            title=params["title"],
            body=params.get("body"),
            kind=params["repeat"],
            timezone=tz_name,
            capability=params.get(
                "capability", "system.notifications.show",
            ),
            parameters={
                "title": params["title"],
                "body": params.get("body", ""),
            },
            catch_up=params.get("catch_up", "once"),
            enabled=True,
        )

        if params["repeat"] == "one_shot":
            if not params.get("when"):
                raise ValueError("schedule.create: 'when' is required for one_shot")
            row.fire_at = _parse_when(params["when"], tz_name)
        elif params["repeat"] == "interval":
            if not params.get("interval_seconds"):
                raise ValueError(
                    "schedule.create: 'interval_seconds' required for interval"
                )
            row.interval_seconds = int(params["interval_seconds"])
        elif params["repeat"] == "cron":
            if not params.get("cron"):
                raise ValueError("schedule.create: 'cron' required for cron")
            row.cron = params["cron"]

        row.next_fire_at = scheduler_service.compute_initial_next(row)

        async with session_factory() as session:
            repo = ScheduleRepository(session)
            await repo.create(row)
            await session.commit()

        return {
            "id": row.id,
            "title": row.title,
            "kind": row.kind,
            "next_fire_at": row.next_fire_at.isoformat() if row.next_fire_at else None,
        }

    async def _list(params: Dict[str, Any], *, user_id: str, **_):
        async with session_factory() as session:
            repo = ScheduleRepository(session)
            rows = await repo.list_for_user(user_id)
        return {
            "schedules": [
                {
                    "id": r.id,
                    "title": r.title,
                    "kind": r.kind,
                    "enabled": r.enabled,
                    "next_fire_at":
                        r.next_fire_at.isoformat() if r.next_fire_at else None,
                    "cron": r.cron,
                }
                for r in rows
            ]
        }

    async def _delete(params: Dict[str, Any], *, user_id: str, **_):
        sid = params["schedule_id"]
        async with session_factory() as session:
            repo = ScheduleRepository(session)
            row = await repo.get(sid)
            if row is None or row.user_id != user_id:
                raise ValueError(f"Schedule {sid} not found")
            await repo.delete(sid)
            await session.commit()
        return {"deleted": sid}

    return [
        BuiltinCapability(
            id="schedule.create",
            description=(
                "Create a reminder or scheduled action. Use for "
                "'remind me at X', 'schedule Y for tomorrow', "
                "'every Monday remind me to Z'. Set 'repeat' to "
                "one_shot, interval, or cron. For one_shot, 'when' must "
                "be ISO 8601 (e.g. '2026-11-15T12:00:00Z'); compute it "
                "from the CURRENT UTC TIME given in context. For cron, "
                "set 'cron' (min hour dom month dow) + 'timezone'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "repeat": {
                        "type": "string",
                        "enum": ["one_shot", "interval", "cron"],
                    },
                    "when": {"type": "string"},
                    "cron": {"type": "string"},
                    "interval_seconds": {"type": "integer"},
                    "timezone": {"type": "string", "default": "UTC"},
                    "target_device_id": {"type": "string"},
                },
                "required": ["title", "repeat"],
            },
            handler=_create,
        ),
        BuiltinCapability(
            id="schedule.list",
            description="List the user's existing reminders/schedules.",
            parameters={"type": "object", "properties": {}},
            handler=_list,
        ),
        BuiltinCapability(
            id="schedule.delete",
            description="Delete a reminder/schedule by id.",
            parameters={
                "type": "object",
                "properties": {"schedule_id": {"type": "string"}},
                "required": ["schedule_id"],
            },
            handler=_delete,
        ),
    ]