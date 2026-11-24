"""Admin API against the real app: auth, active tasks, DAG runs, kill, validation, events."""
import json
import threading
import time

import pytest

from admin_helpers import echo_node, sleep_node


def test_auth_required_and_non_admins_are_forbidden(harness):
    c = harness.client
    assert c.get("/api/v1/admin/overview").status_code == 401                       # no token
    r = c.get("/api/v1/admin/overview", headers=harness.user["h"])
    assert r.status_code == 403 and not harness.user["is_admin"]                    # logged in, not admin
    for method, path in [("get", "/tasks/active"), ("get", "/dags"), ("post", "/run/dag"),
                         ("post", "/dags/x/kill"), ("get", "/events"), ("post", "/test/tts")]:
        resp = getattr(c, method)("/api/v1/admin" + path, headers=harness.user["h"])
        assert resp.status_code == 403, (path, resp.status_code)
    assert harness.get("/overview").status_code == 200                              # admin ok


def test_overview_and_capabilities(harness):
    o = harness.get("/overview").json()
    assert set(o["tasks"]) >= {"running", "queued", "completed", "failed", "cancelled", "pending"}
    assert o["test_capabilities_enabled"] is True and o["process"]["rss_mb"] > 0
    assert {e["name"] for e in o["executors"]} >= {"builtin_executor", "device_executor"}
    caps = harness.get("/capabilities").json()
    assert {"admin.test.sleep", "admin.test.echo", "admin.test.fail", "admin.test.stream"} <= \
        {c["id"] for c in caps["capabilities"] if c["test"]}
    assert {d["kind"] for d in caps["demos"]} == {"fanout", "chain", "long_running", "failure_skip"}


def test_dag_runs_in_dependency_order_and_is_visible_live_then_in_history(harness):
    rid = harness.run_dag([sleep_node("a", 0.3), sleep_node("b", 0.3), echo_node("join", ["a", "b"])])
    live = harness.get(f"/dags/{rid}").json()
    assert live["request_id"] == rid and {n["spec_id"] for n in live["nodes"]} == {"a", "b", "join"}
    assert {(e["source"], e["target"]) for e in live["edges"]} == {("a", "join"), ("b", "join")}
    assert harness.get("/dags", params={"active_only": True}).json()["runs"][0]["request_id"] == rid
    done = harness.wait_dag(rid)
    assert done["status"] == "completed" and done["counts"]["completed"] == 3
    n = {x["spec_id"]: x for x in done["nodes"]}
    assert n["join"]["started_at"] >= max(n["a"]["finished_at"], n["b"]["finished_at"])   # joiner waited
    assert n["a"]["started_at"] < n["b"]["finished_at"]                                   # a,b overlapped
    assert all(x["runtime_task_id"] for x in done["nodes"])                               # linked to real tasks
    ids = [r["request_id"] for r in harness.get("/dags").json()["runs"]]
    assert rid in ids                                                                     # kept in history


def test_active_tasks_span_all_users_and_paginate(harness):
    from pai.orchestration.execution_context import ExecutionContext
    from pai.planning.models import TaskSpec
    from pai.common.enums.permission import Origin
    dag = harness.ctx.orchestrator.dag_scheduler

    async def other_users_run():
        ctx = ExecutionContext(user_id=harness.user["id"], session_id="user-session", origin=Origin.USER_DIRECT)
        ctx.request_id = "user-run-1"
        specs = [TaskSpec(id=f"u{i}", name="s", input="s", type="one_shot", capability="admin.test.sleep",
                          parameters={"seconds": 1.5}) for i in range(2)]
        return await dag.execute(specs, ctx)
    fut = harness.client.portal.start_task_soon(other_users_run)
    rid = harness.run_dag([sleep_node("mine", 1.5)])
    time.sleep(0.5)
    page = harness.get("/tasks/active").json()
    owners = {t["user_id"] for t in page["items"]}
    assert {harness.user["id"], harness.admin["id"]} <= owners                      # tasks of BOTH users
    assert page["total"] >= 3 and all(t["status"] in ("running", "queued", "initialized", "created", "paused", "retrying", "pending")
                                      for t in page["items"])
    only = harness.get("/tasks/active", params={"user_id": harness.user["id"]}).json()
    assert {t["user_id"] for t in only["items"]} == {harness.user["id"]}
    p1 = harness.get("/tasks/active", params={"limit": 1, "offset": 0}).json()
    p2 = harness.get("/tasks/active", params={"limit": 1, "offset": 1}).json()
    assert len(p1["items"]) == 1 and p1["items"][0]["id"] != p2["items"][0]["id"] and p1["total"] == page["total"]
    fut.result(timeout=10)
    harness.wait_dag(rid)


