"""Scheduler loop — fires schedules into TaskManager."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy.ext.asyncio import async_sessionmaker

from pai.devices.manager import DeviceManager
from pai.executors.manager import ExecutorManager
from pai.notifications.push_service import PushService, PushTokenRepository
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository
from pai.tasks.manager import TaskManager


logger = logging.getLogger(__name__)


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
    ) -> None:
        self.session_factory = session_factory
        self.task_manager = task_manager
        self.executor = executor
        self.device_manager = device_manager
        self.push_service = push_service
        self.tick_seconds = tick_seconds
        self.device_executor_name = device_executor_name
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

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
        target_device = await self._resolve_device(sched)
        if target_device is None:
            await self._handle_undeliverable(sched, repo, now, reason="no_device")
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
                sched, repo, now, reason="device_offline_no_push",
            )
            return

        logger.info(
            "Firing schedule %s (%s) on %s via %s",
            sched.id, sched.title, target_device, delivery,
        )

        # 1. Create + initialize in one call. `create_initialized` requires
        #    capability / device_id / executor_id; create() does not.
        try:
            task = await self.task_manager.create_initialized(
                input=sched.body or sched.title,
                task_type=sched.capability,          # becomes Task.type
                capability=sched.capability,
                device_id=target_device,
                executor_id=self.device_executor_name,
                parameters={
                    # Merge any preset parameters, then stamp the reminder
                    # text so system.notifications.show picks it up.
                    **(sched.parameters or {}),
                    "title": sched.title,
                    "body": sched.body or "",
                },
                user_id=sched.user_id,
                timeout=60.0,       # generous for slow devices
                max_retries=1,
                metadata={
                    "source": "scheduler",
                    "schedule_id": sched.id,
                    "schedule_kind": sched.kind,
                },
            )
        except Exception:
            logger.exception(
                "Schedule %s: task creation failed", sched.id,
            )
            await self._advance(sched, repo, now)
            return

        # 2. Fix the auto-generated title ("Task for <type>") for the UI.
        try:
            await self.task_manager.update_task(task.id, title=sched.title)
        except Exception:
            logger.debug(
                "Schedule %s: title update failed (cosmetic)", sched.id,
            )

        # 3. Queue + run it. wait=False so the scheduler loop doesn't block
        #    on slow devices. Failures flow back as task.failed events.
        if delivery == "push":
            await self._deliver_push(sched, task, target_device)
        else:
            try:
                await self.task_manager.submit(task.id, wait=False)
            except Exception as exc:
                logger.warning("Schedule %s: submit failed: %s", sched.id, exc)

        await self._advance(sched, repo, now)


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

    async def _handle_undeliverable(self, sched, repo, now, reason: str):
        logger.warning(
            "Schedule %s undeliverable (%s) — catch_up=%s",
            sched.id, reason, sched.catch_up,
        )
        if sched.catch_up == "none":
            await self._advance(sched, repo, now)
        # else: leave due — retry next tick when a device comes back.

    async def _advance(self, sched, repo, now):
        next_at = self._next_fire_at(sched, now)
        await repo.mark_fired(sched.id, fired_at=now, next_fire_at=next_at)


    async def _resolve_device(self, sched: ScheduleModel) -> Optional[str]:
        if sched.target_device_id:
            try:
                connected = await self.device_manager.is_connected(
                    sched.target_device_id,
                )
            except Exception:
                connected = False
            return sched.target_device_id if connected else None

        try:
            devices = await self.device_manager.list_devices(sched.user_id)
        except Exception:
            return None
        for d in devices:
            dev_id = getattr(d, "id", None)
            if dev_id and await self.device_manager.is_connected(dev_id):
                return dev_id
        return None

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