"""SchedulerService._fire creates tasks with the CURRENT TaskManager API (it used a removed one)."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pai.scheduler.scheduler_service import SchedulerService
from pai.tasks.models import TaskSource, TaskType


class Devices:
    async def is_connected(self, device_id): return True


class FakeRepo:
    def __init__(self): self.runs = []
    async def record_run(self, schedule_id, *, status, at, error=None, task_id=None):
        self.runs.append((schedule_id, status, task_id))


async def test_fire_creates_a_scheduled_task_and_submits_it(task_manager):
    svc = object.__new__(SchedulerService)
    svc.task_manager, svc.device_manager, svc.push_service = task_manager, Devices(), None
    svc.device_executor_name, svc.session_factory = "builtin_executor", None
    svc.validator, svc.audit_logger, svc._bg, svc._last_undeliverable = None, None, set(), {}
    repo = FakeRepo()
    advanced, submitted = [], []

    async def resolve(sched): return "self"
    async def advance(sched, repo, now, due_at=None): advanced.append(sched.id)
    real_submit = task_manager.submit
    async def spy_submit(task_id, **kw):
        submitted.append(task_id)
        return await real_submit(task_id, **kw)
    svc._resolve_device, svc._advance = resolve, advance
    task_manager.submit = spy_submit

    sched = SimpleNamespace(
        id="862e263a", title="Call Mom", body="at noon", capability="system.notifications.show",
        kind="one_shot", target_device_id=None, last_run_status=None,
        user_id="u1", parameters={"repeat": "one_shot"},
        catch_up="once", misfire_grace_seconds=None, next_fire_at=None)
    now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    await svc._fire(sched, repo=repo, now=now)
    await asyncio.sleep(0.2)                                   # let the background run settle

    assert advanced == ["862e263a"]                            # schedule moved on exactly once
    (task,) = await task_manager.list()
    assert submitted == [task.id]
    assert task.name == "Call Mom" and task.capability == "system.notifications.show"
    assert repo.runs == [("862e263a", "ok", task.id)]                  # the outcome is remembered
    assert task.type == TaskType.ONE_SHOT and task.source == TaskSource.SCHEDULED
    assert task.user_id == "u1" and task.session_id == "schedule:862e263a"
    assert task.parameters == {"repeat": "one_shot", "title": "Call Mom", "body": "at noon"}
    assert task.idempotency_key == f"schedule:862e263a:{now.isoformat()}"
    assert task.metadata["schedule_id"] == "862e263a" and task.metadata["source"] == "scheduler"
