"""Scheduler -> real TaskManager.run -> REAL authorization (Origin.SCHEDULED + token) -> device."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))   # the shared rig

from pai.common.enums.permission import Origin                                   # noqa: E402
from pai.scheduler.scheduler_service import SchedulerService                    # noqa: E402
from pai.security.audit.models import AuditEventType                            # noqa: E402
from pai.tasks.models import TaskSource, TaskStatus                             # noqa: E402
from rig import device_sends, fake_device, frame_dict, rig                      # noqa: E402,F401
from support import DEV, MANIFEST, USER                                          # noqa: E402
from test_firing import audit, load, make_row                                    # noqa: E402


class Resolver:
    def is_initialized(self): return True
    async def resolve(self, spec, user_id):
        return SimpleNamespace(capability=spec.capability, plugin_id="p1", plugin_manifest=MANIFEST, targets=[])


async def build(rig, security, session_factory, validator, devices):
    tm = await rig.make_tm(security=security, resolver=Resolver())
    svc = SchedulerService(session_factory=session_factory, task_manager=tm, executor=None,
                           device_manager=devices, validator=validator, audit_logger=security.audit_logger)
    frames = []

    async def device(frame):
        f = frame_dict(frame)
        if f["type"] == "invoke":
            frames.append(f)
            await device_sends(rig, type="result", request_id=f["request_id"], result={"photo": "ok"})
    stop = asyncio.Event()
    return tm, svc, frames, asyncio.create_task(fake_device(rig, device, stop)), stop


async def test_consented_action_runs_through_real_authorization_as_scheduled(
        rig, security, session_factory, validator, devices):
    tm, svc, frames, dev, stop = await build(rig, security, session_factory, validator, devices)
    sid = await make_row(session_factory, consent=True, requires_consent=True)
    await svc._tick()
    await asyncio.gather(*svc._bg)
    await asyncio.sleep(0.1)
    stop.set(); await dev

    assert [f["capability"] for f in frames] == ["camera.snap"]                 # it reached the device
    assert frames[0]["idempotency_key"]                                          # device can de-duplicate
    (task,) = await tm.list()
    assert task.status == TaskStatus.COMPLETED and task.source == TaskSource.SCHEDULED
    assert task.spec_id == f"schedule_{sid}" and task.metadata["schedule_id"] == sid
    assert task.authorization.origin == Origin.SCHEDULED
    assert task.authorization.tokens, "a signed, task-bound token was issued"
    security.verify_task_tokens(task)                                            # and it verifies
    row = await load(session_factory, sid)
    assert (row.last_run_status, row.last_task_id) == ("ok", task.id)
    assert audit(security, AuditEventType.SCHEDULE_RUN, sid)
    assert any(e.event_type == AuditEventType.AUTHORIZATION and e.capability == "camera.snap"
               for e in security.audit_logger.repository.events)               # real authz decision audited


async def test_without_consent_nothing_reaches_the_device(rig, security, session_factory, validator, devices):
    tm, svc, frames, dev, stop = await build(rig, security, session_factory, validator, devices)
    sid = await make_row(session_factory, requires_consent=True)                # no consent
    await svc._tick()
    await asyncio.sleep(0.2)
    stop.set(); await dev
    assert [f for f in frames if f["capability"] == "camera.snap"] == []         # the action never ran
    notice = [f for f in frames if f["capability"] == "system.notifications.show"]
    assert notice and "needs your approval" in notice[0]["parameters"]["body"]   # but the user was told
    assert not [t for t in await tm.list() if t.capability == "camera.snap"]    # no action task was created
    assert (await load(session_factory, sid)).last_run_status == "skipped_no_consent"


async def test_free_action_runs_without_consent_and_without_a_confirmation_pause(
        rig, security, session_factory, validator, devices):
    tm, svc, frames, dev, stop = await build(rig, security, session_factory, validator, devices)
    await make_row(session_factory, capability="lights.on", parameters={"level": 3})
    await svc._tick()
    await asyncio.gather(*svc._bg)
    stop.set(); await dev
    (task,) = await tm.list()
    assert task.status == TaskStatus.COMPLETED and not task.requires_confirmation
    assert frames[0]["parameters"] == {"level": 3}
