"""Schedule CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db
from pai.app_context import PAIAppContext, get_app_context
from pai.tasks.models import (
    ScheduleCreate, ScheduleOut, ScheduleUpdate,
)
from pai.storage.repositories.schedule import ScheduleRepository
from pai.storage.models import ScheduleModel
from pai.tasks.models import Task
from loguru import logger

def _task_to_dict(t: Task) -> dict:
    if hasattr(t, "to_dict"):
        return t.to_dict()
    return {k: v for k, v in getattr(t, "__dict__", {}).items()
            if not k.startswith("_")}

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    repo = ScheduleRepository(session)
    rows = await repo.list_for_user(user_id)
    return [ScheduleOut.from_row(r) for r in rows]



@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    payload: ScheduleCreate,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    # Build the row
    row = ScheduleModel(
        user_id=user_id,
        target_device_id=payload.target_device_id,
        title=payload.title,
        body=payload.body,
        kind=payload.kind,
        fire_at=payload.fire_at,
        cron=payload.cron,
        interval_seconds=payload.interval_seconds,
        timezone=payload.timezone,
        capability=payload.capability,
        parameters=payload.parameters,
        catch_up=payload.catch_up,
        enabled=True,
    )

    logger.debug(f"Creating schedule: {row}")

    # Compute the first firing.
    scheduler = context.orchestrator.scheduler_service
    row.next_fire_at = scheduler.compute_initial_next(row)
    logger.debug(f"Computed next fire at: {row.next_fire_at}")

    repo = ScheduleRepository(session)
    await repo.create(row)
    await session.commit()

    response = ScheduleOut.from_row(row)
    logger.debug(f"Created schedule: {response}")

    return response


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    repo = ScheduleRepository(session)
    row = await repo.get(schedule_id)
    if row is None:
        raise HTTPException(404, "Schedule not found")
    if row.user_id != user_id:
        raise HTTPException(403, "Not your schedule")

    # Apply fields
    for field in (
        "title", "body", "kind", "fire_at", "cron",
        "interval_seconds", "timezone", "capability",
        "parameters", "catch_up", "target_device_id",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(row, field, val)

    # Toggle
    if payload.enabled is not None:
        row.enabled = payload.enabled

    # Recompute next fire if schedule-shape changed OR we re-enabled.
    scheduler = getattr(context.orchestrator, "scheduler", None)
    if scheduler is not None and (
        payload.kind or payload.fire_at or payload.cron
        or payload.interval_seconds or payload.enabled
    ):
        row.next_fire_at = scheduler.compute_initial_next(row)

    await session.commit()
    return ScheduleOut.from_row(row)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    repo = ScheduleRepository(session)
    row = await repo.get(schedule_id)
    if row is None:
        raise HTTPException(404, "Schedule not found")
    if row.user_id != user_id:
        raise HTTPException(403, "Not your schedule")
    await repo.delete(schedule_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/{schedule_id}/history")
async def schedule_history(
    schedule_id: str,
    limit: int = 50,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    repo = ScheduleRepository(session)
    row = await repo.get(schedule_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "Schedule not found")

    tm = context.orchestrator.task_manager
    # Fetch user's tasks and filter by metadata — swap for a SQL filter
    # once the tasks route supports it.
    all_tasks = await tm.list(user_id=user_id, limit=500)
    matching = [
        t for t in all_tasks
        if (getattr(t, "metadata", None) or {}).get("schedule_id") == schedule_id
    ]
    matching.sort(
        key=lambda t: getattr(t, "created_at", None) or "",
        reverse=True,
    )
    matching = matching[:max(1, min(limit, 200))]

    return {
        "schedule_id": schedule_id,
        "tasks": [_task_to_dict(t) for t in matching],
        "total": len(matching),
    }