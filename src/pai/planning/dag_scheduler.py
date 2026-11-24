from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.event_bus.bus import EventBus
from pai.event_bus.events import EventType
from pai.observability.tracer import get_tracer
from pai.orchestration.execution_context import ExecutionContext
from pai.tasks.lifecycle import TaskControlError
from pai.tasks.manager import TaskManager
from pai.tasks.models import TaskResult
from pai.planning.dag_models import (
    CancelOutcome, DagCounts, DagEdge, DagEvent, DagFailedEvent, DagNodeSnapshot, DagRunStatus,
    DagSnapshot, KillRunResult, NodeStatus, DagFinishedEvent, DagResult, DagStartedEvent, DagStatus,
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
    futures: Dict[str, "asyncio.Task"] = field(default_factory=dict)   # spec id -> its run_one future
    # --- live view (read by the admin DAG panel) ---
    nodes: Dict[str, DagNodeSnapshot] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    killed: bool = False

    def init_nodes(self) -> None:
        for tid, spec in self.task_map.items():
            self.nodes[tid] = DagNodeSnapshot(
                spec_id=tid, capability=spec.capability,
                task_type=getattr(spec.type, "value", str(spec.type)),
                depends_on=list(spec.depends_on))

    def mark_running(self, tid: str) -> None:
        n = self.nodes.get(tid)
        if n is not None:
            n.status, n.started_at = NodeStatus.RUNNING, datetime.now(timezone.utc)

    def mark_done(self, tid: str, status: NodeStatus, *, error: Optional[str] = None,
                  runtime_task_id: Optional[str] = None) -> None:
        n = self.nodes.get(tid)
        if n is None:
            return
        n.status, n.error = status, error
        n.finished_at = datetime.now(timezone.utc)
        if runtime_task_id and runtime_task_id != tid:
            n.runtime_task_id = runtime_task_id
        if n.started_at is not None:
            n.duration_ms = round((n.finished_at - n.started_at).total_seconds() * 1000, 1)

    def run_status(self) -> DagRunStatus:
        if self.finished_at is None:
            return DagRunStatus.RUNNING
        if self.killed:
            return DagRunStatus.CANCELLED
        bad = {NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.CANCELLED}
        return DagRunStatus.FAILED if any(n.status in bad for n in self.nodes.values()) else DagRunStatus.COMPLETED

    def to_snapshot(self, runtime_ids: Optional[Dict[str, str]] = None) -> DagSnapshot:
        nodes = [n.model_copy(deep=True) for n in self.nodes.values()]
        for n in nodes:
            if runtime_ids and not n.runtime_task_id:
                n.runtime_task_id = runtime_ids.get(n.spec_id)
        counts = DagCounts(**{k: sum(1 for n in nodes if n.status.value == k) for k in DagCounts.model_fields})
        end = self.finished_at
        return DagSnapshot(
            request_id=self.context.request_id, user_id=self.context.user_id,
            session_id=getattr(self.context, "session_id", None), status=self.run_status(),
            started_at=self.started_at, finished_at=end,
            duration_ms=round(((end or datetime.now(timezone.utc)) - self.started_at).total_seconds() * 1000, 1),
            nodes=nodes,
            edges=[DagEdge(source=d, target=n.spec_id) for n in nodes for d in n.depends_on if d in self.nodes],
            counts=counts)


class DAGScheduler(Lifecycle):

    def __init__(
        self,
        *,
        event_bus: EventBus,
        task_manager: TaskManager,
        max_concurrency: int = 4,
        history_size: int = 200,
        history_store: Any = None,
    ):
        self.task_manager = task_manager
        self._event_bus = event_bus
        self.max_concurrency = max_concurrency
        self._runs: Dict[str, _DagRun] = {}     # request_id -> live run
        # Finished runs for the admin DAG view. Bounded: the oldest are dropped.
        self._history: Deque[DagSnapshot] = deque(maxlen=max(1, history_size))
        # Optional persistent store (pai.api.admin.history.AdminHistory): finished runs survive a restart and
        # stay viewable for its retention window, beyond the bounded in-memory deque.
        self._store = history_store
        self._stored_cache: Dict[Any, Any] = {}
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
            run.init_nodes()
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
                            run.mark_done(tid, NodeStatus.SKIPPED, error=reason)
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
                            run.mark_running(tid)
                            fut = asyncio.create_task(run_one(t), name=f"dag:{tid}")
                            running[fut] = tid
                            run.futures[tid] = fut
                            logger.debug("DAG launching {} (deps={})", tid, t.depends_on)

                    if not running:
                        break

                    done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                    for fut in done:
                        tid = running.pop(fut)
                        run.futures.pop(tid, None)
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
                        if tid in run.user_cancelled and (res is None or not res.success):
                            # The user killed this node; however the unwinding surfaced (a cancelled
                            # DB call can raise instead of CancelledError), it is a cancellation.
                            error = "Task cancelled"
                            res = TaskResult(task_id=getattr(res, "task_id", None) or tid, success=False, error=error)
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
                        run.mark_done(
                            tid,
                            (NodeStatus.CANCELLED if status == "failed" and error == "Task cancelled"
                             else NodeStatus.FAILED if status == "failed" else NodeStatus.COMPLETED),
                            error=error if status == "failed" else None,
                            runtime_task_id=getattr(res, "task_id", None))
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
                run.finished_at = datetime.now(timezone.utc)
                if run.run_status() == DagRunStatus.RUNNING:       # (defensive) never archive as running
                    run.killed = True
                finished = run.to_snapshot()
                self._history.append(finished)
                self._runs.pop(context.request_id, None)
                self._persist(finished)

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

    # ------------------------------------------------------------------
    # Read-only views (admin panel)
    # ------------------------------------------------------------------

    async def _runtime_ids(self, run: _DagRun) -> Dict[str, str]:
        """spec id -> runtime task id for a live run (via the stable idempotency key)."""
        try:
            tasks = await self.task_manager.store.list(
                user_id=run.context.user_id, session_id=getattr(run.context, "session_id", None))
        except Exception as exc:                       # a view must never break the scheduler
            logger.debug("runtime id lookup failed: {}", exc)
            return {}
        prefix = f"{run.context.request_id}:"
        return {t.idempotency_key[len(prefix):]: t.id for t in tasks
                if t.idempotency_key and t.idempotency_key.startswith(prefix)}

    def _persist(self, snap: DagSnapshot) -> None:
        if self._store is None:
            return
        try:
            self._store.append_dag(snap.model_dump(mode="json"))
        except Exception as exc:                      # history must never break a run
            logger.warning("could not persist DAG run {}: {}", snap.request_id, exc)

    def _stored(self, *, days: Optional[float], limit: int) -> List[DagSnapshot]:
        """Finished runs from the persistent history. Cached for a few seconds: the admin DAG list polls
        every 2 s and anything that just finished is already in the in-memory history."""
        if self._store is None:
            return []
        key = (days, limit)
        hit = self._stored_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < 10.0:
            return hit[1]
        out = self._read_stored(days=days, limit=limit)
        self._stored_cache = {key: (time.monotonic(), out)}
        return out

    def _read_stored(self, *, days: Optional[float], limit: int) -> List[DagSnapshot]:
        out: List[DagSnapshot] = []
        try:
            for rec in self._store.read_dags(days=days, limit=limit):
                try:
                    out.append(DagSnapshot.model_validate(rec))
                except Exception:
                    continue                          # a record from an older schema is skipped, not fatal
        except Exception as exc:
            logger.warning("could not read DAG history: {}", exc)
        return out

    async def snapshot(self, request_id: str) -> Optional[DagSnapshot]:
        """A live run, else a finished one (in memory, else from the persistent history), else None."""
        run = self._runs.get(request_id)
        if run is not None:
            return run.to_snapshot(await self._runtime_ids(run))
        mem = next((s for s in reversed(self._history) if s.request_id == request_id), None)
        if mem is not None or self._store is None:
            return mem
        try:
            rec = self._store.get_dag(request_id)
            return DagSnapshot.model_validate(rec) if rec else None
        except Exception:
            return None

    async def list_runs(self, *, active_only: bool = False, days: Optional[float] = None,
                        limit: int = 500) -> List[DagSnapshot]:
        """Live runs first (oldest first), then finished runs newest first: the in-memory history, then
        older ones from the persistent history (within `days`, at most the store's retention)."""
        # Iterate a COPY: we await inside the loop, and a run that finishes meanwhile removes
        # itself from _runs ("dictionary changed size during iteration").
        live = [r.to_snapshot(await self._runtime_ids(r)) for r in list(self._runs.values())]
        live.sort(key=lambda s: s.started_at)
        if active_only:
            return live
        finished = list(reversed(self._history))
        if self._store is not None:
            have = {s.request_id for s in finished} | {s.request_id for s in live}
            finished += [s for s in self._stored(days=days, limit=limit) if s.request_id not in have]
            finished.sort(key=lambda s: s.finished_at or s.started_at, reverse=True)
        return live + finished[: max(0, limit)]

    def active_run_count(self) -> int:
        return len(self._runs)

    async def kill_run(self, request_id: str) -> Optional[KillRunResult]:
        """Kill every unfinished node of a run. Pending nodes first, so killing a running
        node can't let a dependent slip in; None if the run is not live."""
        run = self._runs.get(request_id)
        if run is None:
            return None
        run.killed = True
        out = KillRunResult(request_id=request_id)
        for status in (NodeStatus.PENDING, NodeStatus.RUNNING):
            for tid in [t for t, n in run.nodes.items() if n.status == status]:
                outcome = await self.cancel(request_id, tid)
                if outcome == CancelOutcome.CANCELLED_PENDING:
                    out.cancelled_pending += 1
                elif outcome == CancelOutcome.CANCELLED_RUNNING:
                    out.cancelled_running += 1
                elif outcome == CancelOutcome.NOT_CANCELLABLE:
                    out.not_cancellable += 1
                else:
                    out.already_finished += 1
        return out

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
            run.mark_done(spec_id, NodeStatus.CANCELLED, error=reason)
            run.results.append(SkippedTask(task_id=spec_id, error=reason))
            self._emit_model(EventType.DAG_TASK_FINISHED, DagTaskFinishedEvent(
                task_id=spec_id, status="skipped", error=reason, **routing))
            return CancelOutcome.CANCELLED_PENDING

        # Running: find its runtime task by spec id within this request's session.
        candidates = await self.task_manager.store.list(
            user_id=run.context.user_id, session_id=run.context.session_id)
        # Match this run's task exactly (request_id:spec_id), not just the spec id: two live
        # runs may reuse spec ids ("a", "b") in one session and must not kill each other.
        key = f"{request_id}:{spec_id}"
        live = [t for t in candidates if t.idempotency_key == key and not t.terminal]
        fut = run.futures.get(spec_id)
        if not live and (fut is None or fut.done()):
            return CancelOutcome.NOT_FOUND
        for task in live:
            try:
                await self.task_manager.cancel(task.id, reason="Cancelled by user")
            except TaskControlError as exc:
                if exc.code == "control_not_supported":
                    # Same rule as everywhere else: this action can't be cancelled once started.
                    # Leave the node (and its future) alone; it will run to completion.
                    return CancelOutcome.NOT_CANCELLABLE
                raise
        # Belt and braces: the runtime task may not be registered with the TaskManager yet (the node is
        # "running" as soon as it is launched), in which case cancel() above could not stop its wrapper.
        # Cancelling this node's own future unwinds whatever is awaiting inside it.
        if fut is not None and not fut.done():
            run.user_cancelled.add(spec_id)
            fut.cancel()
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
