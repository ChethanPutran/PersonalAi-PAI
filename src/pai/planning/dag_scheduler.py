from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.event_bus.bus import EventBus
from pai.event_bus.events import EventType
from pai.observability.tracer import get_tracer
from pai.orchestration.execution_context import ExecutionContext
from pai.tasks.manager import TaskManager
from pai.tasks.models import TaskResult
from pai.planning.dag_models import (
    CancelOutcome, DagEvent, DagFailedEvent, DagFinishedEvent, DagResult, DagStartedEvent, DagStatus,
    DagTaskFinishedEvent, DagTaskStartedEvent, SkippedTask,
)
from pai.planning.models import TaskSpec


@dataclass
class _DagRun:
    """Live bookkeeping for one execute() call, so single tasks can be killed."""
    context: ExecutionContext
    task_map: Dict[str, TaskSpec]
    started: set = field(default_factory=set)
    finished: set = field(default_factory=set)
    skipped: Dict[str, str] = field(default_factory=dict)
    results: List[TaskResult | SkippedTask] = field(default_factory=list)
    user_cancelled: set = field(default_factory=set)


class DAGScheduler(Lifecycle):

    def __init__(
        self,
        *,
        event_bus: EventBus,
        task_manager: TaskManager,
        max_concurrency: int = 4,
    ):
        self.task_manager = task_manager
        self._event_bus = event_bus
        self.max_concurrency = max_concurrency
        self._runs: Dict[str, _DagRun] = {}     # request_id -> live run
        logger.debug(
            "DAGScheduler initialized max_concurrency={}", max_concurrency
        )

    async def initialize(self) -> None:
        logger.debug("DAGScheduler initialization complete")
        if not self.task_manager.is_initialized():
            await self.task_manager.initialize()

    def is_initialized(self) -> bool:
        return self.task_manager.is_initialized()

    async def shutdown(self) -> None:
        if self.task_manager.is_initialized():
            await self.task_manager.shutdown()
        logger.debug("DAGScheduler shutdown complete")

    async def execute(
        self,
        tasks: List[TaskSpec],
        context: ExecutionContext,
        wait: bool = True,
    ) -> DagResult:

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
                return DagResult(status=DagStatus.COMPLETED)

            # ------------------------------------------------------------------
            # Build id → task map
            # ------------------------------------------------------------------
            task_map: Dict[str, TaskSpec] = {}
            for task in tasks:
                task_id = task.id
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
                self._emit_model(EventType.DAG_FAILED, DagFailedEvent(
                    error=f"Cycle detected: {cycle_str}", **self._routing(context)))
                raise RuntimeError(f"DAG contains a cycle: {cycle_str}")

            routing = self._routing(context)

            # Dependencies only mean something if we wait for results.
            if not wait and any(t.depends_on for t in task_map.values()):
                logger.warning("DAG has dependencies; ignoring wait=False")
                wait = True

            self._emit_model(EventType.DAG_STARTED, DagStartedEvent(
                task_count=len(task_map), max_concurrency=self.max_concurrency,
                task_ids=list(task_map), **routing))

            # All bookkeeping is by *spec* id (what depends_on refers to).
            # TaskResult.task_id is the runtime task's id and differs.
            completed: set[str] = set()
            failed: set[str] = set()
            run = _DagRun(context=context, task_map=task_map)
            skipped, started, results = run.skipped, run.started, run.results
            self._runs[context.request_id] = run
            semaphore = asyncio.Semaphore(self.max_concurrency)
            started_at = time.perf_counter()

            async def run_one(task_spec: TaskSpec) -> TaskResult | None:
                task_id = task_spec.id
                async with semaphore:
                    async with tracer.span(
                        f"dag.task:{task_spec.capability}",
                        task_id=task_id,
                        capability=task_spec.capability,
                        user_id=context.user_id,
                    ) as task_span:
                        t0 = time.perf_counter()
                        self._emit_model(EventType.DAG_TASK_STARTED, DagTaskStartedEvent(
                            task_id=task_id, capability=task_spec.capability, **routing))
                        try:
                            result = await self.task_manager.run(
                                task_spec, context=context, wait=wait,
                            )
                            task_span.set_attribute(
                                "duration_ms", round((time.perf_counter() - t0) * 1000, 1))
                            return result
                        except asyncio.CancelledError:
                            task_span.set_error("cancelled")
                            raise
                        except Exception as exc:
                            task_span.set_error(f"{type(exc).__name__}: {exc}")
                            raise

            def _unknown_deps(t: TaskSpec) -> List[str]:
                return [d for d in t.depends_on if d not in task_map]

            def _propagate_skips() -> None:
                """Skip (transitively) everything that can no longer run."""
                changed = True
                while changed:
                    changed = False
                    for tid, t in task_map.items():
                        if tid in started or tid in skipped:
                            continue
                        bad = [d for d in t.depends_on if d in failed or d in skipped]
                        unknown = _unknown_deps(t)
                        if bad or unknown:
                            reason = (f"dependency failed: {bad}" if bad
                                      else f"unknown dependency: {unknown}")
                            skipped[tid] = reason
                            results.append(SkippedTask(task_id=tid, error=reason))
                            self._emit_model(EventType.DAG_TASK_FINISHED, DagTaskFinishedEvent(
                                task_id=tid, status="skipped", error=reason, **routing))
                            changed = True

            running: Dict[asyncio.Task, str] = {}
            try:
                while True:
                    _propagate_skips()
                    # Launch everything whose dependencies are ALL complete — right
                    # now, regardless of unrelated tasks still running.
                    for tid, t in task_map.items():
                        if tid in started or tid in skipped:
                            continue
                        if all(d in completed for d in t.depends_on):
                            started.add(tid)
                            running[asyncio.create_task(run_one(t), name=f"dag:{tid}")] = tid
                            logger.debug("DAG launching {} (deps={})", tid, t.depends_on)

                    if not running:
                        break

                    done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                    for fut in done:
                        tid = running.pop(fut)
                        run.finished.add(tid)
                        error: str | None = None
                        try:
                            res = fut.result()
                        except asyncio.CancelledError:
                            # A *child* was cancelled (e.g. the user cancelled that task).
                            # If the DAG itself were cancelled we'd have raised from
                            # asyncio.wait() above, not here.
                            error = "Task cancelled"
                            res = TaskResult(task_id=tid, success=False, error=error)
                        except Exception as exc:
                            # One task blowing up (e.g. authorization denied) must not
                            # abort the DAG: fail it, skip its dependents, keep going.
                            logger.error("DAG task {} raised: {}", tid, exc)
                            error = f"{type(exc).__name__}: {exc}"
                            res = TaskResult(task_id=tid, success=False, error=error)
                        if res is None:
                            # wait=False: submitted, outcome not tracked.
                            completed.add(tid)
                            status = "submitted"
                        else:
                            results.append(res)
                            error = error or getattr(res, "error", None)
                            if res.success:
                                completed.add(tid)
                                status = "completed"
                            else:
                                failed.add(tid)
                                status = "failed"
                        self._emit_model(EventType.DAG_TASK_FINISHED, DagTaskFinishedEvent(
                            task_id=tid, status=status, error=error,
                            completed=len(completed), failed=len(failed),
                            skipped=len(skipped), total=len(task_map), **routing))
            except asyncio.CancelledError:
                logger.warning("DAGScheduler cancelled; cancelling {} running task(s)", len(running))
                for fut in running:
                    fut.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                raise
            finally:
                self._runs.pop(context.request_id, None)

            elapsed_total_s = time.perf_counter() - started_at
            status = DagStatus.FAILED if (failed or skipped) else DagStatus.COMPLETED

            logger.info(
                "DAG execution {} in {:.1f}ms: completed={} failed={} skipped={} total={}",
                status.value, elapsed_total_s * 1000, len(completed), len(failed),
                len(skipped), len(task_map),
            )
            dag_span.set_attribute("status", status.value)
            dag_span.set_attribute("completed", len(completed))
            dag_span.set_attribute("failed", len(failed))
            dag_span.set_attribute("skipped", len(skipped))
            dag_span.set_attribute("duration_ms", round(elapsed_total_s * 1000, 1))

            self._emit_model(
                EventType.DAG_COMPLETED if status == DagStatus.COMPLETED else EventType.DAG_FAILED,
                DagFinishedEvent(
                    status=status, completed=len(completed), failed=len(failed),
                    skipped=len(skipped), duration_ms=round(elapsed_total_s * 1000, 1), **routing),
            )

            return DagResult(
                status=status, results=results, completed=list(completed),
                failed=list(failed), skipped=list(skipped),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def owner_of(self, request_id: str) -> str | None:
        """user_id that started this live DAG (for ownership checks), else None."""
        run = self._runs.get(request_id)
        return run.context.user_id if run else None

    async def cancel(self, request_id: str, spec_id: str) -> CancelOutcome:
        """Kill ONE task of a running DAG without touching independent tasks.

        running  -> that task's runtime task is cancelled (device told to stop);
                    its dependents are skipped, siblings keep running.
        pending  -> it never starts; its dependents are skipped too.
        """
        run = self._runs.get(request_id)
        if run is None or spec_id not in run.task_map:
            return CancelOutcome.NOT_FOUND
        if spec_id in run.finished or spec_id in run.skipped:
            return CancelOutcome.ALREADY_FINISHED

        routing = self._routing(run.context)
        if spec_id not in run.started:
            reason = "cancelled by user"
            run.skipped[spec_id] = reason
            run.results.append(SkippedTask(task_id=spec_id, error=reason))
            self._emit_model(EventType.DAG_TASK_FINISHED, DagTaskFinishedEvent(
                task_id=spec_id, status="skipped", error=reason, **routing))
            return CancelOutcome.CANCELLED_PENDING

        # Running: find its runtime task by spec id within this request's session.
        candidates = await self.task_manager.store.list(
            user_id=run.context.user_id, session_id=run.context.session_id)
        live = [t for t in candidates if t.spec_id == spec_id and not t.terminal]
        if not live:
            return CancelOutcome.NOT_FOUND
        for task in live:
            await self.task_manager.cancel(task.id, reason="Cancelled by user")
        return CancelOutcome.CANCELLED_RUNNING

    @staticmethod
    def _routing(context: ExecutionContext) -> dict:
        """Kwargs for the DagEvent base model (splatted into its constructor)."""
        return {"request_id": context.request_id, "user_id": context.user_id,
                "session_id": getattr(context, "session_id", None)}

    def _emit_model(self, event_type: EventType, payload: DagEvent) -> None:
        # The bus carries plain JSON; serialize only here, at the boundary.
        self._emit(event_type, payload.model_dump(mode="json"))

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
