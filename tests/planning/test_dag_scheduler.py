"""DAGScheduler: parallel independents, sequential dependents, failures, events."""
import asyncio
import time
import uuid

import pytest

from pai.event_bus.events import EventType
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.dag_scheduler import DAGScheduler
from pai.planning.models import TaskSpec
from pai.tasks.models import TaskResult


class FakeBus:
    def __init__(self): self.events = []
    async def publish(self, event_type, data=None, *, source=None, distributed=True, **kw):
        self.events.append((getattr(event_type, "value", event_type), data or {}))


class FakeTaskManager:
    """Mimics TaskManager.run: returns a TaskResult keyed by a *runtime* uuid, not the spec id."""
    def __init__(self, durations=None, fail=(), raise_=()):
        self.durations, self.fail, self.raise_ = durations or {}, set(fail), set(raise_)
        self.log = []           # (event, spec_id, t)
        self.runs = []
    def is_initialized(self): return True
    async def run(self, spec, context, wait=True):
        self.runs.append(spec.id)
        self.log.append(("start", spec.id, time.perf_counter()))
        await asyncio.sleep(self.durations.get(spec.id, 0.05))
        self.log.append(("end", spec.id, time.perf_counter()))
        if spec.id in self.raise_:
            raise PermissionError("denied")
        if not wait:
            return None
        return TaskResult(task_id=str(uuid.uuid4()), success=spec.id not in self.fail,
                          error="boom" if spec.id in self.fail else None)


def _spec(i, deps=()):
    return TaskSpec(id=i, name=i, input=i, type="one_shot", capability="chat.reply", depends_on=list(deps))


def _run(specs, tm, max_concurrency=4, wait=True):
    bus = FakeBus()
    dag = DAGScheduler(event_bus=bus, task_manager=tm, max_concurrency=max_concurrency)
    ctx = ExecutionContext(user_id="u", session_id="s")
    async def go():
        out = await asyncio.wait_for(dag.execute(specs, ctx, wait=wait), timeout=5)
        await asyncio.sleep(0.05)   # let fire-and-forget emits land
        return out
    return asyncio.run(go()), bus, tm


def _t(tm, ev, i): return next(t for e, s, t in tm.log if e == ev and s == i)


def test_independent_tasks_run_in_parallel():
    out, _, tm = _run([_spec("a"), _spec("b"), _spec("c")], FakeTaskManager({"a": .3, "b": .3, "c": .3}))
    assert out.status == "completed"
    assert max(_t(tm, "end", x) for x in "abc") - min(_t(tm, "start", x) for x in "abc") < .5   # ~0.3 not 0.9


def test_dependents_run_after_dependencies_chain():
    out, _, tm = _run([_spec("a"), _spec("b", ["a"]), _spec("c", ["b"])], FakeTaskManager())
    assert out.status == "completed", out
    assert _t(tm, "end", "a") <= _t(tm, "start", "b") and _t(tm, "end", "b") <= _t(tm, "start", "c")


def test_dependent_starts_as_soon_as_its_own_deps_finish_not_after_slow_sibling():
    # a fast, slow independent, b depends only on a. b must not wait for 'slow'.
    out, _, tm = _run([_spec("a"), _spec("slow"), _spec("b", ["a"])],
                      FakeTaskManager({"a": .05, "slow": .6, "b": .05}))
    assert _t(tm, "start", "b") < _t(tm, "end", "slow")


def test_max_concurrency_respected():
    out, _, tm = _run([_spec(str(i)) for i in range(6)], FakeTaskManager({str(i): .1 for i in range(6)}), max_concurrency=2)
    live = peak = 0
    for e, _, _ in sorted(tm.log, key=lambda x: x[2]):
        live += 1 if e == "start" else -1
        peak = max(peak, live)
    assert peak <= 2


def test_failed_dependency_skips_dependents_but_runs_independents():
    out, bus, tm = _run([_spec("a"), _spec("b", ["a"]), _spec("c")], FakeTaskManager(fail={"a"}))
    assert out.status == "failed"
    assert "b" not in tm.runs and "c" in tm.runs
    assert any(getattr(r, "status", None) == "skipped" and r.task_id == "b" for r in out.results)


def test_exception_in_one_task_does_not_abort_dag():
    out, _, tm = _run([_spec("a"), _spec("b", ["a"]), _spec("c")], FakeTaskManager(raise_={"a"}))
    assert out.status == "failed" and "c" in tm.runs and "b" not in tm.runs


def test_wait_false_runs_each_task_once():
    out, _, tm = _run([_spec("a"), _spec("b")], FakeTaskManager(), wait=False)
    assert sorted(tm.runs) == ["a", "b"]


def test_cycle_rejected():
    with pytest.raises(RuntimeError):
        _run([_spec("a", ["b"]), _spec("b", ["a"])], FakeTaskManager())


def test_dag_events_emitted_with_session_for_filtering():
    out, bus, _ = _run([_spec("a"), _spec("b", ["a"])], FakeTaskManager())
    kinds = [k for k, _ in bus.events]
    assert kinds[0] == EventType.DAG_STARTED.value and kinds[-1] == EventType.DAG_COMPLETED.value
    assert all(d.get("session_id") == "s" for _, d in bus.events), bus.events