def test_kill_one_running_node_leaves_independents_and_skips_dependents(harness):
    rid = harness.run_dag([sleep_node("victim", 30), sleep_node("other", 0.6), echo_node("after_victim", ["victim"]),
                           echo_node("after_other", ["other"])])
    harness.wait_dag(rid, until=lambda s: {n["spec_id"]: n["status"] for n in s["nodes"]}["victim"] == "running")
    r = harness.post(f"/dags/{rid}/tasks/victim/kill")
    assert r.status_code == 200 and r.json()["outcome"] == "cancelled_running"
    done = harness.wait_dag(rid)
    st = {n["spec_id"]: n["status"] for n in done["nodes"]}
    assert st == {"victim": "cancelled", "after_victim": "skipped", "other": "completed", "after_other": "completed"}
    assert done["status"] == "failed"                       # something didn't complete, but it was not a whole-run kill
    assert harness.post(f"/dags/{rid}/tasks/victim/kill").status_code == 404      # run is over


def test_kill_a_pending_node_and_kill_by_runtime_task_id(harness):
    rid = harness.run_dag([sleep_node("slow", 30), sleep_node("later", 0.1, ["slow"]), sleep_node("x", 30)])
    snap = harness.wait_dag(rid, until=lambda s: s["counts"]["running"] == 2 and all(n["runtime_task_id"] for n in s["nodes"] if n["status"] == "running"))
    assert harness.post(f"/dags/{rid}/tasks/later/kill").json()["outcome"] == "cancelled_pending"
    task_id = {n["spec_id"]: n["runtime_task_id"] for n in snap["nodes"]}["slow"]
    r = harness.post(f"/tasks/{task_id}/kill")                                   # via the TASK endpoint
    assert r.status_code == 200 and r.json()["task"]["status"] == "cancelled"
    s = harness.wait_dag(rid, until=lambda s: s["counts"]["running"] == 1)
    assert {n["spec_id"]: n["status"] for n in s["nodes"]} == {"slow": "cancelled", "later": "cancelled", "x": "running"}
    harness.post(f"/dags/{rid}/kill")
    harness.wait_dag(rid)


def test_kill_whole_dag(harness):
    rid = harness.run_dag([sleep_node("a", 30), sleep_node("b", 30), sleep_node("c", 30, ["a"]), echo_node("d", ["b", "c"])])
    harness.wait_dag(rid, until=lambda s: s["counts"]["running"] == 2)
    r = harness.post(f"/dags/{rid}/kill").json()
    assert r["cancelled_running"] == 2 and r["cancelled_pending"] == 2
    done = harness.wait_dag(rid)
    assert done["status"] == "cancelled" and done["counts"]["cancelled"] >= 2
    assert all(n["status"] in ("cancelled", "skipped") for n in done["nodes"])
    assert harness.post(f"/dags/{rid}/kill").status_code == 404


@pytest.mark.parametrize("tasks,needle", [
    ([{"id": "a", "capability": "admin.test.echo", "depends_on": ["b"]},
      {"id": "b", "capability": "admin.test.echo", "depends_on": ["a"]}], "cycl"),
    ([{"id": "a", "capability": "admin.test.echo", "depends_on": ["ghost"]}], "unknown task ghost"),
    ([{"id": "a", "capability": "admin.test.echo", "depends_on": ["a"]}], "depends on itself"),
    ([{"id": "a", "capability": "no.such.capability"}], "no.such.capability"),
    ([{"id": "a", "capability": "admin.test.echo"}, {"id": "a", "capability": "admin.test.echo"}], "Duplicate"),
])
def test_run_dag_validation_returns_422_with_clear_issues(harness, tasks, needle):
    r = harness.post("/run/dag", json={"tasks": tasks})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert needle in " ".join(detail["issues"]) + detail["message"], detail


def test_run_dag_rejects_malformed_input(harness):
    assert harness.post("/run/dag", json={"tasks": []}).status_code == 422
    assert harness.post("/run/dag", json={"tasks": [{"id": "bad id!", "capability": "admin.test.echo"}]}).status_code == 422
    assert harness.post("/run/dag", json={"tasks": [{"id": "a", "capability": "admin.test.echo", "type": "bogus"}]}).status_code == 422


def test_demo_dags_complete_with_the_expected_shape(harness):
    expected = {
        "fanout": {"a": "completed", "b": "completed", "c": "completed", "join": "completed"},
        "chain": {"step1": "completed", "step2": "completed", "step3": "completed"},
        "failure_skip": {"ok": "completed", "boom": "failed", "blocked": "skipped", "independent": "completed"},
    }
    for kind, statuses in expected.items():
        rid = harness.post(f"/run/demo/{kind}", params={"scale": 0.05}).json()["request_id"]
        done = harness.wait_dag(rid)
        assert {n["spec_id"]: n["status"] for n in done["nodes"]} == statuses, kind
    boom = [n for n in done["nodes"] if n["spec_id"] == "boom"][0]
    assert "boom (on purpose)" in (boom["error"] or "")                          # the failure reason is visible
    assert harness.post("/run/demo/long_running", params={"scale": 0.01}).status_code == 202


