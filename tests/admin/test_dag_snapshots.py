"""DAGScheduler live snapshots / bounded history / kill_run (unit level, fake TaskManager)."""
import asyncio
from types import SimpleNamespace

import pytest

from pai.orchestration.execution_context import ExecutionContext
from pai.planning.dag_models import DagRunStatus, NodeStatus
from pai.planning.dag_scheduler import DAGScheduler
from pai.planning.models import TaskSpec
from pai.tasks.models import TaskResult


class Bus:
    async def publish(self, *a, **k): ...


class FakeTM:
    """Mimics TaskManager.run/cancel/store.list closely enough for the scheduler."""
    def __init__(self, durations=None, fail=()):
        self.durations, self.fail = durations or {}, set(fail)
        self.tasks = {}                       # task id -> SimpleNamespace
        self._gates = {}                      # task id -> asyncio.Event (set = cancelled)
        self.store = SimpleNamespace(list=self._list)
    def is_initialized(self): return True
    async def _list(self, *, user_id=None, session_id=None):
        return list(self.tasks.values())
    async def run(self, spec, context, wait=True):
        tid = f"rt-{context.request_id}-{spec.id}"
        t = SimpleNamespace(id=tid, idempotency_key=f"{context.request_id}:{spec.id}", terminal=False)
        self.tasks[tid] = t
        gate = self._gates[tid] = asyncio.Event()
        try:
            await asyncio.wait_for(gate.wait(), self.durations.get(spec.id, 0.05))
            t.terminal = True
            return TaskResult(task_id=tid, success=False, error="Task cancelled")
        except asyncio.TimeoutError:
            t.terminal = True
            ok = spec.id not in self.fail
            return TaskResult(task_id=tid, success=ok, error=None if ok else "boom")
    async def cancel(self, task_id, reason=None):
        self._gates[task_id].set()


def spec(i, deps=()):
    return TaskSpec(id=i, name=i, input=i, type="one_shot", capability="x.y", depends_on=list(deps))


def ctx(rid):
    c = ExecutionContext(user_id="u", session_id="s")
    c.request_id = rid
    return c


async def test_snapshot_tracks_node_states_counts_and_runtime_ids():
    tm = FakeTM({"a": 0.2, "b": 0.2})
    dag = DAGScheduler(event_bus=Bus(), task_manager=tm)
    run = asyncio.create_task(dag.execute([spec("a"), spec("b", ["a"]), spec("c", ["a"])], ctx("r1")))
    await asyncio.sleep(0.08)
    snap = await dag.snapshot("r1")
    assert snap.status == DagRunStatus.RUNNING
    st = {n.spec_id: n.status for n in snap.nodes}
    assert st == {"a": NodeStatus.RUNNING, "b": NodeStatus.PENDING, "c": NodeStatus.PENDING}
    assert snap.counts.running == 1 and snap.counts.pending == 2
    assert {(e.source, e.target) for e in snap.edges} == {("a", "b"), ("a", "c")}
    assert [n for n in snap.nodes if n.spec_id == "a"][0].runtime_task_id == "rt-r1-a"
    assert dag.active_run_count() == 1 and dag.owner_of("r1") == "u"
    await run
    done = await dag.snapshot("r1")
    assert done.status == DagRunStatus.COMPLETED and done.finished_at and done.counts.completed == 3
    assert dag.active_run_count() == 0
    n = {x.spec_id: x for x in done.nodes}
    assert n["b"].started_at >= n["a"].finished_at and n["a"].duration_ms >= 150


async def test_history_is_bounded_newest_first_and_live_runs_come_first():
    dag = DAGScheduler(event_bus=Bus(), task_manager=FakeTM(), history_size=3)
    for i in range(6):
        await dag.execute([spec("a")], ctx(f"r{i}"))
    ids = [s.request_id for s in await dag.list_runs()]
    assert ids == ["r5", "r4", "r3"]                                  # only the newest 3 kept
    assert await dag.snapshot("r0") is None and (await dag.snapshot("r5")) is not None
    live = asyncio.create_task(dag.execute([spec("slow")], ctx("live")))
    tm = dag.task_manager; tm.durations["slow"] = 0.3
    await asyncio.sleep(0.05)
    runs = await dag.list_runs()
    assert runs[0].request_id == "live" and runs[0].status == DagRunStatus.RUNNING
    assert [r.request_id for r in await dag.list_runs(active_only=True)] == ["live"]
    await live


