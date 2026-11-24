"""Admin operations: overview, task/DAG views, kill, run goals / DAGs / demos, audit."""
from __future__ import annotations

import asyncio
import gc
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, List, Optional, Sequence

from loguru import logger

from pai.api.admin.demos import DEMOS, build_demo
from pai.api.admin.models import (
    AdminTask, CapabilityInfo, CapabilityList, DemoKind, DeviceCounts, ExecutorInfo, NodeSpec,
    OverviewResponse, ProcessResources, RunAccepted, RunDagRequest, RunGoalRequest, TaskCounts,
    TaskPage, VoiceInfo,
)
from pai.common.enums.permission import Origin
from pai.config.models.admin import AdminConfig
from pai.planning.models import Plan, TaskSpec
from pai.security.audit.models import AuditEventType
from pai.tasks.models import Task, TaskStatus

_STARTED = time.monotonic()
_ACTIVE = (TaskStatus.CREATED, TaskStatus.PENDING, TaskStatus.INITIALIZED, TaskStatus.QUEUED,
           TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.RETRYING)


class AdminError(Exception):
    """An error with an HTTP-ish status for the route layer to translate."""

    def __init__(self, status: int, message: str, issues: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.status, self.message, self.issues = status, message, issues or []


def _process_resources() -> ProcessResources:
    rss_mb = 0.0
    try:                                                    # Linux: current RSS
        with open("/proc/self/statm") as f:
            rss_mb = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except Exception:
        try:
            import resource
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            pass
    try:
        fds: Optional[int] = len(os.listdir("/proc/self/fd"))
    except Exception:
        fds = None
    try:
        n_tasks = len(asyncio.all_tasks())
    except RuntimeError:
        n_tasks = 0
    return ProcessResources(
        rss_mb=round(rss_mb, 1), threads=threading.active_count(), asyncio_tasks=n_tasks,
        open_fds=fds, uptime_seconds=round(time.monotonic() - _STARTED, 1), gc_objects=len(gc.get_objects()))


class AdminService:
    def __init__(self, orchestrator: Any, config: AdminConfig, *, voice: Any = None, history: Any = None) -> None:
        self.orchestrator = orchestrator
        self.config = config
        self.voice = voice
        self.history = history                   # AdminHistory | None (persistent events + DAG runs)
        self._history_subs: List[Any] = []
        self._prune_task: Optional[asyncio.Task] = None
        self._bg: set[asyncio.Task] = set()      # fallback when the orchestrator tracker is absent

    # ------------------------------------------------------------- helpers

    @property
    def tm(self) -> Any:
        return self.orchestrator.task_manager

    @property
    def dag(self) -> Any:
        return self.orchestrator.dag_scheduler

    def _spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task:
        """Run in the background and keep a reference (never an orphan task).

        Deliberately NOT in the orchestrator's tracker: that one is drained for up to 5 s at
        shutdown ("let background memory writes finish"), which would stall shutdown on a
        long-running admin task. Instead ``aclose()`` cancels these, and even if nobody calls it
        the TaskManager interrupts the running tasks at shutdown so the DAG run ends by itself.
        """
        task = asyncio.create_task(coro, name=name)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        task.add_done_callback(self._log_failure)
        return task

    @staticmethod
    def _log_failure(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("admin background run {} failed: {}", task.get_name(), task.exception())

    # ------------------------------------------------------------- history

    async def start_history(self) -> None:
        """Record every task.* / dag.* event (from all sessions) to the persistent history, and prune old
        days now and every few hours. Runs for the life of the server whether or not an admin has the page open."""
        if self.history is None or self._history_subs:
            return
        self.history.prune()
        bus = self.orchestrator.event_bus

        async def record(event: Any) -> None:
            try:
                self.history.append_event(str(getattr(event.event_type, "value", event.event_type)), dict(event.data or {}))
            except Exception as exc:                    # never let history break the bus
                logger.debug("admin history record failed: {}", exc)

        self._history_subs = [await bus.subscribe(pattern, record) for pattern in ("task.*", "dag.*")]

        async def prune_loop() -> None:
            while True:
                await asyncio.sleep(6 * 3600)
                self.history.prune()

        self._prune_task = asyncio.create_task(prune_loop(), name="admin-history-prune")

    async def stop_history(self) -> None:
        if self._prune_task is not None:
            self._prune_task.cancel()
            await asyncio.gather(self._prune_task, return_exceptions=True)
            self._prune_task = None
        bus = self.orchestrator.event_bus
        for sub in self._history_subs:
            try:
                await bus.unsubscribe(sub)
            except Exception:
                pass
        self._history_subs = []

    async def aclose(self) -> None:
        await self.stop_history()
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)

    def audit(self, admin_id: str, action: str, *, task_id: Optional[str] = None, **detail: Any) -> None:
        """Record a mutating admin action (who did what to what)."""
        security = getattr(self.orchestrator, "security_manager", None)
        audit_logger = getattr(security, "audit_logger", None)
        if audit_logger is None:
            logger.warning("admin action {} by {} (no audit logger)", action, admin_id)
            return
        # record() stores the event itself (it used to only log, so this method appended it by hand).
        audit_logger.record(
            AuditEventType.ADMIN_ACTION, user_id=admin_id, capability=f"admin.{action}",
            allowed=True, reason=", ".join(f"{k}={v}" for k, v in detail.items())[:300],
            task_id=task_id, metadata={"action": action, **{k: str(v) for k, v in detail.items()}})

    # ------------------------------------------------------------ overview

    async def overview(self) -> OverviewResponse:
        counts = TaskCounts()
        for status in TaskStatus:
            setattr(counts, status.value, await self.tm.store.count(status=status))
        dm = self.orchestrator.device_manager
        devices = await dm.registry.all()
        connected = sum([1 for d in devices if await dm.is_connected(d.id)])
        executors = [ExecutorInfo(name=e.name, running=bool(e.running), kind=type(e).__name__)
                     for e in self.orchestrator.executor_manager.list()]
        v = self.voice
        voice = VoiceInfo(
            available=bool(v and v.available), stt=getattr(getattr(v, "stt", None), "name", None),
            tts=getattr(getattr(v, "tts", None), "name", None),
            active_sessions=v.active_sessions if v else 0,
            reason="" if (v and v.available) else (v.unavailable_reason if v else "voice is not configured"))
        extra: Optional[Dict[str, Any]] = None
        try:                                              # provided by the observability resources module
            from pai.observability import resources as _res          # type: ignore[attr-defined]
            collected = _res.collect()
            extra = collected.model_dump(mode="json") if hasattr(collected, "model_dump") else dict(collected)
        except Exception:
            extra = None
        return OverviewResponse(
            tasks=counts, active_tasks=counts.active, active_dags=self.dag.active_run_count(),
            dags_in_history=len(self.dag._history), devices=DeviceCounts(total=len(devices), connected=connected),
            executors=executors, voice=voice, process=_process_resources(), resources=extra,
            test_capabilities_enabled=self.config.test_capabilities, server_time=datetime.now(timezone.utc))

    # --------------------------------------------------------------- tasks

    async def _filtered(self, statuses: Sequence[Optional[TaskStatus]], *, user_id: Optional[str],
                        capability: Optional[str]) -> List[Task]:
        found: Dict[str, Task] = {}
        for status in statuses:
            for t in await self.tm.list(status=status, user_id=user_id):
                if capability and t.capability != capability:
                    continue
                found[t.id] = t
        return sorted(found.values(), key=lambda t: t.created_at, reverse=True)

    def _page(self, tasks: List[Task], offset: int, limit: int) -> TaskPage:
        limit = max(1, min(limit, self.config.max_active_page_size))
        offset = max(0, offset)
        return TaskPage(items=[AdminTask.from_task(t) for t in tasks[offset:offset + limit]],
                        total=len(tasks), offset=offset, limit=limit)

    async def active_tasks(self, *, offset: int = 0, limit: int = 100, user_id: Optional[str] = None,
                           capability: Optional[str] = None) -> TaskPage:
        return self._page(await self._filtered(_ACTIVE, user_id=user_id, capability=capability), offset, limit)

    async def list_tasks(self, *, status: Optional[TaskStatus] = None, offset: int = 0, limit: int = 100,
                         user_id: Optional[str] = None, capability: Optional[str] = None) -> TaskPage:
        return self._page(await self._filtered([status], user_id=user_id, capability=capability), offset, limit)

    async def get_task(self, task_id: str) -> Task:
        task = await self.tm.get(task_id)
        if task is None:
            raise AdminError(404, "Task not found")
        return task

    # ---------------------------------------------------------------- runs

    def _new_request_id(self) -> str:
        return "adm-" + uuid.uuid4().hex[:12]

    async def run_goal(self, admin_id: str, req: RunGoalRequest) -> RunAccepted:
        rid = self._new_request_id()
        self.audit(admin_id, "run_goal", request_id=rid, goal=req.goal[:80])

        async def _go() -> None:
            await self.orchestrator.run(
                user_id=admin_id, goal=req.goal, session_id=req.session_id, request_id=rid,
                context={"mode": "admin"})
        self._spawn(_go(), f"admin.goal.{rid}")
        return RunAccepted(request_id=rid, kind="goal")

    async def validate_specs(self, admin_id: str, nodes: Sequence[NodeSpec]) -> List[TaskSpec]:
        """Typed NodeSpecs -> TaskSpecs, verified with the same PlanVerifier real plans use."""
        def _deadline(n: NodeSpec) -> float:
            # One-shot tasks get a deadline; a "sleep 60" must not die at the 30 s default.
            if n.timeout:
                return n.timeout
            secs = n.parameters.get("seconds") if n.capability == "admin.test.sleep" else None
            return max(30.0, float(secs) + 15.0) if isinstance(secs, (int, float)) else 30.0

        specs = [TaskSpec(id=n.id, name=n.name or n.id, input=n.name or n.id, type=n.type,
                          capability=n.capability, parameters=dict(n.parameters),
                          depends_on=list(n.depends_on), timeout=_deadline(n),
                          # Test capabilities must fail/finish deterministically: no auto-retry backoff.
                          retryable=not n.capability.startswith("admin.test.")) for n in nodes]
        issues: List[str] = []
        verifier = getattr(getattr(self.orchestrator, "planner", None), "verifier", None)
        if verifier is not None:
            result = await verifier.verify(Plan(goal="admin dag", tasks=specs, context={"user_id": admin_id}))
            issues = list(result.get("issues") or [])
        if not issues:                       # belt and braces if the verifier is unavailable
            ids = {s.id for s in specs}
            issues += [f"Task {s.id} depends on unknown task {d}" for s in specs for d in s.depends_on if d not in ids]
            cycle = self.dag._detect_cycle({s.id: s for s in specs})
            if cycle:
                issues.append("Plan contains a cyclic dependency: " + " -> ".join(cycle))
        if issues:
            raise AdminError(422, "Invalid DAG: " + "; ".join(issues), issues)
        return specs

    async def run_dag(self, admin_id: str, req: RunDagRequest, *, kind: str = "dag") -> RunAccepted:
        specs = await self.validate_specs(admin_id, req.tasks)
        rid = self._new_request_id()
        ctx = await self.orchestrator.context_manager.create_context(
            user_id=admin_id, session_id=None, goal=req.label, metadata={"mode": "admin"})
        ctx.request_id = rid
        ctx.origin = Origin.USER_DIRECT              # an admin acting directly
        self.audit(admin_id, "run_dag", request_id=rid, kind=kind, tasks=len(specs))
        self._spawn(self.dag.execute(specs, ctx), f"admin.dag.{rid}")
        for _ in range(200):                         # wait (<=2 s) until the run is visible to GET /dags/{id}
            if await self.dag.snapshot(rid) is not None:
                break
            await asyncio.sleep(0.01)
        return RunAccepted(request_id=rid, kind=kind, task_count=len(specs))

    async def run_demo(self, admin_id: str, kind: DemoKind, scale: float = 1.0) -> RunAccepted:
        if not self.config.test_capabilities:
            raise AdminError(409, "Demo DAGs need the admin.test.* capabilities: set admin.test_capabilities: true "
                                  "(or PAI_ADMIN_TEST_CAPABILITIES=1) and restart.")
        return await self.run_dag(admin_id, RunDagRequest(tasks=build_demo(kind, scale), label=f"demo {kind.value}"),
                                  kind=f"demo:{kind.value}")

    # --------------------------------------------------------- capabilities

    async def capabilities(self, admin_id: str) -> CapabilityList:
        described: Dict[str, Dict[str, Any]] = {}
        router = getattr(self.orchestrator, "capability_router", None)
        if router is not None:
            try:
                described = await router.describe_capabilities_with_schema(admin_id) or {}
            except Exception as exc:
                logger.warning("capability listing failed: {}", exc)
        infos = [CapabilityInfo(id=cid, description=str((info or {}).get("description") or ""),
                                test=cid.startswith("admin.test."), parameters=(info or {}).get("parameters") or None)
                 for cid, info in sorted(described.items())]
        return CapabilityList(capabilities=infos, test_capabilities_enabled=self.config.test_capabilities, demos=DEMOS)
