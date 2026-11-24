"""Persistent admin history: events and finished DAG runs kept for the retention window."""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from admin_helpers import echo_node
from pai.api.admin.history import AdminHistory

DAY = 86400
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc).timestamp()


def hist(tmp_path, days=7, **kw) -> AdminHistory:
    return AdminHistory(tmp_path / "h", retention_days=days, **kw)


def dag(rid, finished_ts, status="completed"):
    iso = datetime.fromtimestamp(finished_ts, timezone.utc).isoformat()
    return {"request_id": rid, "user_id": "u", "status": status, "started_at": iso, "finished_at": iso,
            "nodes": [], "edges": [], "counts": {}}


# ── store ───────────────────────────────────────────────────────────────────

def test_events_round_trip_in_order_within_the_window(tmp_path):
    h = hist(tmp_path)
    for i, ts in enumerate([NOW - 3 * DAY, NOW - 2 * DAY, NOW - 60, NOW - 30]):
        h.append_event("task.started", {"id": f"t{i}"}, ts=ts)
    out = h.read_events(now=NOW)
    assert [e["data"]["id"] for e in out] == ["t0", "t1", "t2", "t3"] and out[0]["event"] == "task.started"
    assert [e["data"]["id"] for e in h.read_events(days=1, now=NOW)] == ["t2", "t3"]            # narrower window
    assert [e["data"]["id"] for e in h.read_events(limit=2, now=NOW)] == ["t2", "t3"]            # the newest `limit`
    assert [e["data"]["id"] for e in h.read_events(text="T1", now=NOW)] == ["t1"]
    assert [e["data"]["id"] for e in h.read_events(since=NOW - 100, until=NOW - 40, now=NOW)] == ["t2"]


def test_nothing_older_than_the_retention_is_returned_or_kept(tmp_path):
    h = hist(tmp_path, days=3)
    h.append_event("dag.started", {"id": "old"}, ts=NOW - 5 * DAY)
    h.append_event("dag.started", {"id": "edge"}, ts=NOW - 2.5 * DAY)
    h.append_event("dag.started", {"id": "new"}, ts=NOW - 10)
    h.append_dag(dag("old", NOW - 5 * DAY), ts=NOW - 5 * DAY)
    assert [e["data"]["id"] for e in h.read_events(days=30, now=NOW)] == ["edge", "new"]          # `days` is capped at retention
    assert h.read_dags(now=NOW) == []                                                              # the old DAG is outside
    removed = h.prune(now=NOW)
    assert removed == 2                                                                            # both day files of 5 days ago
    assert sorted(p.name for p in (tmp_path / "h").glob("*.jsonl")) == sorted(
        [f"events-{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%d}.jsonl" for t in {NOW - 2.5 * DAY, NOW - 10}])


def test_dag_runs_newest_first_one_per_request_and_lookup(tmp_path):
    h = hist(tmp_path)
    h.append_dag(dag("a", NOW - 2 * DAY), ts=NOW - 2 * DAY)
    h.append_dag(dag("b", NOW - DAY), ts=NOW - DAY)
    h.append_dag(dag("a", NOW - 2 * DAY, status="failed"), ts=NOW - 2 * DAY)                       # a later line for the same run wins
    runs = h.read_dags(now=NOW)
    assert [r["request_id"] for r in runs] == ["b", "a"] and runs[1]["status"] == "failed"
    assert h.get_dag("b", now=NOW)["request_id"] == "b" and h.get_dag("nope", now=NOW) is None
    assert [r["request_id"] for r in h.read_dags(limit=1, now=NOW)] == ["b"]


def test_oversized_event_payloads_keep_their_identity_but_not_the_bulk(tmp_path):
    h = hist(tmp_path, max_event_bytes=200)
    h.append_event("task.completed", {"id": "t1", "status": "completed", "capability": "x.y", "result": "z" * 5000}, ts=NOW)
    (ev,) = h.read_events(now=NOW + 1)
    assert ev["data"] == {"id": "t1", "status": "completed", "capability": "x.y", "_truncated": True}


def test_corrupt_lines_and_unserializable_data_do_not_break_history(tmp_path):
    h = hist(tmp_path)
    h.append_event("task.created", {"id": "ok", "obj": object()}, ts=NOW - 5)                     # str()-ified, not an error
    path = next((tmp_path / "h").glob("events-*.jsonl"))
    path.write_text(path.read_text() + "{broken\n" + json.dumps({"ts": NOW - 1, "event": "task.started", "data": {"id": "later"}}) + "\n")
    assert [e["data"]["id"] for e in h.read_events(now=NOW)] == ["ok", "later"]


def test_an_unwritable_directory_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    h = AdminHistory(blocker / "sub", retention_days=7)
    h.append_event("task.created", {"id": "x"})                                                    # must not raise
    h.append_dag(dag("a", time.time()))
    assert h.read_events() == [] and h.info()["writable"] is False


def test_info_and_empty_store(tmp_path):
    h = hist(tmp_path)
    assert h.read_events() == [] and h.read_dags() == [] and h.prune() == 0 and h.info()["days_on_disk"] == []
    h.append_event("a.b", {}, ts=NOW)
    assert h.info()["days_on_disk"] == ["2026-11-24"] and h.info()["retention_days"] == 7


# ── the running app ─────────────────────────────────────────────────────────

def test_events_and_runs_are_recorded_and_survive_the_in_memory_history(harness):
    c, H = harness.client, harness.H
    rid = harness.run_dag([echo_node("a"), echo_node("b", ["a"])], label="hist")
    harness.wait_dag(rid)

    ev = c.get("/api/v1/admin/events/history", headers=H, params={"limit": 500}).json()
    assert ev["enabled"] is True and ev["retention_days"] >= 1
    names = [e["event"] for e in ev["events"] if e["data"].get("request_id") == rid]
    assert "dag.started" in names and any(n.startswith("dag.") and n != "dag.started" for n in names)
    assert any(e["event"].startswith("task.") for e in ev["events"])
    assert [e["ts"] for e in ev["events"]] == sorted(e["ts"] for e in ev["events"])                # oldest first
    assert c.get("/api/v1/admin/events/history", headers=H, params={"text": rid}).json()["events"]
    assert c.get("/api/v1/admin/events/history", headers=H, params={"text": "zzz-nothing-zzz"}).json()["events"] == []

    # a "restart": the bounded in-memory history is gone, the persisted run is still listed and openable
    harness.ctx.orchestrator.dag_scheduler._history.clear()
    listed = c.get("/api/v1/admin/dags", headers=H).json()
    assert rid in [r["request_id"] for r in listed["runs"]] and listed["retention_days"] >= 1
    snap = c.get(f"/api/v1/admin/dags/{rid}", headers=H)
    assert snap.status_code == 200 and snap.json()["request_id"] == rid and len(snap.json()["nodes"]) == 2
    assert rid not in [r["request_id"] for r in c.get("/api/v1/admin/dags", headers=H, params={"active_only": True}).json()["runs"]]
    assert c.get("/api/v1/admin/dags", headers=H, params={"days": 0}).status_code == 422


def test_history_endpoints_are_admin_only(harness):
    c = harness.client
    for path in ("/api/v1/admin/events/history", "/api/v1/admin/history/info"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers=harness.user["h"]).status_code == 403
    info = c.get("/api/v1/admin/history/info", headers=harness.H).json()
    assert info["retention_days"] >= 1 and info["writable"] is True