async def test_failed_dependency_marks_failed_then_skipped_and_run_failed():
    dag = DAGScheduler(event_bus=Bus(), task_manager=FakeTM(fail={"a"}))
    await dag.execute([spec("a"), spec("b", ["a"]), spec("c")], ctx("rf"))
    s = await dag.snapshot("rf")
    assert {n.spec_id: n.status for n in s.nodes} == {"a": NodeStatus.FAILED, "b": NodeStatus.SKIPPED, "c": NodeStatus.COMPLETED}
    assert s.status == DagRunStatus.FAILED
    assert [n for n in s.nodes if n.spec_id == "a"][0].error == "boom"


async def test_kill_run_cancels_pending_first_then_running_and_marks_run_cancelled():
    tm = FakeTM({"a": 30, "b": 30})
    dag = DAGScheduler(event_bus=Bus(), task_manager=tm)
    run = asyncio.create_task(dag.execute([spec("a"), spec("b"), spec("c", ["a"]), spec("d", ["b", "c"])], ctx("rk")))
    await asyncio.sleep(0.1)
    res = await dag.kill_run("rk")
    assert (res.cancelled_running, res.cancelled_pending) == (2, 2)
    out = await asyncio.wait_for(run, 3)
    s = await dag.snapshot("rk")
    assert s.status == DagRunStatus.CANCELLED
    assert {n.status for n in s.nodes} <= {NodeStatus.CANCELLED, NodeStatus.SKIPPED}
    assert await dag.kill_run("rk") is None                            # no longer live


async def test_two_live_runs_with_the_same_spec_ids_cannot_kill_each_other():
    tm = FakeTM({"a": 30})
    dag = DAGScheduler(event_bus=Bus(), task_manager=tm)
    r1 = asyncio.create_task(dag.execute([spec("a")], ctx("run-1")))
    r2 = asyncio.create_task(dag.execute([spec("a")], ctx("run-2")))
    await asyncio.sleep(0.1)
    await dag.cancel("run-1", "a")
    await asyncio.wait_for(r1, 3)
    s2 = await dag.snapshot("run-2")
    assert s2.status == DagRunStatus.RUNNING and s2.nodes[0].status == NodeStatus.RUNNING      # untouched
    await dag.kill_run("run-2"); await asyncio.wait_for(r2, 3)


async def test_list_runs_survives_a_run_finishing_while_it_awaits():
    """Regression: 'dictionary changed size during iteration' found by the browser."""
    tm = FakeTM({"a": 30, "b": 30})
    dag = DAGScheduler(event_bus=Bus(), task_manager=tm)
    r1 = asyncio.create_task(dag.execute([spec("a")], ctx("x1")))
    r2 = asyncio.create_task(dag.execute([spec("b")], ctx("x2")))
    await asyncio.sleep(0.05)
    orig = dag._runtime_ids

    async def slow(run):                          # yields, so a run can finish/pop mid-iteration
        await asyncio.sleep(0.05)
        return await orig(run)
    dag._runtime_ids = slow
    lister = asyncio.create_task(dag.list_runs())
    await asyncio.sleep(0.01)
    await dag.kill_run("x1")                      # finishes (and removes) x1 while list_runs is iterating
    runs = await asyncio.wait_for(lister, 3)
    assert {r.request_id for r in runs} >= {"x2"}
    await dag.kill_run("x2"); await asyncio.gather(r1, r2)


async def test_history_never_holds_a_running_snapshot_even_if_the_run_is_cancelled():
    tm = FakeTM({"a": 30})
    dag = DAGScheduler(event_bus=Bus(), task_manager=tm)
    run = asyncio.create_task(dag.execute([spec("a")], ctx("c1")))
    await asyncio.sleep(0.05)
    run.cancel()                                           # e.g. server shutdown
    with pytest.raises(asyncio.CancelledError):
        await run
    s = await dag.snapshot("c1")
    assert s is not None and s.status != DagRunStatus.RUNNING and dag.active_run_count() == 0
