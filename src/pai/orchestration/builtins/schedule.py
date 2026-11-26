"""Built-in capabilities for schedule management."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pai.utils.structured import parse_structured
from typing import Any, Dict
from zoneinfo import ZoneInfo

from pai.orchestration.builtin_capabilities import BuiltinCapability
from pai.scheduler.validation import (
    REMINDER_CAPABILITY, ScheduleSpec, ScheduleValidationError, ScheduleValidator,
)
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository


# How far in the past an absolute `when` may be and still be honoured (slow planning, not a stale request).
PLAN_LATENCY_GRACE_SECONDS = 120


def _parse_when(when: str, tz_name: str) -> datetime:
    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc)


def build_schedule_capabilities(
    session_factory,
    scheduler_service,
    validator: "ScheduleValidator | None" = None,
) -> list[BuiltinCapability]:
    async def _create(params: Dict[str, Any], *, user_id: str, **_):
        """Create a reminder or a scheduled action (chat / voice path).

        Same validation as the REST route. This path can NEVER grant consent: whatever the
        user typed or said (or the model decided), an action that needs approval is created
        waiting for it, and the user approves it in the Reminders screen.
        """
        tz_name = params.get("timezone", "UTC")
        capability = params.get("capability") or REMINDER_CAPABILITY
        plugin_params = parse_structured(params.get("parameters") or {})   # tolerate "{'a': 1}" text
        if not isinstance(plugin_params, dict):
            raise ValueError("schedule.create: 'parameters' must be an object")
        kind = params["repeat"]
        fire_at = None
        if kind == "one_shot":
            now = datetime.now(timezone.utc)
            after = params.get("after_seconds")
            if after is not None:
                # Relative delay ("in 5 seconds"): resolved HERE, at execution time. The model
                # must not do clock arithmetic — planning can take 30 s, so a time it computed
                # from the prompt's clock is already in the past when this runs.
                try:
                    delay = float(after)
                except (TypeError, ValueError):
                    raise ValueError("schedule.create: 'after_seconds' must be a number")
                if not 0 < delay <= 366 * 86400:
                    raise ValueError("schedule.create: 'after_seconds' must be between 1 second and a year")
                fire_at = now + timedelta(seconds=delay)
            elif params.get("when"):
                fire_at = _parse_when(params["when"], tz_name)
                lag = (now - fire_at).total_seconds()
                if 0 < lag <= PLAN_LATENCY_GRACE_SECONDS:
                    # An absolute time that went stale only because planning was slow: run it
                    # now rather than refusing a request the user made seconds ago.
                    fire_at = now + timedelta(seconds=2)
            else:
                raise ValueError("schedule.create: 'when' or 'after_seconds' is required for one_shot")

        row = ScheduleModel(
            user_id=user_id,
            target_device_id=params.get("target_device_id"),
            title=params["title"],
            body=params.get("body"),
            kind=kind,
            timezone=tz_name,
            capability=capability,
            # A reminder keeps its text in title/body (the notification capability reads it).
            # Any other capability gets ONLY the parameters the user asked for.
            parameters=({"title": params["title"], "body": params.get("body", "")}
                        if capability == REMINDER_CAPABILITY
                        else dict(plugin_params)),
            catch_up=params.get("catch_up", "once"),
            misfire_grace_seconds=(int(params["misfire_grace_seconds"])
                                   if params.get("misfire_grace_seconds") is not None else None),
            enabled=True,
            fire_at=fire_at,
            interval_seconds=int(params["interval_seconds"]) if kind == "interval" and params.get("interval_seconds") else None,
            cron=params.get("cron") if kind == "cron" else None,
        )
        if kind == "interval" and not row.interval_seconds:
            raise ValueError("schedule.create: 'interval_seconds' required for interval")
        if kind == "cron" and not row.cron:
            raise ValueError("schedule.create: 'cron' required for cron")

        if validator is not None:
            try:
                v = await validator.validate(user_id, ScheduleSpec(
                    title=row.title, kind=kind, fire_at=fire_at, cron=row.cron,
                    interval_seconds=row.interval_seconds, timezone=tz_name,
                    capability=capability, parameters=row.parameters,
                    target_device_id=row.target_device_id))
            except ScheduleValidationError as exc:
                raise ValueError("schedule.create: " + "; ".join(
                    f"{e.field}: {e.message}" for e in exc.errors))
            row.capability, row.target_device_id, row.parameters = v.capability, v.target_device_id, v.parameters
            row.requires_consent, row.risk, row.executor = v.requires_consent, v.risk, v.executor
            if v.fire_at is not None:
                row.fire_at = v.fire_at
        elif capability != REMINDER_CAPABILITY:
            raise ValueError("schedule.create: scheduled actions are not available")

        row.next_fire_at = scheduler_service.compute_initial_next(row)

        async with session_factory() as session:
            repo = ScheduleRepository(session)
            await repo.create(row)
            await session.commit()

        result = {
            "id": row.id,
            "title": row.title,
            "kind": row.kind,
            "capability": row.capability,
            "next_fire_at": row.next_fire_at.isoformat() if row.next_fire_at else None,
            "requires_consent": bool(row.requires_consent),
        }
        if row.requires_consent:
            result["message"] = (
                "Created, but it will not run until you approve it: open the Reminders screen "
                "and approve this scheduled action. Approval cannot be given by voice or chat.")
        return result

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
                "one_shot, interval, or cron. For one_shot: if the user gives a DELAY "
                "('in 5 seconds', 'after 10 minutes') set 'after_seconds' to the number of seconds "
                "and do NOT compute a time yourself (the server resolves it when it runs); only "
                "for a clock time ('at 12pm', 'tomorrow 9am') set 'when' (ISO 8601 with a UTC "
                "offset, e.g. '2026-11-15T12:00:00Z'). For cron, "
                "set 'cron' (min hour dom month dow) + 'timezone'. By default it "
                "shows a reminder notification. To schedule an ACTION instead (e.g. take a "
                "photo every morning) set 'capability' to the capability id and 'parameters' "
                "to its parameters; actions that need approval are created waiting for the "
                "user to approve them in the Reminders screen (you cannot approve them)."
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
                    "when": {"type": "string", "description": "Absolute clock time, ISO 8601 with UTC offset. Not for delays."},
                    "after_seconds": {"type": "number", "description": "Delay from now in seconds (for 'in/after N ...')."},
                    "cron": {"type": "string"},
                    "interval_seconds": {"type": "integer"},
                    "timezone": {"type": "string", "default": "UTC"},
                    "target_device_id": {"type": "string"},
                    "capability": {"type": "string"},
                    "parameters": {"type": "object"},
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