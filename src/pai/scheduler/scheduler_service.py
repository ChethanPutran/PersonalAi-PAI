"""Scheduler loop — fires schedules into TaskManager."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from pai.devices.manager import DeviceManager
from pai.executors.manager import ExecutorManager
from pai.notifications.push_service import PushService, PushTokenRepository
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository
from pai.common.enums.permission import Origin
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.models import TaskSpec
from pai.scheduler import consent as consent_mod
from pai.scheduler.validation import (
    REMINDER_CAPABILITY, ScheduleValidationError, ScheduleValidator,
)
from pai.security.audit.models import AuditEventType
from pai.tasks.manager import TaskManager
from pai.tasks.models import LastRunStatus, TaskSource, TaskType


logger = logging.getLogger(__name__)

# How long a background scheduled run may take (beyond the task's own timeout) before it is
# abandoned — e.g. a task that fell back to waiting for a human confirmation nobody gives.
_RUN_GRACE_SECONDS = 120.0
# catch_up="all": most missed runs replayed per tick (the rest stay due and go on the next tick), and
# most occurrences enumerated while counting the stale ones (bounds the work after a long outage).
MAX_REPLAY_PER_TICK = 50
_MAX_SCAN = 10_000


def _utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; we always wrote UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
_AUDIT_FOR_STATUS = {
    "ok": AuditEventType.SCHEDULE_RUN,
    "skipped_no_consent": AuditEventType.SCHEDULE_SKIPPED,
    "device_offline": AuditEventType.SCHEDULE_SKIPPED,
    "missed": AuditEventType.SCHEDULE_SKIPPED,
    "denied": AuditEventType.SCHEDULE_DENIED,
    "failed": AuditEventType.SCHEDULE_FAILED,
}


class FireJob(BaseModel):
    """Plain snapshot of a due schedule, safe to hand to a background task (no ORM row)."""
    schedule_id: str
    user_id: str
    title: str
    kind: str
    capability: str
    parameters: dict[str, Any]
    device_id: str
    plugin_id: str
    executor_id: str
    approved: bool
    request_id: str


class SchedulerService:
    """Async loop that turns due ScheduleModel rows into Tasks."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        task_manager:TaskManager,
        executor: ExecutorManager,
        device_manager: DeviceManager,
        tick_seconds: int = 15,
        device_executor_name: str = "device_executor",
        push_service: Optional[PushService] = None,
        validator: Optional[ScheduleValidator] = None,
        audit_logger: Optional[Any] = None,
    ) -> None:
        self.validator = validator
        self.audit_logger = audit_logger
        self._bg: set[asyncio.Task] = set()
        self.session_factory = session_factory
        self.task_manager = task_manager
        self.executor = executor
        self.device_manager = device_manager
        self.push_service = push_service
        self.tick_seconds = tick_seconds
        self.device_executor_name = device_executor_name
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_undeliverable: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="scheduler")
        logger.info("SchedulerService started (tick=%ss)", self.tick_seconds)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        logger.info("SchedulerService stopped")

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.tick_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            repo = ScheduleRepository(session)
            due = await repo.find_due(now)
            if not due:
                return

            for sched in due:
                try:
                    await self._fire(sched, repo, now)
                except Exception:
                    logger.exception(
                        "Failed to fire schedule %s", sched.id,
                    )
            await session.commit()

    # ------------------------------------------------------------------
    # Fire
    # ------------------------------------------------------------------

    async def _fire(
        self,
        sched: ScheduleModel,
        repo: ScheduleRepository,
        now: datetime,
    ) -> None:
        """Apply the misfire policy, then dispatch.

        ``next_fire_at`` is persisted, so after downtime every missed schedule is simply "due" on
        the first tick. How late it may still run is decided here:

        * ``misfire_grace_seconds`` (None = no limit): a run later than this is MISSED, not run.
        * ``catch_up="once"`` / ``"none"``: one run now (if within grace), then back on cadence.
        * ``catch_up="all"``: every occurrence that fell within the grace window is replayed
          oldest-first (at most ``MAX_REPLAY_PER_TICK`` per tick); older ones are recorded as missed.
        """
        grace = sched.misfire_grace_seconds
        late = (now - _utc(sched.next_fire_at)).total_seconds() if sched.next_fire_at else 0.0

        if sched.catch_up == "all" and sched.kind != "one_shot":
            if late > 0:
                await self._replay_missed(sched, repo, now)
                return
        elif grace is not None and late > grace:
            await self._skip_missed(sched, repo, now, f"{int(late)}s late (grace {grace}s)")
            return
        await self._dispatch(sched, repo, now)

    async def _dispatch(self, sched, repo, now, due_at: Optional[datetime] = None) -> None:
        if sched.capability == REMINDER_CAPABILITY:
            await self._fire_reminder(sched, repo, now, due_at)    # the classic reminder, as before
        else:
            await self._fire_action(sched, repo, now, due_at)      # a plugin/server/builtin action

    async def _skip_missed(self, sched, repo, now, detail: str) -> None:
        """Too late to run: say so (status, audit) and move on to the next occurrence after now."""
        logger.warning("Schedule %s missed its fire time: %s", sched.id, detail)
        await self._record(sched, repo, now, "missed", error=f"missed: {detail}"[:500])
        await self._advance(sched, repo, now)

    def _plan_replay(
        self, sched: ScheduleModel, now: datetime,
    ) -> tuple[int, bool, list[datetime]]:
        """Split the occurrences in [next_fire_at, now] into (stale_count, stale_truncated, fresh)."""
        first = _utc(sched.next_fire_at)
        grace = sched.misfire_grace_seconds
        cutoff = now - timedelta(seconds=grace) if grace is not None else None
        stale, truncated, fresh = 0, False, []
        cur: Optional[datetime] = first
        step = timedelta(seconds=sched.interval_seconds or 3600) if sched.kind == "interval" else None

        def advance(t: datetime) -> Optional[datetime]:
            return t + step if step else self._next_fire_at(sched, t)

        scanned = 0
        while cur is not None and cur <= now:
            if cutoff is not None and cur < cutoff:
                if scanned >= _MAX_SCAN:          # a very long outage: jump straight to the window
                    truncated = True
                    if step:
                        cur += step * math.ceil((cutoff - cur) / step)      # first run >= cutoff
                    else:
                        cur = self._next_fire_at(sched, cutoff - timedelta(microseconds=1))
                    scanned = 0
                    continue
                stale += 1
                scanned += 1
            else:
                fresh.append(cur)
                if len(fresh) >= MAX_REPLAY_PER_TICK:
                    break
            cur = advance(cur)
        return stale, truncated, fresh

    async def _replay_missed(self, sched, repo, now) -> None:
        stale, truncated, fresh = self._plan_replay(sched, now)
        if stale:
            n = f"{stale}+" if truncated else str(stale)
            logger.warning("Schedule %s: %s missed run(s) are older than the grace window", sched.id, n)
            await self._record(sched, repo, now, "missed",
                               error=f"missed: {n} run(s) older than the {sched.misfire_grace_seconds}s grace window")
        if not fresh:
            await self._advance(sched, repo, now)       # nothing worth running: jump past now
            return
        logger.info("Schedule %s: replaying %d missed run(s)", sched.id, len(fresh))
        for occ in fresh:
            marker = sched.next_fire_at = occ           # "first run not yet handled"
            await self._dispatch(sched, repo, now, due_at=occ)
            if sched.next_fire_at is marker:            # not advanced (e.g. device offline): retry next tick
                break

    async def _fire_reminder(
        self,
        sched: ScheduleModel,
        repo: ScheduleRepository,
        now: datetime,
        due_at: Optional[datetime] = None,
    ) -> None:
        target_device = await self._resolve_device(sched)
        if target_device is None:
            await self._handle_undeliverable(sched, repo, now, reason="no_device", due_at=due_at)
            return

        is_notification = sched.capability == "system.notifications.show"
        device_online = False
        try:
            device_online = await self.device_manager.is_connected(target_device)
        except Exception:
            device_online = False

        has_push = False
        if is_notification and self.push_service and self.push_service.enabled:
            async with self.session_factory() as ps:
                tokens = await PushTokenRepository(ps).get_for_device(target_device)
                has_push = bool(tokens)

        # Decide delivery channel before creating a Task so we never
        # leave orphaned CREATED tasks in history.
        if is_notification and has_push:
            delivery = "push"
        elif device_online:
            delivery = "ws"
        else:
            await self._handle_undeliverable(
                sched, repo, now, reason="device_offline_no_push", due_at=due_at,
            )
            return

        logger.info(
            "Firing schedule %s (%s) on %s via %s",
            sched.id, sched.title, target_device, delivery,
        )

        # 1. Create the task from a TaskSpec (the title is the task's name). submit() below
        #    moves it from CREATED to INITIALIZED, so nothing else is needed here.
        spec = TaskSpec(
            id=f"schedule_{sched.id}",
            name=sched.title,
            input=sched.body or sched.title,
            type=TaskType.ONE_SHOT,
            capability=sched.capability,
            parameters={
                # Merge any preset parameters, then stamp the reminder
                # text so system.notifications.show picks it up.
                **(sched.parameters or {}),
                "title": sched.title,
                "body": sched.body or "",
            },
            timeout=60.0,           # generous for slow devices
            retry_count=1,
        )
        try:
            task = await self.task_manager.create_task(
                spec,
                user_id=sched.user_id,
                session_id=f"schedule:{sched.id}",       # ScheduleModel has no session of its own
                executor_id=self.device_executor_name,
                device_id=target_device,
                source=TaskSource.SCHEDULED,
                metadata={
                    "source": "scheduler",
                    "schedule_id": sched.id,
                    "schedule_kind": sched.kind,
                },
                # One key per firing: a device that already showed this reminder
                # can de-duplicate a retry / recovery re-send.
                idempotency_key=f"schedule:{sched.id}:{(due_at or now).isoformat()}",
            )
        except Exception:
            logger.exception(
                "Schedule %s: task creation failed", sched.id,
            )
            await self._record(sched, repo, now, "failed", error="could not create the reminder task")
            await self._advance(sched, repo, now, due_at)
            return

        # 3. Queue + run it. wait=False so the scheduler loop doesn't block
        #    on slow devices. Failures flow back as task.failed events.
        if delivery == "push":
            await self._deliver_push(sched, task, target_device)
        else:
            try:
                await self.task_manager.submit(task.id, wait=False)
            except Exception as exc:
                logger.warning("Schedule %s: submit failed: %s", sched.id, exc)

        await self._record(sched, repo, now, "ok", task_id=task.id)
        await self._advance(sched, repo, now, due_at)

    # ------------------------------------------------------------------
    # Scheduled ACTIONS (plugin / server / built-in capabilities)
    # ------------------------------------------------------------------

    async def _fire_action(
        self, sched: ScheduleModel, repo: ScheduleRepository, now: datetime,
        due_at: Optional[datetime] = None,
    ) -> None:
        """Run a scheduled action — unattended, so every safety check is re-done NOW:

        1. the capability/device are re-resolved against the live index;
        2. an offline device is never "woken" with a queued action (catch_up policy applies);
        3. the real authorization pipeline is dry-run as a SCHEDULED run: blocked => denied;
           needs approval => runs only under a valid standing consent, else it is skipped and
           the user is told;
        4. the run goes through TaskManager.run (real task, real authorization + token) with
           ``Origin.SCHEDULED``; ``pre_approved`` only ever resolves APPROVAL_REQUIRED.
        """
        if self.validator is None:
            await self._record(sched, repo, now, "failed", error="scheduler is not set up for actions")
            await self._advance(sched, repo, now, due_at)
            return
        try:
            ft = await self.validator.resolve_for_fire(sched)
        except ScheduleValidationError as exc:
            await self._record(sched, repo, now, "failed", error=str(exc)[:500])
            await self._advance(sched, repo, now, due_at)
            return
        except Exception as exc:
            logger.exception("Schedule %s: could not resolve target", sched.id)
            await self._record(sched, repo, now, "failed", error=f"{type(exc).__name__}: {exc}"[:500])
            await self._advance(sched, repo, now, due_at)
            return

        if ft.requires_device and not await self._device_connected(ft.device_id):
            await self._handle_undeliverable(sched, repo, now, reason="device_offline", due_at=due_at)
            return

        pre = ft.preflight
        # Keep what the Reminders screen shows in step with what the pipeline says today
        # (older schedules were stored before these flags existed).
        sched.requires_consent, sched.risk, sched.executor = pre.requires_consent, pre.risk, ft.executor_id
        if pre.blocked:
            await self._record(sched, repo, now, "denied", error=pre.blocked_reason)
            await self._advance(sched, repo, now, due_at)
            return

        approved = False
        if pre.requires_consent:
            state = consent_mod.evaluate(sched, now)
            if not state.active:
                first_time = sched.last_run_status != "skipped_no_consent"
                await self._record(sched, repo, now, "skipped_no_consent",
                                   error=f"needs your approval ({state.reason.value})",
                                   plugin_id=ft.plugin_id, device_id=ft.device_id)
                if first_time:                              # tell them once, not on every tick
                    await self._notify_user(
                        sched, "Scheduled action needs your approval",
                        f"Scheduled action '{sched.title}' needs your approval. "
                        "Open Reminders to approve it.")
                await self._advance(sched, repo, now, due_at)
                return
            approved = True

        job = FireJob(
            schedule_id=sched.id, user_id=sched.user_id, title=sched.title, kind=sched.kind,
            capability=sched.capability, parameters=dict(sched.parameters or {}),
            device_id=ft.device_id, plugin_id=ft.plugin_id, executor_id=ft.executor_id,
            approved=approved, request_id=f"schedule:{sched.id}:{(due_at or now).isoformat()}")
        logger.info("Firing scheduled action %s (%s) on %s via %s approved=%s",
                    sched.id, sched.capability, ft.device_id, ft.executor_id, approved)
        # Background: a slow device must not stall every other due schedule.
        t = asyncio.create_task(self._execute_job(job), name=f"schedule-run:{sched.id}")
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        await self._advance(sched, repo, now, due_at)

    async def _execute_job(self, job: FireJob) -> None:
        status: LastRunStatus = "ok"
        error: Optional[str] = None
        task_id: Optional[str] = None
        spec = TaskSpec(
            id=f"schedule_{job.schedule_id}", name=job.title, input=job.title,
            type=TaskType.ONE_SHOT, capability=job.capability, parameters=job.parameters,
            preferred_device=job.device_id, timeout=60.0,
            metadata={"source": "scheduler", "schedule_id": job.schedule_id,
                      "schedule_kind": job.kind},
        )
        ctx = ExecutionContext(
            user_id=job.user_id, session_id=f"schedule:{job.schedule_id}",
            request_id=job.request_id, origin=Origin.SCHEDULED,
            pre_approved=job.approved,       # server-decided, from a valid standing consent
            metadata={"allowed_devices": [job.device_id]},   # the validated, user-owned target
        )
        try:
            result = await asyncio.wait_for(
                self.task_manager.run(spec, ctx, wait=True), timeout=spec.timeout + _RUN_GRACE_SECONDS)
            if result is not None and not result.success:
                status, error = "failed", (result.error or "the action failed")[:500]
        except asyncio.CancelledError:
            raise
        except PermissionError as exc:
            status, error = "denied", str(exc)[:500]
        except asyncio.TimeoutError:
            status, error = "failed", "timed out"
        except Exception as exc:
            logger.exception("Scheduled run %s failed", job.schedule_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        try:
            existing = await self.task_manager.find_by_idempotency_key(
                f"{job.request_id}:{spec.id}", user_id=ctx.user_id, session_id=ctx.session_id)
            task_id = existing.id if existing else None
        except Exception:
            task_id = None
        try:
            async with self.session_factory() as session:
                await ScheduleRepository(session).record_run(
                    job.schedule_id, status=status, at=datetime.now(timezone.utc),
                    error=error, task_id=task_id)
                await session.commit()
        except Exception:
            logger.exception("Could not record outcome of schedule %s", job.schedule_id)
        self._audit(job.schedule_id, job.user_id, job.capability, status, error=error,
                    task_id=task_id, plugin_id=job.plugin_id, device_id=job.device_id)

    async def _device_connected(self, device_id: str) -> bool:
        try:
            return bool(await self.device_manager.is_connected(device_id))
        except Exception:
            return False

    async def _notify_user(self, sched: ScheduleModel, title: str, body: str) -> None:
        """Tell the user something about a schedule: push first, else the device over WS
        (through the normal reminder-task path). Never raises."""
        try:
            device = await self._resolve_device(sched)
            if device is None:
                return
            if self.push_service and self.push_service.enabled:
                async with self.session_factory() as ps:
                    tokens = await PushTokenRepository(ps).get_for_device(device)
                if tokens and await self.push_service.send(
                    tokens=tokens, title=title, body=body,
                    data={"type": "schedule_needs_approval", "schedule_id": sched.id},
                ) > 0:
                    return
            if await self._device_connected(device):
                spec = TaskSpec(
                    id=f"schedule_notice_{sched.id}", name=title, input=body, type=TaskType.ONE_SHOT,
                    capability=REMINDER_CAPABILITY, parameters={"title": title, "body": body},
                    timeout=60.0, retry_count=0)
                task = await self.task_manager.create_task(
                    spec, user_id=sched.user_id, session_id=f"schedule:{sched.id}",
                    executor_id=self.device_executor_name, device_id=device,
                    source=TaskSource.SCHEDULED,
                    metadata={"source": "scheduler", "schedule_id": sched.id, "notice": True})
                await self.task_manager.submit(task.id, wait=False)
        except Exception:
            logger.exception("Schedule %s: could not notify the user", sched.id)

    # ------------------------------------------------------------------
    # Recording + audit
    # ------------------------------------------------------------------

    async def _record(
        self, sched: ScheduleModel, repo: ScheduleRepository, now: datetime, status: LastRunStatus,
        *, error: Optional[str] = None, task_id: Optional[str] = None,
        plugin_id: Optional[str] = None, device_id: Optional[str] = None,
    ) -> None:
        await repo.record_run(sched.id, status=status, at=now, error=error, task_id=task_id)
        self._audit(sched.id, sched.user_id, sched.capability, status, error=error, task_id=task_id,
                    plugin_id=plugin_id, device_id=device_id or sched.target_device_id)

    def _audit(
        self, schedule_id: str, user_id: str, capability: str, status: str, *,
        error: Optional[str], task_id: Optional[str], plugin_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> None:
        """Every firing decision (ran / skipped / denied / failed) goes to the security audit log."""
        logger.info("schedule %s fired: %s (%s)", schedule_id, status, error or "")
        if self.audit_logger is None:
            return
        try:
            self.audit_logger.record(
                _AUDIT_FOR_STATUS[status], user_id=user_id, plugin_id=plugin_id,
                capability=capability, allowed=status == "ok", reason=error or status,
                device_id=device_id, task_id=task_id, metadata={"schedule_id": schedule_id})
        except Exception:
            logger.exception("audit write failed for schedule %s", schedule_id)

    async def _deliver_push(self, sched, task, target_device: str) -> None:
        """Try push, fall back to WS, fail only if both are unavailable."""
        push_enabled = bool(
            self.push_service and self.push_service.enabled
        )
        had_tokens = False

        # ---- 1. Try push (send only — don't mutate task state here) ----
        sent = 0
        if push_enabled:
            try:
                async with self.session_factory() as ps:
                    tokens = await PushTokenRepository(ps).get_for_device(
                        target_device,
                    )
                had_tokens = bool(tokens)

                if tokens:
                    sent = await self.push_service.send(
                        tokens=tokens,
                        title=sched.title,
                        body=sched.body or "",
                        data={
                            "type": "schedule_reminder",
                            "schedule_id": sched.id,
                            "task_id": task.id,
                        },
                    )
                    logger.info(
                        "Schedule %s: push sent to %d/%d tokens",
                        sched.id, sent, len(tokens),
                    )
            except Exception:
                logger.exception("Schedule %s: push send threw", sched.id)
                sent = 0

        # ---- 2. Commit task state — either via-push or via-WS ----
        if sent > 0:
            try:
                await self.task_manager.complete_via_push(
                    task.id,
                    payload={"delivered_via": "push", "recipients": sent},
                )
                return
            except Exception:
                # Task is now in an intermediate state — do NOT try to
                # submit it, it will race. Mark failed directly.
                logger.exception(
                    "Schedule %s: complete_via_push failed after send",
                    sched.id,
                )
                await self.task_manager.fail_undelivered(
                    task.id,
                    "push delivered but local state update failed",
                    extra={
                        "push_enabled": push_enabled,
                        "had_tokens": had_tokens,
                        "sent": sent,
                        "schedule_id": sched.id,
                    },
                )
                return

        # ---- 3. WS fallback ----
        try:
            await self.task_manager.submit(task.id, wait=False)
            logger.info("Schedule %s: dispatched via WS fallback", sched.id)
            return
        except Exception as exc:
            logger.warning(
                "Schedule %s: WS fallback also failed: %s", sched.id, exc,
            )

        # ---- 4. Both channels dead ----
        await self.task_manager.fail_undelivered(
            task.id,
            "no delivery channel available",
            extra={
                "push_enabled": push_enabled,
                "had_tokens": had_tokens,
                "sent": sent,
                "schedule_id": sched.id,
            },
        )

    async def _handle_undeliverable(self, sched, repo, now, reason: str, due_at: Optional[datetime] = None):
        if self._last_undeliverable.get(sched.id) != reason:
            logger.warning(
                "Schedule %s undeliverable (%s) — catch_up=%s",
                sched.id, reason, sched.catch_up,
            )
            self._last_undeliverable[sched.id] = reason
            await self._record(sched, repo, now, "device_offline", error=reason)
        else:
            logger.debug(
                "Schedule %s still undeliverable (%s)", sched.id, reason,
            )
        if sched.catch_up == "none":
            await self._advance(sched, repo, now, due_at)

    async def _advance(self, sched, repo, now, due_at: Optional[datetime] = None):
        """Move to the next occurrence after ``due_at`` (a replayed run) or after ``now``."""
        next_at = self._next_fire_at(sched, due_at or now)
        await repo.mark_fired(sched.id, fired_at=now, next_fire_at=next_at)
        sched.last_fired_at, sched.next_fire_at = now, next_at      # keep the loaded row in step
        if next_at is None:
            sched.enabled = False


    async def _resolve_device(self, sched) -> Optional[str]:
        if sched.target_device_id:
            return sched.target_device_id          # trust the target, check connectivity later
        try:
            devices = await self.device_manager.list_devices(sched.user_id)
        except Exception:
            return None
        # prefer a connected device, but fall back to any device the user owns
        fallback = None
        for d in devices:
            dev_id = getattr(d, "id", None)
            if not dev_id:
                continue
            fallback = fallback or dev_id
            if await self.device_manager.is_connected(dev_id):
                return dev_id
        return fallback

    # ------------------------------------------------------------------
    # Time math
    # ------------------------------------------------------------------

    def _next_fire_at(
        self, sched: ScheduleModel, after: datetime,
    ) -> Optional[datetime]:
        if sched.kind == "one_shot":
            return None    # fires once, then disabled
        if sched.kind == "interval":
            secs = sched.interval_seconds or 3600
            return after + timedelta(seconds=secs)
        if sched.kind == "cron" and sched.cron:
            tz = ZoneInfo(sched.timezone or "UTC")
            local_after = after.astimezone(tz)
            itr = croniter(sched.cron, local_after)
            return itr.get_next(datetime).astimezone(timezone.utc)
        return None

    def compute_initial_next(self, sched: ScheduleModel) -> Optional[datetime]:
        """Called once at creation time."""
        now = datetime.now(timezone.utc)
        if sched.kind == "one_shot":
            return sched.fire_at
        if sched.kind == "interval":
            secs = sched.interval_seconds or 3600
            return now + timedelta(seconds=secs)
        if sched.kind == "cron" and sched.cron:
            tz = ZoneInfo(sched.timezone or "UTC")
            itr = croniter(sched.cron, now.astimezone(tz))
            return itr.get_next(datetime).astimezone(timezone.utc)
        return None