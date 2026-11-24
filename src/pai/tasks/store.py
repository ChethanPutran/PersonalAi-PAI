from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Protocol

from pai.tasks.models import Task, TaskStatus


class TaskNotFoundError(KeyError):
    """Raised when a task does not exist."""

class TaskStoreProtocol(Protocol):
    async def create(self, task: Task) -> Task: ...
    async def save(self, task: Task) -> Task: ...
    async def upsert(self, task: Task) -> Task: ...
    async def get(self, task_id: str) -> Optional[Task]: ...
    async def require(self, task_id: str) -> Task: ...
    async def delete(self, task_id: str) -> bool: ...
    async def list(
        self,
        *,
        status: Optional[TaskStatus] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Task]: ...
    async def count(
        self,
        *, status: Optional[TaskStatus] = None,
    ) -> int: ...
    async def clear(self) -> None: ...
    async def recover_orphaned(self) -> List[Task]: ...

class TaskStore:
    """
    Async in-memory task store.

    This is the runtime task store.

    Persistent storage can later be implemented behind the same interface
    using pai.storage.repositories.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._tasks: Dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self.max_history = max_history

    def _evict_terminal(self) -> None:
        """Keep at most ``max_history`` finished tasks (oldest go first); never touches live ones."""
        finished = [t for t in self._tasks.values() if t.terminal]
        excess = len(finished) - self.max_history
        if excess > 0:
            finished.sort(key=lambda t: t.completed_at or t.created_at)
            for t in finished[:excess]:
                del self._tasks[t.id]

    async def create(self, task: Task) -> Task:
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(
                    f"Task already exists: {task.id}"
                )

            self._tasks[task.id] = task
            self._evict_terminal()
            return task

    async def save(self, task: Task) -> Task:
        async with self._lock:
            if task.id not in self._tasks:
                raise TaskNotFoundError(task.id)

            self._tasks[task.id] = task
            self._evict_terminal()
            return task

    async def upsert(self, task: Task) -> Task:
        async with self._lock:
            self._tasks[task.id] = task
            return task

    async def get(self, task_id: str) -> Optional[Task]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def require(self, task_id: str) -> Task:
        task = await self.get(task_id)

        if task is None:
            raise TaskNotFoundError(task_id)

        return task

    async def delete(self, task_id: str) -> bool:
        async with self._lock:
            return self._tasks.pop(task_id, None) is not None

    async def list(
        self,
        *,
        status: Optional[TaskStatus] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Task]:
        async with self._lock:
            tasks = list(self._tasks.values())

        if status is not None:
            tasks = [
                task
                for task in tasks
                if task.status == status
            ]

        if user_id is not None:
            tasks = [
                task
                for task in tasks
                if task.user_id == user_id
            ]

        if session_id is not None:
            tasks = [
                task
                for task in tasks
                if task.session_id == session_id
            ]

        tasks.sort(
            key=lambda task: task.created_at,
            reverse=True,
        )

        if limit is not None:
            tasks = tasks[:limit]

        return tasks

    async def count(
        self,
        *,
        status: Optional[TaskStatus] = None,
    ) -> int:
        tasks = await self.list(status=status)
        return len(tasks)

    async def clear(self) -> None:
        async with self._lock:
            self._tasks.clear()

    async def recover_orphaned(self) -> List[Task]:
        """
        Recover tasks that were left in a RUNNING or QUEUED state by a prior
        process. This is a no-op for the in-memory store because memory is
        fresh on restart.
        """
        return []