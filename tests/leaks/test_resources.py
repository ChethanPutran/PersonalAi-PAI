"""observability/resources.py: snapshots never raise; the monitor warns on steady RSS growth."""
import asyncio
from types import SimpleNamespace

import pytest

from pai.config.models.resources import ResourceMonitorConfig
from pai.observability import resources
from pai.observability.resources import ResourceMonitor, ResourceSnapshot, collect


def test_collect_without_a_context_still_reports_the_process():
    snap = collect()
    assert isinstance(snap, ResourceSnapshot)
    assert snap.process.rss_bytes and snap.process.rss_bytes > 10_000_000
    assert snap.process.threads >= 1 and snap.process.open_fds and snap.process.open_fds > 2
    assert snap.runtime.gc_objects > 0 and len(snap.runtime.gc_counts) == 3
    assert snap.collections.tasks_running is None            # nothing to look at: None, not an error
    assert "rss=" in snap.summary_line()


def test_collect_reads_the_real_collections_and_survives_missing_parts():
    class Obj(SimpleNamespace):
        pass
    orch = Obj(
        task_manager=Obj(store=Obj(_tasks={"a": 1, "b": 2}), _running_tasks={"x": 1}, _pause_events={}),
        idempotency=Obj(_entries={1: 1, 2: 2, 3: 3}),
        event_bus=Obj(_subscriptions={"a.*": {"1": 1}, "b.*": {"2": 2, "3": 3}}, _event_queue=asyncio.Queue()),
        dag_scheduler=Obj(_runs={"r": 1}),
        memory_manager=Obj(quarantine_log=[1, 2], short_term=Obj(_store=[1, 2, 3, 4])),
        context_manager=Obj(_session_registry={"s": 1}),
        device_manager=Obj(_connections={"d": 1}),
        executor_manager=Obj(get=lambda name: Obj(_pending={"p": 1}, _in_flight={"t": 1})),
    )
    c = collect(Obj(orchestrator=orch, voice=Obj(active_sessions=2))).collections
    assert (c.task_store_tasks, c.tasks_running, c.task_pause_events) == (2, 1, 0)
    assert (c.idempotency_entries, c.event_bus_subscriptions, c.event_bus_queue_depth) == (3, 3, 0)
    assert (c.dag_runs_active, c.quarantine_log, c.short_term_records) == (1, 2, 4)
    assert (c.chat_sessions_tracked, c.device_connections, c.device_requests_in_flight) == (1, 1, 2)
    assert c.voice_sessions_active == 2
    # a half-built context must never make collect() raise
    collect(Obj(orchestrator=Obj()))
    collect(object())


def _monitor(rss_values, **cfg):
    config = ResourceMonitorConfig(enabled=True, interval_seconds=1.0, **cfg)
    m = ResourceMonitor(None, config)
    it = iter(rss_values)

    def fake_collect(_ctx):
        return ResourceSnapshot(
            taken_at=0.0,
            process=resources.ProcessStats(rss_bytes=next(it) * 1048576, threads=1),
            runtime=resources.RuntimeStats(), collections=resources.CollectionSizes())
    return m, fake_collect


def test_monitor_warns_only_on_steady_and_large_growth(monkeypatch):
    from loguru import logger
    warnings = []
    sink = logger.add(lambda msg: warnings.append(msg.record["message"]), level="WARNING")
    try:
        # steady growth past the threshold -> warns
        m, fake = _monitor([100, 150, 200, 260, 300, 400], growth_warn_mb=100, window=6)
        monkeypatch.setattr(resources, "collect", fake)
        for _ in range(6):
            m.sample()
        assert any("possible leak" in w for w in warnings)

        # noisy (not monotonic) -> silent, even though it ends higher
        warnings.clear()
        m, fake = _monitor([100, 300, 200, 400, 250, 500], growth_warn_mb=100, window=6)
        monkeypatch.setattr(resources, "collect", fake)
        for _ in range(6):
            m.sample()
        assert not warnings

        # monotonic but tiny -> silent
        m, fake = _monitor([100, 101, 102, 103, 104, 105], growth_warn_mb=100, window=6)
        monkeypatch.setattr(resources, "collect", fake)
        for _ in range(6):
            m.sample()
        assert not warnings
    finally:
        logger.remove(sink)


async def test_monitor_is_off_by_default_and_stops_cleanly():
    assert ResourceMonitorConfig().enabled is False
    off = ResourceMonitor(None, ResourceMonitorConfig())
    await off.start()
    assert off._task is None
    on = ResourceMonitor(None, ResourceMonitorConfig(enabled=True, interval_seconds=1.0))
    await on.start()
    await asyncio.sleep(0.05)
    assert on.last is not None                                # it sampled at least once
    await on.stop()
    assert on._task is None
    await on.stop()                                           # idempotent
