import asyncio
from typing import Awaitable, Dict, Set, TypeVar

from loguru import logger

T = TypeVar("T")


class TaskTracker:
    """
    Tracks spawned tasks so shutdown can wait for them safely.

    `cancel_all` awaits each tracked task with a timeout first. Only
    tasks still pending after the timeout are cancelled — and one at a
    time, so a cycle in one task doesn't abort the cancellation of the
    rest.
    """

    def __init__(self) -> None:
        self._tasks: Dict[asyncio.Task, str] = {}

    def spawn(self, coro: Awaitable[T], *, name: str = "") -> asyncio.Task:
        task = asyncio.create_task(coro, name=name or None)
        self._tasks[task] = name
        task.add_done_callback(self._forget)
        return task

    def _forget(self, task: asyncio.Task) -> None:
        self._tasks.pop(task, None)

    def __len__(self) -> int:
        return len(self._tasks)

    async def cancel_all(self, timeout: float = 5.0) -> None:
        if not self._tasks:
            return

        tasks = list(self._tasks)
        logger.info("TaskTracker: waiting for {} background task(s)", len(tasks))

        # Phase 1 — give them a chance to finish on their own.
        # This is what avoids the cyclic-cancel scenario in the common
        # case: no cancel = no awaiter walk.
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        # Phase 2 — cancel whatever remains, one at a time so a
        # RecursionError in one task's awaiter chain doesn't abort the rest.
        for task in pending:
            try:
                task.cancel()
            except RecursionError:
                logger.error(
                    "TaskTracker: RecursionError cancelling {!r}",
                    self._tasks.get(task, task.get_name()),
                )
                continue
            try:
                await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "TaskTracker: {!r} did not exit after cancel",
                    self._tasks.get(task, task.get_name()),
                )
            except RecursionError:
                logger.error(
                    "TaskTracker: RecursionError awaiting cancellation of {!r}",
                    self._tasks.get(task, task.get_name()),
                )

        self._tasks.clear()