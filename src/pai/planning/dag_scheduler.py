from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.observability.tracer import get_tracer
from pai.orchestration.execution_context import ExecutionContext
from pai.orchestration.task_runner import TaskRunner


class DAGScheduler(Lifecycle):

    def __init__(
        self,
        *,
        task_runner: TaskRunner,
        max_concurrency: int = 4,
    ):
        self.task_runner = task_runner
        self.max_concurrency = max_concurrency
        logger.debug(
            "DAGScheduler initialized max_concurrency={}", max_concurrency
        )

    async def initialize(self) -> None:
        logger.debug("DAGScheduler initialization complete")
        if self.task_runner.is_initialized():
            await self.task_runner.initialize()

    async def is_initialized(self) -> bool:
        return self.task_runner.is_initialized()

    async def shutdown(self) -> None:
        if self.task_runner.is_initialized():
            await self.task_runner.shutdown()
        logger.debug("DAGScheduler shutdown complete")

    async def execute(
        self,
        tasks: List[Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:

        tracer = get_tracer()

        async with tracer.span(
            "dag.execute",
            task_count=len(tasks),
            max_concurrency=self.max_concurrency,
            request_id=context.request_id,
        ) as dag_span:

            total = len(tasks)
            logger.info(
                "DAG execution starting: tasks={} max_concurrency={}",
                total, self.max_concurrency,
            )

            if total == 0:
                logger.warning("DAG execution called with empty task list")
                dag_span.set_attribute("status", "empty")
                return {
                    "status": "completed",
                    "results": [],
                    "completed": [],
                    "failed": [],
                }

            # ------------------------------------------------------------------
            # Build id → task map
            # ------------------------------------------------------------------
            task_map: Dict[str, Any] = {}
            for task in tasks:
                task_id = self._id(task)
                if task_id in task_map:
                    logger.warning(
                        "Duplicate task id '{}' in DAG — later entry "
                        "overwrites earlier one. Earlier task: {}",
                        task_id, self._dict(task),
                    )
                task_map[task_id] = task

            if len(task_map) != total:
                logger.warning(
                    "DAG has {} tasks but only {} unique ids — "
                    "{} duplicate id(s) collapsed",
                    total, len(task_map), total - len(task_map),
                )
                dag_span.set_attribute("duplicate_ids", total - len(task_map))

            # ------------------------------------------------------------------
            # Cycle detection
            # ------------------------------------------------------------------
            cycle = self._detect_cycle(task_map)
            if cycle:
                cycle_str = " → ".join(cycle)
                logger.error(
                    "DAG contains a cycle, refusing to execute: {}", cycle_str
                )
                dag_span.set_error(f"Cycle detected: {cycle_str}")
                raise RuntimeError(f"DAG contains a cycle: {cycle_str}")

            completed: set[str] = set()
            failed: set[str] = set()
            results: List[Any] = []
            semaphore = asyncio.Semaphore(self.max_concurrency)

            started_at = time.perf_counter()
            round_index = 0

            # ------------------------------------------------------------------
            # Main scheduling loop
            # ------------------------------------------------------------------
            while len(completed) + len(failed) < len(task_map):

                round_index += 1

                ready: List[Any] = []
                blocked_info: Dict[str, List[str]] = {}

                for task_id, task in task_map.items():
                    if task_id in completed or task_id in failed:
                        continue
                    dependencies = self._dict(task).get("depends_on", []) or []
                    if all(dep in completed for dep in dependencies):
                        ready.append(task)
                    else:
                        unmet = [d for d in dependencies if d not in completed]
                        blocked_info[task_id] = unmet

                logger.info(
                    "DAG round {}: completed={} failed={} ready={} "
                    "blocked={} (of {} tasks)",
                    round_index,
                    len(completed),
                    len(failed),
                    len(ready),
                    len(blocked_info),
                    len(task_map),
                )

                if blocked_info:
                    logger.debug(
                        "DAG round {} blocked: {}",
                        round_index,
                        {tid: unmet for tid, unmet in blocked_info.items()},
                    )

                if not ready:
                    unresolved = [
                        task_id
                        for task_id in task_map
                        if task_id not in completed and task_id not in failed
                    ]
                    logger.error(
                        "DAG cannot make progress: {} unresolved task(s). "
                        "Details: {}",
                        len(unresolved),
                        {tid: blocked_info.get(tid, []) for tid in unresolved},
                    )
                    dag_span.set_error(
                        f"Deadlock: {len(unresolved)} unresolved tasks"
                    )
                    raise RuntimeError(
                        f"DAG cannot make progress. Unresolved tasks: {unresolved}"
                    )

                logger.debug(
                    "DAG round {} launching {} ready task(s): {}",
                    round_index, len(ready),
                    [self._id(t) for t in ready],
                )

                # ------------------------------------------------------------------
                # Run ready tasks with a per-task span
                # ------------------------------------------------------------------
                async def run_one(task: Any):
                    task_id = self._id(task)
                    task_dict = self._dict(task)
                    capability = (
                        task_dict.get("capability") or task_dict.get("type")
                    )

                    async with semaphore:
                        async with tracer.span(
                            f"dag.task:{capability}",
                            task_id=task_id,
                            capability=capability,
                            round=round_index,
                            user_id=context.user_id,
                        ) as task_span:
                            t0 = time.perf_counter()
                            logger.debug(
                                "Task {} starting (round {})",
                                task_id, round_index,
                            )
                            try:
                                result = await self.task_runner.run(
                                    task, context=context,
                                )
                                elapsed_ms = (time.perf_counter() - t0) * 1000
                                logger.info(
                                    "Task {} completed in {:.1f}ms",
                                    task_id, elapsed_ms,
                                )
                                task_span.set_attribute(
                                    "duration_ms", round(elapsed_ms, 1),
                                )
                                return task_id, True, result

                            except asyncio.CancelledError:
                                elapsed_ms = (time.perf_counter() - t0) * 1000
                                logger.warning(
                                    "Task {} cancelled after {:.1f}ms",
                                    task_id, elapsed_ms,
                                )
                                task_span.set_error("cancelled")
                                raise

                            except Exception as exc:
                                elapsed_ms = (time.perf_counter() - t0) * 1000
                                logger.exception(
                                    "Task {} failed after {:.1f}ms: {}",
                                    task_id, elapsed_ms, exc,
                                )
                                task_span.set_error(f"{type(exc).__name__}: {exc}")
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

                # Process results INSIDE the loop
                for task_id, success, result in batch:
                    results.append(result)
                    if success:
                        completed.add(task_id)
                    else:
                        failed.add(task_id)

                logger.debug(
                    "DAG round {} finished: {} ok, {} failed this round",
                    round_index,
                    sum(1 for _, s, _ in batch if s),
                    sum(1 for _, s, _ in batch if not s),
                )

            # ------------------------------------------------------------------
            # Handle tasks blocked by failed dependencies
            # ------------------------------------------------------------------
            elapsed_total_s = time.perf_counter() - started_at

            still_pending = [
                tid for tid in task_map
                if tid not in completed and tid not in failed
            ]
            if still_pending:
                logger.warning(
                    "DAG finished with {} task(s) never executed because "
                    "a dependency failed: {}",
                    len(still_pending),
                    {
                        tid: [
                            d for d in (
                                self._dict(task_map[tid]).get("depends_on", []) or []
                            ) if d in failed
                        ]
                        for tid in still_pending
                    },
                )
                for tid in still_pending:
                    results.append({
                        "task_id": tid,
                        "status": "skipped",
                        "error": "dependency failed",
                    })
                dag_span.set_attribute("skipped", len(still_pending))

            status = "failed" if failed else "completed"

            logger.info(
                "DAG execution {} in {:.1f}ms: completed={} failed={} "
                "skipped={} total={}",
                status,
                elapsed_total_s * 1000,
                len(completed),
                len(failed),
                len(still_pending),
                len(task_map),
            )

            dag_span.set_attribute("status", status)
            dag_span.set_attribute("completed", len(completed))
            dag_span.set_attribute("failed", len(failed))
            dag_span.set_attribute(
                "duration_ms", round(elapsed_total_s * 1000, 1),
            )

            return {
                "status": status,
                "results": results,
                "completed": list(completed),
                "failed": list(failed),
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dict(task):
        if isinstance(task, dict):
            return task
        if hasattr(task, "to_dict"):
            return task.to_dict()
        return task.__dict__

    @classmethod
    def _id(cls, task):
        data = cls._dict(task)
        task_id = data.get("id")
        if task_id is None:
            raise ValueError(f"Task has no 'id' field: {data}")
        return str(task_id)

    @staticmethod
    def _detect_cycle(task_map: Dict[str, Any]) -> List[str]:
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: Dict[str, int] = {tid: WHITE for tid in task_map}
        stack: List[str] = []

        def _deps(tid: str) -> List[str]:
            data = DAGScheduler._dict(task_map[tid])
            return [str(d) for d in (data.get("depends_on") or [])]

        def dfs(tid: str) -> List[str]:
            colour[tid] = GRAY
            stack.append(tid)
            for dep in _deps(tid):
                if dep not in task_map:
                    continue
                if colour[dep] == GRAY:
                    idx = stack.index(dep)
                    return stack[idx:] + [dep]
                if colour[dep] == WHITE:
                    res = dfs(dep)
                    if res:
                        return res
            stack.pop()
            colour[tid] = BLACK
            return []

        for tid in task_map:
            if colour[tid] == WHITE:
                res = dfs(tid)
                if res:
                    return res
        return []