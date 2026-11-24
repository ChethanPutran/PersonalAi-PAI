"""What happens when a schedule is DUE: re-validate, check device + consent, run as SCHEDULED."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pai.common.enums.permission import Origin
from pai.scheduler import consent as consent_mod
from pai.security.audit.models import AuditEventType
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository
from support import DEV, USER


def past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=1)


async def make_row(session_factory, *, consent: bool = False, **kw) -> str:
    base = dict(id=str(uuid4()), user_id=USER, title="Morning photo", kind="interval", interval_seconds=300,
                capability="camera.snap", parameters={}, target_device_id=DEV, catch_up="once",
                enabled=True, next_fire_at=past(), timezone="UTC")
    base.update(kw)
    row = ScheduleModel(**base)
    if consent:
        consent_mod.grant(row, by_user_id=USER, days=30)
    async with session_factory() as s:
        s.add(row)
        await s.commit()
    return row.id


async def load(session_factory, sid) -> ScheduleModel:
    async with session_factory() as s:
        return await ScheduleRepository(s).get(sid)


async def tick(scheduler):
    await scheduler._tick()
    if scheduler._bg:
        await asyncio.gather(*scheduler._bg)           # the background runs


def audit(security, event_type, sid):
    return [e for e in security.audit_logger.repository.events
            if e.event_type == event_type and e.metadata.get("schedule_id") == sid]


# --------------------------------------------------------------- free actions just run

async def test_free_action_runs_as_scheduled_with_only_the_users_parameters(
        scheduler, tm, session_factory, security):
    sid = await make_row(session_factory, capability="lights.on", parameters={"level": 7})
    await tick(scheduler)

    (spec, ctx), = tm.runs
    assert spec.capability == "lights.on" and spec.parameters == {"level": 7}   # no title/body mixed in
    assert spec.preferred_device == DEV and spec.metadata["schedule_id"] == sid
    assert ctx.origin == Origin.SCHEDULED and ctx.pre_approved is False
    assert ctx.metadata == {"allowed_devices": [DEV]}
    assert ctx.request_id.startswith(f"schedule:{sid}:")                        # idempotent per firing
    row = await load(session_factory, sid)
    assert (row.last_run_status, row.last_task_id, row.last_run_error) == ("ok", "task-123", None)
    assert row.next_fire_at > datetime.now(timezone.utc).replace(tzinfo=None) or row.next_fire_at.tzinfo
    assert len(audit(security, AuditEventType.SCHEDULE_RUN, sid)) == 1


async def test_a_failed_run_is_recorded_as_failed(scheduler, tm, session_factory, security):
    tm.succeed = False
    sid = await make_row(session_factory, capability="lights.on", parameters={"level": 1})
    await tick(scheduler)
    row = await load(session_factory, sid)
    assert (row.last_run_status, row.last_run_error) == ("failed", "device said no")
    assert len(audit(security, AuditEventType.SCHEDULE_FAILED, sid)) == 1


# ------------------------------------------------ sensitive actions need standing consent

async def test_sensitive_action_without_consent_is_skipped_notified_audited_and_advanced(
        scheduler, tm, session_factory, security):
    sid = await make_row(session_factory, kind="one_shot", interval_seconds=None,
                         fire_at=past(), requires_consent=True)
    await tick(scheduler)

    assert tm.runs == []                                                       # NOT run
    row = await load(session_factory, sid)
    assert row.last_run_status == "skipped_no_consent" and "approval" in row.last_run_error
    assert row.enabled is False and row.next_fire_at is None                   # advanced (one-shot is done)
    assert len(audit(security, AuditEventType.SCHEDULE_SKIPPED, sid)) == 1
    (notice, kw), = tm.created                                                 # the user was told
    assert notice.capability == "system.notifications.show"
    assert "Morning photo" in notice.parameters["body"] and "needs your approval" in notice.parameters["body"]
    assert kw["metadata"]["schedule_id"] == sid and tm.submitted == ["notice-1"]


async def test_the_user_is_told_once_not_on_every_tick(scheduler, tm, session_factory):
    sid = await make_row(session_factory, requires_consent=True)
    await tick(scheduler)
    async with session_factory() as s:                        # due again
        await ScheduleRepository(s).mark_fired(sid, fired_at=past(), next_fire_at=past())
        await s.commit()
    await tick(scheduler)
    assert tm.runs == [] and len(tm.created) == 1


async def test_with_valid_consent_it_runs_pre_approved_as_scheduled(scheduler, tm, session_factory, security):
    sid = await make_row(session_factory, consent=True, requires_consent=True)
    await tick(scheduler)
    (spec, ctx), = tm.runs
    assert spec.capability == "camera.snap" and ctx.origin == Origin.SCHEDULED
    assert ctx.pre_approved is True                           # set by the scheduler, from the consent
    assert (await load(session_factory, sid)).last_run_status == "ok"
    assert tm.created == []                                   # no "needs approval" notice


async def test_confirmation_required_by_the_manifest_is_covered_by_consent_too(scheduler, tm, session_factory):
    await make_row(session_factory, capability="confirm.me", consent=True)
    await tick(scheduler)
    assert tm.runs and tm.runs[0][1].pre_approved is True


async def test_editing_what_was_approved_revokes_the_consent(scheduler, tm, session_factory):
    for field, value in (("parameters", {"lens": "wide"}), ("target_device_id", "dev-2"),
                         ("capability", "confirm.me")):
        sid = await make_row(session_factory, consent=True, requires_consent=True)
        row = await load(session_factory, sid)
        assert consent_mod.evaluate(row).active
        setattr(row, field, value)                                       # the edit
        assert consent_mod.evaluate(row).reason == consent_mod.ConsentReason.BINDING_CHANGED
        assert consent_mod.revoke_if_binding_changed(row) is True        # what the PATCH route does
        assert not consent_mod.evaluate(row).active and row.consent_binding_hash is None


async def test_a_consent_that_no_longer_matches_does_not_run(scheduler, tm, session_factory):
    sid = await make_row(session_factory, consent=True)
    async with session_factory() as s:                       # edited behind the API's back (no revoke)
        row = await ScheduleRepository(s).get(sid)
        row.parameters = {"lens": "tele"}
        await s.commit()
    await tick(scheduler)
    assert tm.runs == []
    assert "binding_changed" in (await load(session_factory, sid)).last_run_error


async def test_expired_consent_does_not_run(scheduler, tm, session_factory):
    sid = await make_row(session_factory)
    async with session_factory() as s:
        row = await ScheduleRepository(s).get(sid)
        consent_mod.grant(row, by_user_id=USER, days=1, now=datetime.now(timezone.utc) - timedelta(days=3))
        await s.commit()
    await tick(scheduler)
    assert tm.runs == []
    row = await load(session_factory, sid)
    assert row.last_run_status == "skipped_no_consent" and "expired" in row.last_run_error


# ------------------------------------------------------- approval never overrides a DENY

async def test_approved_never_overrides_a_deny_shell_stays_blocked_even_with_consent(
        scheduler, tm, session_factory, security):
    # A legacy/tampered row: shell.exec with a perfectly valid consent (creation would have refused it).
    sid = await make_row(session_factory, capability="shell.exec", consent=True)
    await tick(scheduler)
    assert tm.runs == []
    row = await load(session_factory, sid)
    assert row.last_run_status == "denied" and "blocked" in row.last_run_error.lower()
    assert len(audit(security, AuditEventType.SCHEDULE_DENIED, sid)) == 1


async def test_the_real_pipeline_denies_shell_even_when_approved(security):
    from pai.security.authorization.context import ScopeContext
    from support import MANIFEST
    d = await security.authorize(
        user_id=USER, plugin_id="p1", capability="shell.exec", device_id=DEV, plugin_manifest=MANIFEST,
        parameters={}, scope=ScopeContext(allowed_devices=[DEV]), origin=Origin.SCHEDULED, approved=True)
    assert d.effect.value == "deny"


# ----------------------------------------------------------------------- offline / routing

async def test_offline_device_is_never_run_and_follows_the_catch_up_policy(
        scheduler, tm, session_factory, devices, security):
    devices.online.clear()
    keep = await make_row(session_factory, capability="lights.on", parameters={"level": 1}, catch_up="once")
    drop = await make_row(session_factory, capability="lights.on", parameters={"level": 1}, catch_up="none")
    await tick(scheduler)
    assert tm.runs == []
    kept, dropped = await load(session_factory, keep), await load(session_factory, drop)
    assert kept.last_run_status == dropped.last_run_status == "device_offline"
    assert kept.next_fire_at is not None and kept.next_fire_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)
    assert dropped.next_fire_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)   # skipped ahead
    assert len(audit(security, AuditEventType.SCHEDULE_SKIPPED, keep)) == 1


async def test_server_actions_do_not_need_the_device_and_use_their_own_executor(
        scheduler, tm, session_factory, devices):
    devices.online.clear()
    await make_row(session_factory, capability="server.job", target_device_id="server")
    await tick(scheduler)
    assert len(tm.runs) == 1 and tm.runs[0][0].capability == "server.job"
    assert tm.runs[0][1].metadata == {"allowed_devices": ["server"]}


async def test_a_vanished_capability_fails_the_firing_cleanly(scheduler, tm, session_factory, router):
    sid = await make_row(session_factory, capability="lights.on", parameters={"level": 1})
    del router.index["lights.on"]
    await tick(scheduler)
    row = await load(session_factory, sid)
    assert tm.runs == [] and row.last_run_status == "failed" and "no longer available" in row.last_run_error


# ---------------------------------------------------------------- old reminders unchanged

async def test_classic_reminders_still_use_the_reminder_path_without_consent(scheduler, tm, session_factory):
    sid = await make_row(session_factory, capability="system.notifications.show", title="Call Mom",
                         body="at noon", parameters={"title": "Call Mom", "body": "at noon"})
    await tick(scheduler)
    assert tm.runs == []                                                    # not through the action path
    (spec, kw), = tm.created
    assert spec.capability == "system.notifications.show" and spec.name == "Call Mom"
    assert spec.parameters["body"] == "at noon" and kw["executor_id"] == "device_executor"
    assert (await load(session_factory, sid)).last_run_status == "ok"
