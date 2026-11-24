"""Process and in-memory-collection sizes, for spotting leaks while the server runs.

``collect(app_context)`` returns a typed ``ResourceSnapshot`` and never raises: a probe that
fails (component missing / not started) simply reports ``None``. ``ResourceMonitor`` logs a
snapshot periodically and warns when RSS grows monotonically.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import threading
import time
from collections import deque
from typing import Any, Deque, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from pai.config.models.resources import ResourceMonitorConfig


class ProcessStats(BaseModel):
    rss_bytes: Optional[int] = None
    vms_bytes: Optional[int] = None
    threads: int
    open_fds: Optional[int] = None
    cpu_seconds: Optional[float] = None


class RuntimeStats(BaseModel):
    asyncio_tasks: Optional[int] = None
    gc_counts: List[int] = Field(default_factory=list)       # pending objects per generation
    gc_objects: int = 0                                       # objects tracked by the collector


class CollectionSizes(BaseModel):
    """Sizes of the long-lived in-process collections (None = component not available)."""
    task_store_tasks: Optional[int] = None
    tasks_running: Optional[int] = None
    task_pause_events: Optional[int] = None
    idempotency_entries: Optional[int] = None
    event_bus_subscriptions: Optional[int] = None
    event_bus_queue_depth: Optional[int] = None
    tracer_traces: Optional[int] = None
    quarantine_log: Optional[int] = None
    dag_runs_active: Optional[int] = None
    voice_sessions_active: Optional[int] = None
    short_term_records: Optional[int] = None
    chat_sessions_tracked: Optional[int] = None
    device_connections: Optional[int] = None
    device_requests_in_flight: Optional[int] = None


class ResourceSnapshot(BaseModel):
    taken_at: float
    process: ProcessStats
    runtime: RuntimeStats
    collections: CollectionSizes

    def summary_line(self) -> str:
        p, r, c = self.process, self.runtime, self.collections
        mb = f"{p.rss_bytes / 1048576:.0f}MB" if p.rss_bytes is not None else "?"
        return (f"rss={mb} threads={p.threads} fds={p.open_fds} tasks={r.asyncio_tasks} "
                f"gc_objs={r.gc_objects} | running_tasks={c.tasks_running} dag_runs={c.dag_runs_active} "
                f"bus_subs={c.event_bus_subscriptions} bus_q={c.event_bus_queue_depth} "
                f"idem={c.idempotency_entries} traces={c.tracer_traces} voice={c.voice_sessions_active} "
                f"short_term={c.short_term_records} sessions={c.chat_sessions_tracked}")


# --------------------------------------------------------------------- probes

def _safe(fn) -> Optional[int]:
    try:
        v = fn()
        return None if v is None else int(v)
    except Exception:
        return None


def _process_stats() -> ProcessStats:
    rss = vms = fds = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                elif line.startswith("VmSize:"):
                    vms = int(line.split()[1]) * 1024
    except OSError:                                   # not Linux: best effort via getrusage
        with contextlib.suppress(Exception):
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if os.uname().sysname == "Darwin" else 1024)
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        with contextlib.suppress(OSError):
            fds = len(os.listdir(fd_dir))
            break
    cpu = None
    with contextlib.suppress(Exception):
        t = os.times()
        cpu = t.user + t.system
    return ProcessStats(rss_bytes=rss, vms_bytes=vms, threads=threading.active_count(),
                        open_fds=fds, cpu_seconds=cpu)


def _runtime_stats() -> RuntimeStats:
    tasks = None
    with contextlib.suppress(RuntimeError):
        tasks = len(asyncio.all_tasks())
    return RuntimeStats(asyncio_tasks=tasks, gc_counts=list(gc.get_count()), gc_objects=len(gc.get_objects()))


def _collection_sizes(ctx: Any) -> CollectionSizes:
    orch = getattr(ctx, "orchestrator", None)
    tm = getattr(orch, "task_manager", None)
    bus = getattr(orch, "event_bus", None) or getattr(orch, "_event_bus", None)
    mm = getattr(orch, "memory_manager", None)
    dev_exec = None
    with contextlib.suppress(Exception):
        dev_exec = orch.executor_manager.get("device_executor")

    def tracer_traces():
        from pai.observability.tracer import get_tracer
        return len(get_tracer().store._events)

    voice = getattr(ctx, "voice", None)
    return CollectionSizes(
        task_store_tasks=_safe(lambda: len(tm.store._tasks)),            # in-memory store only
        tasks_running=_safe(lambda: len(tm._running_tasks)),
        task_pause_events=_safe(lambda: len(tm._pause_events)),
        idempotency_entries=_safe(lambda: len(orch.idempotency._entries)),
        event_bus_subscriptions=_safe(lambda: sum(len(v) for v in bus._subscriptions.values())),
        event_bus_queue_depth=_safe(lambda: bus._event_queue.qsize()),
        tracer_traces=_safe(tracer_traces),
        quarantine_log=_safe(lambda: len(mm.quarantine_log)),
        dag_runs_active=_safe(lambda: len(orch.dag_scheduler._runs)),
        voice_sessions_active=_safe(lambda: voice.active_sessions),
        short_term_records=_safe(lambda: len(mm.short_term._store)),
        chat_sessions_tracked=_safe(lambda: len(orch.context_manager._session_registry)),
        device_connections=_safe(lambda: len(orch.device_manager._connections)),
        device_requests_in_flight=_safe(lambda: len(dev_exec._pending) + len(dev_exec._in_flight)),
    )


def collect(app_context: Any = None) -> ResourceSnapshot:
    """Snapshot of the process and of the collections that could leak. Never raises."""
    return ResourceSnapshot(
        taken_at=time.time(),
        process=_process_stats(),
        runtime=_runtime_stats(),
        collections=_collection_sizes(app_context) if app_context is not None else CollectionSizes(),
    )


# -------------------------------------------------------------------- monitor

class ResourceMonitor:
    """Logs a snapshot every ``interval_seconds`` (debug level) and warns on steady RSS growth."""

    def __init__(self, app_context: Any, config: Optional[ResourceMonitorConfig] = None) -> None:
        self._ctx = app_context
        self.config = config or ResourceMonitorConfig()
        self._rss: Deque[int] = deque(maxlen=self.config.window)
        self._task: Optional[asyncio.Task] = None
        self.last: Optional[ResourceSnapshot] = None

    def sample(self) -> ResourceSnapshot:
        snap = self.last = collect(self._ctx)
        logger.debug("resources: {}", snap.summary_line())
        rss = snap.process.rss_bytes
        if rss is not None:
            self._rss.append(rss)
            if self._growing():
                grown = (self._rss[-1] - self._rss[0]) / 1048576
                logger.warning(
                    "memory: RSS rose on each of the last {} samples (+{:.0f} MB, now {:.0f} MB) — "
                    "possible leak. {}", len(self._rss), grown, rss / 1048576, snap.summary_line())
        return snap

    def _growing(self) -> bool:
        r = list(self._rss)
        return (len(r) == self.config.window
                and all(b > a for a, b in zip(r, r[1:]))
                and (r[-1] - r[0]) / 1048576 >= self.config.growth_warn_mb)

    async def start(self) -> None:
        if self.config.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="resource-monitor")

    async def _run(self) -> None:
        while True:
            self.sample()
            await asyncio.sleep(self.config.interval_seconds)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
