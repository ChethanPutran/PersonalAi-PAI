from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List


TaskRunnerFn = Callable[
    [Any],
    Awaitable[Dict[str, Any]],
]


class DAGScheduler:

    def __init__(
        self,
        task_runner: TaskRunnerFn,
        max_concurrency: int = 4,
    ):
        self.task_runner = task_runner
        self.max_concurrency = max_concurrency

    async def execute(
        self,
        tasks: List[Any],
    ) -> Dict[str, Any]:

        task_map = {
            self._id(task): task
            for task in tasks
        }

        completed = set()
        failed = set()
        results = []

        semaphore = asyncio.Semaphore(
            self.max_concurrency
        )

        while len(completed) + len(failed) < len(task_map):

            ready = []

            for task_id, task in task_map.items():

                if (
                    task_id in completed
                    or task_id in failed
                ):
                    continue

                dependencies = (
                    self._dict(task)
                    .get("depends_on", [])
                    or []
                )

                # A task can execute only when all dependencies
                # completed successfully.
                if all(
                    dep in completed
                    for dep in dependencies
                ):
                    ready.append(task)

            if not ready:

                unresolved = [
                    task_id
                    for task_id in task_map
                    if task_id not in completed
                    and task_id not in failed
                ]

                raise RuntimeError(
                    "DAG cannot make progress. "
                    f"Unresolved tasks: {unresolved}"
                )

            async def run_one(task):

                async with semaphore:

                    task_id = self._id(task)

                    try:
                        result = await self.task_runner(
                            task
                        )

                        return (
                            task_id,
                            True,
                            result,
                        )

                    except Exception as exc:

                        return (
                            task_id,
                            False,
                            {
                                "task_id": task_id,
                                "status": "failed",
                                "error": str(exc),
                            },
                        )

            batch = await asyncio.gather(
                *(run_one(task) for task in ready)
            )

            for task_id, success, result in batch:

                results.append(result)

                if success:
                    completed.add(task_id)
                else:
                    failed.add(task_id)

        return {
            "status": (
                "failed"
                if failed
                else "completed"
            ),
            "results": results,
            "completed": list(completed),
            "failed": list(failed),
        }

    @staticmethod
    def _dict(task):
        if isinstance(task, dict):
            return task

        if hasattr(task, "to_dict"):
            return task.to_dict()

        return task.__dict__

    @classmethod
    def _id(cls, task):
        return str(
            cls._dict(task)["id"]
        )