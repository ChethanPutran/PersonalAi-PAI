"""Trace store — ring buffer + JSONL persistence + live SSE subscribers."""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from pai.observability.events import TraceEvent, TraceSummary


# Cap the write queue so a slow disk can't grow memory unboundedly.
# 5000 events is ~2-3 seconds of typical traffic, more than enough to
# bridge any I/O hiccup while still bounding damage if the writer stalls.
_WRITE_QUEUE_MAX = 5000

# Subscribers (SSE connections) each get their own queue. 512 events
# is plenty for one admin UI; if it falls behind, we drop the oldest
# so the newest events are always shown.
_SUBSCRIBER_QUEUE_MAX = 512


class TraceStore:
    """Ring buffer + optional JSONL persistence + live subscribers."""

    def __init__(
        self,
        *,
        max_traces: int = 500,
        max_events_per_trace: int = 2000,
        persist_dir: Optional[str] = "data/traces",
        retention_days: int = 7,
    ) -> None:
        self.max_traces = max_traces
        self.max_events_per_trace = max_events_per_trace
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self.retention_days = retention_days

        self._events: "OrderedDict[str, deque[TraceEvent]]" = OrderedDict()
        self._summaries: Dict[str, TraceSummary] = {}
        self._subs: List[asyncio.Queue] = []

        self._write_queue: "asyncio.Queue[Optional[TraceEvent]]" = asyncio.Queue(
            maxsize=_WRITE_QUEUE_MAX,
        )
        self._writer_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)

    # -------------------- lifecycle --------------------

    async def start(self) -> None:
        """
        Rehydrate from disk and start the writer.

        Safe to call twice — the second call is a no-op if the writer
        is already running.
        """
        if self._writer_task is not None:
            logger.debug("TraceStore already started — ignoring")
            return

        # Reset the shutdown flag so a fresh start works after a stop()
        self._shutdown_event.clear()

        if self.persist_dir:
            await self._load_recent_files(days=self.retention_days)

        self._writer_task = asyncio.create_task(
            self._writer_loop(), name="trace-store-writer",
        )
        logger.info("TraceStore writer started → {}", self.persist_dir)

    async def stop(self) -> None:
        """
        Stop the writer and wake every SSE subscriber.

        Order matters:
          1. Set the shutdown flag so generators exit on their next tick
          2. Push sentinel `None` to every subscriber queue
          3. Signal the writer to flush and exit
          4. Wait for the writer with a bounded timeout

        Once this returns, no SSE generator should be waiting on a queue
        that never fires — uvicorn's graceful shutdown can proceed
        without hitting its force-close timeout.
        """
        # 1. Signal shutdown — checked by SSE generators on each loop tick
        self._shutdown_event.set()

        # 2. Wake every subscriber with a sentinel
        for q in list(self._subs):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                # If the subscriber is too slow, drop the oldest event
                # to make room for the sentinel — the sentinel is what
                # makes shutdown fast, so it must not be lost.
                try:
                    q.get_nowait()
                    q.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

        # 3. Signal the writer to flush and exit
        if self._writer_task is None:
            return

        try:
            self._write_queue.put_nowait(None)
        except asyncio.QueueFull:
            # Queue is full — drain one slot then force the sentinel in
            try:
                self._write_queue.get_nowait()
                self._write_queue.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

        # 4. Wait for the writer, bounded
        try:
            await asyncio.wait_for(self._writer_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("TraceStore writer did not shut down cleanly")
            self._writer_task.cancel()
            try:
                await self._writer_task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            self._writer_task = None

        logger.info("TraceStore shutdown complete")

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    # -------------------- rehydration --------------------

    async def _load_recent_files(self, days: int = 7) -> None:
        """Rehydrate in-memory buffer from JSONL files on disk."""
        if not self.persist_dir or not self.persist_dir.exists():
            return

        cutoff = time.time() - (days * 86400)
        loaded = 0

        day_dirs = sorted(
            (p for p in self.persist_dir.iterdir() if p.is_dir()),
            reverse=True,
        )

        for day_dir in day_dirs:
            files = sorted(
                day_dir.glob("*.jsonl"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            for jsonl_file in files:
                if loaded >= self.max_traces:
                    break
                try:
                    if jsonl_file.stat().st_mtime < cutoff:
                        continue

                    events: List[TraceEvent] = []
                    for line in jsonl_file.read_text(
                        encoding="utf-8",
                    ).splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            events.append(TraceEvent(**data))
                        except Exception:
                            continue

                    if not events:
                        continue

                    for ev in events:
                        self._replay(ev)
                    loaded += 1

                except Exception as exc:
                    logger.warning(
                        "Failed to load trace file {}: {}", jsonl_file, exc,
                    )

            if loaded >= self.max_traces:
                break

        logger.info(
            "TraceStore rehydrated {} trace(s) from disk ({} day window)",
            loaded, days,
        )

    def _replay(self, event: TraceEvent) -> None:
        """Insert a historical event without re-writing it to disk."""
        self._record_event(event, fan_out=False)

    # -------------------- append (hot path) --------------------

    def append(self, event: TraceEvent) -> None:
        """Record a live event — in memory, to subscribers, and to disk."""
        self._record_event(event, fan_out=True)

    def _record_event(self, event: TraceEvent, *, fan_out: bool) -> None:
        """
        Shared event-recording logic used by both `append` (live) and
        `_replay` (rehydration).

        Keeping this in one place means the summary-building logic
        can't drift between the two paths — a bug class that previously
        required fixing twice.
        """
        events = self._events.get(event.trace_id)
        if events is None:
            events = deque(maxlen=self.max_events_per_trace)
            self._events[event.trace_id] = events
            self._evict()

        events.append(event)
        self._update_summary(event)

        if not fan_out:
            return

        # Live fan-out to SSE subscribers
        dead: List[asyncio.Queue] = []
        for q in self._subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    dead.append(q)
        for q in dead:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

        # Persist — best effort, never blocks the caller
        if self._writer_task is not None:
            try:
                self._write_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _update_summary(self, event: TraceEvent) -> None:
        """Update the per-trace summary that backs the admin list view."""
        s = self._summaries.get(event.trace_id)
        if s is None:
            s = TraceSummary(
                trace_id=event.trace_id,
                name=(
                    event.name if event.kind == "trace.start"
                    else event.trace_id
                ),
                user_id=event.attributes.get("user_id"),
                started_at=event.timestamp,
                ended_at=None,
                duration_ms=None,
                status="running",
                event_count=0,
                error_count=0,
                has_llm=False,
                has_tool=False,
            )
            self._summaries[event.trace_id] = s

        s.event_count += 1
        if event.error:
            s.error_count += 1
        if event.kind == "llm.request":
            s.has_llm = True
        if event.kind == "tool.call":
            s.has_tool = True
        if event.kind == "trace.end":
            s.ended_at = event.timestamp
            s.duration_ms = event.duration_ms
            s.status = event.attributes.get("status", "completed")
            if s.status not in ("completed", "failed", "cancelled"):
                s.status = "completed"

    def _evict(self) -> None:
        while len(self._events) > self.max_traces:
            old, _ = self._events.popitem(last=False)
            self._summaries.pop(old, None)

    # -------------------- query --------------------

    def get_trace(self, trace_id: str) -> List[TraceEvent]:
        return list(self._events.get(trace_id, []))

    def get_summary(self, trace_id: str) -> Optional[TraceSummary]:
        return self._summaries.get(trace_id)

    def list_traces(
        self,
        *,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        has_error: Optional[bool] = None,
        has_llm: Optional[bool] = None,
        has_tool: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TraceSummary]:
        all_s = sorted(
            self._summaries.values(), key=lambda s: s.started_at, reverse=True,
        )
        if user_id:
            all_s = [s for s in all_s if s.user_id == user_id]
        if status:
            all_s = [s for s in all_s if s.status == status]
        if has_error is True:
            all_s = [s for s in all_s if s.error_count > 0]
        if has_error is False:
            all_s = [s for s in all_s if s.error_count == 0]
        if has_llm is not None:
            all_s = [s for s in all_s if s.has_llm == has_llm]
        if has_tool is not None:
            all_s = [s for s in all_s if s.has_tool == has_tool]
        return all_s[offset: offset + limit]

    # -------------------- subscribers --------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
        # If we're already shutting down, don't hand out a queue that
        # will never receive an event — the caller would block until
        # their timeout. Push the sentinel immediately.
        if self._shutdown_event.is_set():
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    # -------------------- persistence --------------------

    async def _writer_loop(self) -> None:
        buffer: Dict[str, List[TraceEvent]] = {}
        last_flush = time.perf_counter()
        FLUSH_INTERVAL = 2.0
        FLUSH_MAX = 200

        async def flush() -> None:
            nonlocal buffer
            if not buffer:
                return
            snapshot = buffer
            buffer = {}
            for tid, events in snapshot.items():
                try:
                    await self._persist(tid, events)
                except Exception as exc:
                    logger.warning(
                        "Failed to persist trace {}: {}", tid, exc,
                    )

        while True:
            try:
                item = await asyncio.wait_for(
                    self._write_queue.get(), timeout=FLUSH_INTERVAL,
                )
            except asyncio.TimeoutError:
                await flush()
                last_flush = time.perf_counter()
                continue

            # Sentinel → shutdown
            if item is None:
                await flush()
                return

            buffer.setdefault(item.trace_id, []).append(item)
            total = sum(len(v) for v in buffer.values())
            if total >= FLUSH_MAX or (
                time.perf_counter() - last_flush
            ) >= FLUSH_INTERVAL:
                await flush()
                last_flush = time.perf_counter()

    async def _persist(
        self, trace_id: str, events: List[TraceEvent],
    ) -> None:
        if not self.persist_dir:
            return
        day = time.strftime("%Y-%m-%d", time.gmtime(events[0].timestamp))
        day_dir = self.persist_dir / day
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{trace_id}.jsonl"

        def _write() -> None:
            with path.open("a", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev.to_dict(), default=str) + "\n")

        await asyncio.get_event_loop().run_in_executor(None, _write)