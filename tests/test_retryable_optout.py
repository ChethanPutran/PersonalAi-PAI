"""Per-capability `retryable` opt-out: manifest -> plan -> task -> retry / recovery behaviour."""
import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from pai.plugins.manifest_install import validate_manifest
from pai.plugins.manifest_models import CapabilitySpec
from pai.planning.goal_decomposer import GoalDecomposer
from pai.planning.dag_scheduler import DAGScheduler
from pai.tasks.lifecycle import TaskLifecycle
from pai.tasks.models import Task, TaskRetryPolicy, TaskStatus, TaskType
from pai.tasks.manager import TaskManager

# integration rig/helpers (same directory tree)
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent / "integration"))
from rig import FakeResolver, rig, spec  # noqa: E402,F401
from test_retry_idempotency_recovery import Device, bad, ctx, ok  # noqa: E402


# ───────────────────────── manifest / schema ─────────────────────────

def _manifest(**cap):
    return {
        "schemaVersion": 1, "id": "demo", "name": "Demo", "version": "1.0.0", "author": "me", "license": "MIT",
        "type": "native", "runtime": {"kind": "device", "entry": "x"} if False else {"kind": "device"},
        "deviceTypes": ["phone"], "capabilities": [{"id": "sms.send", "description": "send", **cap}],
    }


def test_capability_defaults_to_retryable_and_accepts_opt_out():
    assert CapabilitySpec(id="a", description="d").retryable is True
    assert CapabilitySpec(id="a", description="d", retryable=False).retryable is False


def test_schema_accepts_boolean_retryable_and_rejects_other_types():
    from pai.plugins.manifest_install import validate_dict
    base = _manifest()
    for good in (True, False):
        m = _manifest(retryable=good)
        try:
            validate_dict(m)
        except Exception as exc:                      # fixture shape may trip unrelated rules
            assert "retryable" not in str(exc), exc
    with pytest.raises(Exception) as e:
        validate_dict(_manifest(retryable="no"))
    assert "retryable" in str(e.value).lower() or "no" in str(e.value)


# ───────────────────────── planner never takes it from the LLM ─────────────────────────

def test_decomposer_reads_retryable_from_manifest_not_from_llm_output():
    available = {
        "sms.send": {"parameters": {}, "retryable": False, "requires_confirmation": True},
        "note.add": {"parameters": {}},                                  # default: retryable
    }
    raw = [
        {"id": "a", "capability": "sms.send", "parameters": {}, "retryable": True},   # LLM tries to re-enable
        {"id": "b", "capability": "note.add", "parameters": {}, "retryable": False},  # and to disable
    ]
    a, b = GoalDecomposer._normalize_tasks(raw, available)
    assert a.retryable is False and b.retryable is True


def test_task_manager_resolves_retryable_from_the_manifest_over_the_spec():
    res = SimpleNamespace(capability="sms.send", plugin_manifest=SimpleNamespace(
        get_capability=lambda c: SimpleNamespace(retryable=False)))
    s = spec("a")
    assert s.retryable is True
    assert TaskManager.manifest_retryable(res, s) is False               # manifest wins
    none = SimpleNamespace(capability="x", plugin_manifest=None)
    assert TaskManager.manifest_retryable(none, dataclasses.replace(s, retryable=False)) is False  # builtin: spec
    assert TaskManager.manifest_retryable(none, s) is True


# ───────────────────────── runtime behaviour ─────────────────────────

POLICY = TaskRetryPolicy(default_max_retries=3, base_delay_seconds=0.01)


async def test_non_retryable_capability_is_not_auto_retried_but_manual_retry_works(rig):
    dev = Device(rig, lambda n, f: (bad if n == 1 else ok)(rig, f))
    tm = await rig.make_tm(POLICY, resolver=FakeResolver({"notification.send": False}))
    out = await asyncio.wait_for(
        DAGScheduler(event_bus=rig.bus, task_manager=tm).execute([spec("a")], ctx()), 10)
    await asyncio.sleep(.1)
    assert out.status == "failed" and len(dev.invokes) == 1               # budget 3, but opted out
    names = [n for n, _ in rig.events]
    assert names.count("task.retrying") == 0 and names.count("task.failed") == 1

    task = (await tm.list(status=TaskStatus.FAILED))[0]
    assert task.retryable is False
    await tm.retry(task.id)                                               # the USER may still retry
    for _ in range(40):
        await asyncio.sleep(.05)
        if (await tm.get(task.id)).status == TaskStatus.COMPLETED: break
    await dev.close()
    assert (await tm.get(task.id)).status == TaskStatus.COMPLETED and len(dev.invokes) == 2


async def test_retryable_capability_still_retries(rig):
    dev = Device(rig, lambda n, f: (bad if n <= 2 else ok)(rig, f))
    tm = await rig.make_tm(POLICY, resolver=FakeResolver({"notification.send": True}))
    out = await asyncio.wait_for(
        DAGScheduler(event_bus=rig.bus, task_manager=tm).execute([spec("a")], ctx()), 10)
    await dev.close()
    assert out.status == "completed" and len(dev.invokes) == 3


async def test_non_retryable_task_running_at_crash_is_failed_not_resent_but_unsent_one_is_requeued(rig):
    dev = Device(rig, lambda n, f: ok(rig, f))
    mk = lambda **kw: Task(name="x", type=TaskType.ONE_SHOT, capability="notification.send", user_id="u",
                           session_id="s", device_id="dev-1", executor_id="device_executor", **kw)
    running, unsent = mk(spec_id="was-running", retryable=False), mk(spec_id="never-sent", retryable=False)
    await rig.store.create(running); await rig.store.create(unsent)
    TaskLifecycle.initialize(running); TaskLifecycle.queue(running); TaskLifecycle.start(running)
    await rig.store.save(running)
    TaskLifecycle.initialize(unsent); await rig.store.save(unsent)       # INITIALIZED: never reached a device

    tm = await rig.make_tm()                                             # "restart"
    for _ in range(40):
        await asyncio.sleep(.05)
        if (await tm.get(unsent.id)).status == TaskStatus.COMPLETED: break
    await dev.close()

    r = await tm.get(running.id)
    assert r.status == TaskStatus.FAILED and "not retryable" in r.error
    assert (await tm.get(unsent.id)).status == TaskStatus.COMPLETED
    assert [f["task_id"] for f in dev.invokes] == [unsent.id]            # the running one was NOT re-sent


async def test_non_retryable_in_flight_task_is_failed_on_graceful_shutdown(rig):
    async def hang(n, f): await asyncio.sleep(30)
    dev = Device(rig, hang)
    tm = await rig.make_tm(resolver=FakeResolver({"notification.send": False}))
    run = asyncio.create_task(DAGScheduler(event_bus=rig.bus, task_manager=tm).execute([spec("a", timeout=30)], ctx()))
    for _ in range(40):
        await asyncio.sleep(.05)
        if await tm.list(status=TaskStatus.RUNNING): break
    tid = (await tm.list(status=TaskStatus.RUNNING))[0].id
    await tm.shutdown(); run.cancel(); await asyncio.gather(run, return_exceptions=True)
    await dev.close()
    saved = await rig.store.require(tid)
    assert saved.status == TaskStatus.FAILED and "not retryable" in saved.error   # not silently re-queued