def _collect_admin_events(harness, until, timeout=15.0):
    """Subscribe to the admin event stream INSIDE the app's loop (TestClient cannot stream SSE:
    it buffers the whole response), return a future of the events seen."""
    from pai.api.admin.events import AdminEventStream
    bus = harness.ctx.orchestrator.event_bus
    ready = threading.Event()

    async def collect():
        seen = []
        async with AdminEventStream(bus) as stream:
            ready.set()
            end = time.time() + timeout
            while time.time() < end:
                ev = await stream.next_or_none(0.2)
                if ev is not None:
                    seen.append(ev)
                    if until(seen):
                        break
        return seen
    fut = harness.client.portal.start_task_soon(collect)
    assert ready.wait(5)
    return fut


def test_stream_capability_emits_task_stream_events_for_all_sessions(harness):
    fut = _collect_admin_events(harness, lambda seen: any(e.event in ("dag.completed", "dag.failed") for e in seen))
    rid = harness.run_dag([{"id": "s", "capability": "admin.test.stream", "parameters": {"frames": 3, "interval_ms": 20},
                            "type": "stream"}])
    events = fut.result(timeout=20)
    names = [e.event for e in events]
    assert names.count("task.stream") == 5                                       # start + 3 frames + end
    assert {"dag.started", "task.created", "task.started", "task.completed", "dag.task_finished"} <= set(names)
    assert [e.data["kind"] for e in events if e.event == "task.stream"] == ["start", "frame", "frame", "frame", "end"]
    assert harness.wait_dag(rid)["status"] == "completed"


def test_admin_event_stream_unsubscribes_on_exit_and_never_grows_the_bus(harness):
    bus = harness.ctx.orchestrator.event_bus
    count = lambda: sum(len(v) for v in bus._subscriptions.values())            # noqa: E731
    before = count()
    _collect_admin_events(harness, lambda seen: True, timeout=0.3).result(timeout=5)
    time.sleep(0.2)
    assert count() == before


def test_admin_events_endpoint_is_admin_only(harness):
    assert harness.client.get("/api/v1/admin/events").status_code == 401
    assert harness.client.get("/api/v1/admin/events", headers=harness.user["h"]).status_code == 403


def test_failing_test_capability_reports_its_error_without_retrying(harness):
    rid = harness.run_dag([{"id": "f", "capability": "admin.test.fail", "parameters": {"after_seconds": 0.05, "message": "kaboom"}}])
    t0 = time.time()
    done = harness.wait_dag(rid, timeout=8)
    assert time.time() - t0 < 5                                                  # no 10 s retry backoff
    node = done["nodes"][0]
    assert node["status"] == "failed" and "kaboom" in node["error"]


def test_retry_pause_resume_endpoints_report_state_errors_cleanly(harness):
    rid = harness.run_dag([sleep_node("p", 30)])
    snap = harness.wait_dag(rid, until=lambda s: s["counts"]["running"] == 1 and s["nodes"][0]["runtime_task_id"])
    tid = snap["nodes"][0]["runtime_task_id"]
    assert harness.post(f"/tasks/{tid}/retry").status_code == 409                 # running tasks can't be retried
    assert harness.post(f"/tasks/{tid}/resume").status_code == 409                # not paused
    assert harness.post("/tasks/nope/kill").status_code == 404
    assert harness.client.get(f"/api/v1/system/tasks/{tid}", headers=harness.H).json()["status"] == "running"   # existing endpoint
    harness.post(f"/dags/{rid}/kill")
    harness.wait_dag(rid)


def test_every_mutating_action_is_audited_with_the_admin_id(harness):
    before = len(harness.audit_events())
    rid = harness.run_dag([sleep_node("z", 30)])
    harness.wait_dag(rid, until=lambda s: s["counts"]["running"] == 1)
    harness.post(f"/dags/{rid}/tasks/z/kill")
    harness.wait_dag(rid)
    harness.post("/run/demo/chain", params={"scale": 0.05})
    new = harness.audit_events()[before:]
    actions = [e.metadata["action"] for e in new]
    assert actions[:2] == ["run_dag", "dag_node_kill"] and "run_dag" in actions[2:]
    assert all(e.user_id == harness.admin["id"] for e in new)
    kill = [e for e in new if e.metadata["action"] == "dag_node_kill"][0]
    assert kill.metadata["request_id"] == rid and kill.metadata["spec_id"] == "z"
