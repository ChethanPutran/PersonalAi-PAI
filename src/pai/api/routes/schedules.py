"""Schedule routes: reminders AND scheduled plugin actions.

Safety model (see pai/scheduler): everything is validated when it is created or edited;
anything that would need approval runs unattended only under a valid STANDING CONSENT that is
granted here — through the authenticated REST endpoint — and nowhere else.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db
from pai.app_context import PAIAppContext, get_app_context
from pai.scheduler import consent as consent_mod
from pai.scheduler.validation import (
    FieldError, SchedulableCapability, ScheduleSpec, ScheduleValidationError, ScheduleValidator,
)
from pai.security.audit.models import AuditEventType
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository
from pai.tasks.models import (
    ConsentRequest, ScheduleCreate, ScheduleOut, ScheduleUpdate, Task,
)


def _task_to_dict(t: Task) -> dict:
    if hasattr(t, "to_dict"):
        return t.to_dict()
    return {k: v for k, v in getattr(t, "__dict__", {}).items()
            if not k.startswith("_")}


router = APIRouter(prefix="/schedules", tags=["schedules"])


class ValidationDetail(BaseModel):
    errors: List[FieldError]


class CapabilitiesOut(BaseModel):
    capabilities: List[SchedulableCapability]


# ------------------------------------------------------------------ helpers

def _validator(context: PAIAppContext) -> ScheduleValidator:
    v = getattr(context.orchestrator.scheduler_service, "validator", None)
    if v is None:
        raise HTTPException(503, "Scheduled actions are not available")
    return v


def _unprocessable(errors: List[FieldError]) -> HTTPException:
    """422 in the documented shape: {"detail": {"errors": [{"field", "message"}]}}."""
    return HTTPException(422, detail=ValidationDetail(errors=errors).model_dump())


async def request_validation_handler(request: Request, exc: RequestValidationError):
    """Malformed request bodies on /schedules use the SAME 422 shape as our own checks."""
    if "/schedules" not in request.url.path:
        return await request_validation_exception_handler(request, exc)
    errors = [
        FieldError(field=".".join(str(p) for p in e["loc"][1:]) or "body",
                   message=str(e["msg"]).removeprefix("Value error, "))
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": ValidationDetail(errors=errors).model_dump()})


def _audit(context: PAIAppContext, event: AuditEventType, row: ScheduleModel, user_id: str, reason: str) -> None:
    audit = getattr(context.orchestrator.scheduler_service, "audit_logger", None)
    if audit is None:
        return
    try:
        audit.record(event, user_id=user_id, capability=row.capability, allowed=True, reason=reason,
                     device_id=row.target_device_id, metadata={"schedule_id": row.id})
    except Exception:
        logger.exception("audit write failed for schedule {}", row.id)


async def _owned(repo: ScheduleRepository, schedule_id: str, user_id: str) -> ScheduleModel:
    row = await repo.get(schedule_id)
    if row is None:
        raise HTTPException(404, "Schedule not found")
    if row.user_id != user_id:
        raise HTTPException(403, "Not your schedule")
    return row


def _apply_validated(row: ScheduleModel, v) -> None:
    row.capability = v.capability
    row.target_device_id = v.target_device_id
    row.parameters = v.parameters
    row.requires_consent = v.requires_consent
    row.risk = v.risk
    row.executor = v.executor
    if v.fire_at is not None:
        row.fire_at = v.fire_at


# ------------------------------------------------------------------ routes

@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    repo = ScheduleRepository(session)
    rows = await repo.list_for_user(user_id)
    return [ScheduleOut.from_row(r) for r in rows]


@router.get("/capabilities", response_model=CapabilitiesOut)
async def schedulable_capabilities(
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
) -> CapabilitiesOut:
    """What the user can schedule: every capability on their devices/plugins, with its
    parameter schema, risk, whether it needs standing consent, and why it is blocked if so."""
    return CapabilitiesOut(capabilities=await _validator(context).catalog(user_id))


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    payload: ScheduleCreate,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    try:
        validated = await _validator(context).validate(user_id, ScheduleSpec(
            title=payload.title, kind=payload.kind, fire_at=payload.fire_at, cron=payload.cron,
            interval_seconds=payload.interval_seconds, timezone=payload.timezone,
            capability=payload.capability, parameters=payload.parameters,
            target_device_id=payload.target_device_id))
    except ScheduleValidationError as exc:
        raise _unprocessable(exc.errors)

    row = ScheduleModel(
        user_id=user_id, title=payload.title, body=payload.body, kind=payload.kind,
        cron=payload.cron, interval_seconds=payload.interval_seconds, timezone=payload.timezone,
        catch_up=payload.catch_up, enabled=True)
    _apply_validated(row, validated)

    scheduler = context.orchestrator.scheduler_service
    row.next_fire_at = scheduler.compute_initial_next(row)

    repo = ScheduleRepository(session)
    await repo.create(row)
    await session.commit()
    logger.debug("Created schedule {} ({})", row.id, row.capability)
    return ScheduleOut.from_row(row)


_TIMING_FIELDS = ("kind", "fire_at", "cron", "interval_seconds", "timezone")
_ACTION_FIELDS = ("capability", "parameters", "target_device_id")


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    repo = ScheduleRepository(session)
    row = await _owned(repo, schedule_id, user_id)
    sent = payload.model_dump(exclude_unset=True, exclude_none=True)

    touches_action = any(f in sent for f in _ACTION_FIELDS + _TIMING_FIELDS) or sent.get("enabled") is True
    if touches_action:
        spec = ScheduleSpec.from_row(row)
        merged = spec.model_copy(update={k: v for k, v in sent.items() if k in ScheduleSpec.model_fields})
        if "capability" in sent and sent["capability"] != row.capability and "target_device_id" not in sent:
            merged = merged.model_copy(update={"target_device_id": None})   # old device may not fit
        try:
            validated = await _validator(context).validate(user_id, merged)
        except ScheduleValidationError as exc:
            raise _unprocessable(exc.errors)
        _apply_validated(row, validated)

    for field in ("title", "body", "kind", "cron", "interval_seconds", "timezone", "catch_up"):
        if field in sent:
            setattr(row, field, sent[field])
    if "fire_at" in sent and not touches_action:
        row.fire_at = sent["fire_at"]
    if payload.enabled is not None:
        row.enabled = payload.enabled

    # Editing what the user approved (capability / parameters / device) cancels the approval.
    if consent_mod.revoke_if_binding_changed(row):
        _audit(context, AuditEventType.SCHEDULE_CONSENT_REVOKED, row, user_id, "schedule edited")

    if any(f in sent for f in _TIMING_FIELDS) or sent.get("enabled"):
        row.next_fire_at = context.orchestrator.scheduler_service.compute_initial_next(row)

    await session.commit()
    return ScheduleOut.from_row(row)


@router.post("/{schedule_id}/consent", response_model=ScheduleOut)
async def grant_consent(
    schedule_id: str,
    body: ConsentRequest = ConsentRequest(),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    """Approve this scheduled action to run unattended for ``days`` days.

    The approval is bound to the schedule's exact capability, parameters and device. This is the
    ONLY way consent is created: it needs the user's authenticated session, so neither the
    assistant nor anything spoken or typed in chat can grant it.
    """
    repo = ScheduleRepository(session)
    row = await _owned(repo, schedule_id, user_id)
    # The LIVE check decides (not the stored flag): an older schedule may need approval now.
    try:
        validated = await _validator(context).validate(user_id, ScheduleSpec.from_row(row))
    except ScheduleValidationError as exc:
        raise _unprocessable(exc.errors)
    if not validated.requires_consent:
        raise HTTPException(409, "This schedule does not need consent")
    _apply_validated(row, validated)                    # refresh requires_consent / risk / executor
    consent_mod.grant(row, by_user_id=user_id, days=body.days)
    await session.commit()
    _audit(context, AuditEventType.SCHEDULE_CONSENT_GRANTED, row, user_id,
           f"approved for {body.days} days")
    return ScheduleOut.from_row(row)


@router.delete("/{schedule_id}/consent", response_model=ScheduleOut)
async def revoke_consent(
    schedule_id: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    context: PAIAppContext = Depends(get_app_context),
):
    repo = ScheduleRepository(session)
    row = await _owned(repo, schedule_id, user_id)
    consent_mod.revoke(row)
    await session.commit()
    _audit(context, AuditEventType.SCHEDULE_CONSENT_REVOKED, row, user_id, "revoked by user")
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