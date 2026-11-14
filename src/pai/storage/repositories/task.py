"""DB-backed task store — same interface as pai.tasks.store.TaskStore."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pai.storage.models import TaskModel
from pai.tasks.models import Task, TaskStatus
from pai.tasks.store import TaskNotFoundError


logger = logging.getLogger(__name__)


class TaskRepository:
    """Persistent Task store. Async, session-per-call.

    Every write opens its own session + commit — no long-held sessions,
    so the manager can call from arbitrary coroutines without holding
    up the pool. If you later move to a unit-of-work per request, swap
    the session_factory for a caller-scoped one.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.session_factory = session_factory

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(self, task: Task) -> Task:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            existing = await session.get(TaskModel, task.id)
            if existing is not None:
                raise ValueError(f"Task already exists: {task.id}")

            session.add(TaskModel(
                id=task.id,
                status=task.status.value,
                user_id=task.user_id,
                session_id=task.session_id,
                parent_task_id=task.parent_task_id,
                created_at=task.created_at,
                payload=task.to_dict(),
                updated_at=now,
            ))
            await session.commit()
        return task

    async def save(self, task: Task) -> Task:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            row = await session.get(TaskModel, task.id)
            if row is None:
                raise TaskNotFoundError(task.id)

            row.status = task.status.value
            row.user_id = task.user_id
            row.session_id = task.session_id
            row.parent_task_id = task.parent_task_id
            row.payload = task.to_dict()
            row.updated_at = now

            await session.commit()
        return task

    async def upsert(self, task: Task) -> Task:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            row = await session.get(TaskModel, task.id)
            if row is None:
                session.add(TaskModel(
                    id=task.id,
                    status=task.status.value,
                    user_id=task.user_id,
                    session_id=task.session_id,
                    parent_task_id=task.parent_task_id,
                    created_at=task.created_at,
                    payload=task.to_dict(),
                    updated_at=now,
                ))
            else:
                row.status = task.status.value
                row.user_id = task.user_id
                row.session_id = task.session_id
                row.parent_task_id = task.parent_task_id
                row.payload = task.to_dict()
                row.updated_at = now

            await session.commit()
        return task

    async def delete(self, task_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                delete(TaskModel).where(TaskModel.id == task_id),
            )
            await session.commit()
            return result.rowcount > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, task_id: str) -> Optional[Task]:
        async with self.session_factory() as session:
            row = await session.get(TaskModel, task_id)
            if row is None:
                return None
            return self._row_to_task(row)

    async def require(self, task_id: str) -> Task:
        task = await self.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def list(
        self,
        *,
        status: Optional[TaskStatus] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Task]:
        async with self.session_factory() as session:
            stmt = (
                select(TaskModel)
                .order_by(TaskModel.created_at.desc())
            )
            if status is not None:
                stmt = stmt.where(TaskModel.status == status.value)
            if user_id is not None:
                stmt = stmt.where(TaskModel.user_id == user_id)
            if session_id is not None:
                stmt = stmt.where(TaskModel.session_id == session_id)
            if limit is not None:
                stmt = stmt.limit(limit)

            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_task(r) for r in rows]

    async def count(
        self,
        *,
        status: Optional[TaskStatus] = None,
    ) -> int:
        async with self.session_factory() as session:
            stmt = select(func.count()).select_from(TaskModel)
            if status is not None:
                stmt = stmt.where(TaskModel.status == status.value)
            return (await session.execute(stmt)).scalar_one()

    async def clear(self) -> None:
        """Wipe all tasks — tests only."""
        async with self.session_factory() as session:
            await session.execute(delete(TaskModel))
            await session.commit()

    # ------------------------------------------------------------------
    # Startup recovery — resolve tasks left RUNNING/QUEUED by a crash
    # ------------------------------------------------------------------

    async def recover_orphaned(
        self,
        reason: str = "Interrupted by server restart",
    ) -> int:
        """Mark tasks stuck in transient states as FAILED.

        Without this, a crash mid-run leaves tasks in RUNNING/QUEUED
        forever — the Tasks UI shows them as live and cancel/pause
        attempt to talk to a device that's no longer executing them.
        Called once from TaskManager._on_initialize.
        """
        transient = (TaskStatus.RUNNING.value, TaskStatus.QUEUED.value)
        now = datetime.now(timezone.utc)
        recovered = 0

        async with self.session_factory() as session:
            stmt = select(TaskModel).where(TaskModel.status.in_(transient))
            rows = (await session.execute(stmt)).scalars().all()

            for row in rows:
                task = self._row_to_task(row)
                if task.terminal:
                    continue
                task.mark_failed(reason)
                row.status = task.status.value
                row.payload = task.to_dict()
                row.updated_at = now
                recovered += 1

            await session.commit()

        if recovered:
            logger.warning(
                "TaskRepository: recovered %d orphaned task(s) — %s",
                recovered, reason,
            )
        return recovered

    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_task(row: TaskModel) -> Task:
        # Pydantic parses strings/enums/result dict back into types.
        return Task(**row.payload)