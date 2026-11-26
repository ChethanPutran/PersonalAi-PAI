"""Downtime handling: misfire grace window and catch_up="all" replay."""
from datetime import datetime, timedelta, timezone

from pai.scheduler.scheduler_service import MAX_REPLAY_PER_TICK
from pai.security.audit.models import AuditEventType
from test_firing import audit, load, make_row, tick

NOW = lambda: datetime.now(timezone.utc)                                    # noqa: E731
ago = lambda **kw: NOW() - timedelta(**kw)                                  # noqa: E731
utc = lambda dt: dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt   # noqa: E731

FREE = dict(capability="lights.on", parameters={"level": 1})


async def test_within_grace_runs_once_as_before(scheduler, tm, session_factory):
    sid = await make_row(session_factory, **FREE, next_fire_at=ago(minutes=5), misfire_grace_seconds=3600)
    await tick(scheduler)
    assert len(tm.runs) == 1
    row = await load(session_factory, sid)
    assert row.last_run_status == "ok" and utc(row.next_fire_at) > NOW()


async def test_late_beyond_grace_is_missed_not_run_and_recurring_resumes(
        scheduler, tm, session_factory, security):
    sid = await make_row(session_factory, **FREE, next_fire_at=ago(hours=5), misfire_grace_seconds=600)
    await tick(scheduler)
    row = await load(session_factory, sid)
    assert tm.runs == []
    assert row.last_run_status == "missed" and "grace 600s" in row.last_run_error
    assert row.enabled and utc(row.next_fire_at) > NOW()                    # back on cadence
    assert len(audit(security, AuditEventType.SCHEDULE_SKIPPED, sid)) == 1


async def test_missed_one_shot_is_disabled(scheduler, tm, session_factory):
    sid = await make_row(session_factory, **FREE, kind="one_shot", interval_seconds=None,
                         next_fire_at=ago(hours=2), misfire_grace_seconds=60)
    await tick(scheduler)
    row = await load(session_factory, sid)
    assert tm.runs == [] and row.last_run_status == "missed" and row.enabled is False


async def test_no_grace_means_no_limit(scheduler, tm, session_factory):
    await make_row(session_factory, **FREE, next_fire_at=ago(days=30), misfire_grace_seconds=None)
    await tick(scheduler)
    assert len(tm.runs) == 1


async def test_catch_up_once_runs_a_single_time_after_long_outage(scheduler, tm, session_factory):
    await make_row(session_factory, **FREE, interval_seconds=60, next_fire_at=ago(minutes=30), catch_up="once")
    await tick(scheduler)
    assert len(tm.runs) == 1


# ------------------------------------------------------------------ catch_up="all"

async def test_all_replays_every_missed_occurrence_oldest_first(scheduler, tm, session_factory):
    start = ago(minutes=5, seconds=30)                       # occurrences at +0,+1,..,+5 min
    sid = await make_row(session_factory, **FREE, interval_seconds=60, next_fire_at=start, catch_up="all")
    await tick(scheduler)
    assert len(tm.runs) == 6
    ids = [ctx.request_id for _, ctx in tm.runs]
    assert len(set(ids)) == 6                                # one idempotency key per occurrence
    assert ids == sorted(ids, key=lambda r: r.split(":", 2)[2])
    row = await load(session_factory, sid)
    assert utc(row.next_fire_at) > NOW() and row.last_run_status == "ok"


async def test_all_replay_is_idempotent_across_ticks(scheduler, tm, session_factory):
    await make_row(session_factory, **FREE, interval_seconds=60, next_fire_at=ago(minutes=3), catch_up="all")
    await tick(scheduler)
    n = len(tm.runs)
    await tick(scheduler)
    assert len(tm.runs) == n                                 # nothing replayed twice


async def test_all_only_replays_inside_the_grace_window(scheduler, tm, session_factory, security):
    # 60 s interval, down for 10 min, but only the last ~3 min are still worth running.
    sid = await make_row(session_factory, **FREE, interval_seconds=60, next_fire_at=ago(minutes=10),
                         catch_up="all", misfire_grace_seconds=185)
    await tick(scheduler)
    assert 3 <= len(tm.runs) <= 4
    row = await load(session_factory, sid)
    assert row.last_run_status == "ok"
    skipped = audit(security, AuditEventType.SCHEDULE_SKIPPED, sid)
    assert len(skipped) == 1 and "older than the 185s grace window" in skipped[0].reason


async def test_all_with_everything_stale_just_records_missed(scheduler, tm, session_factory):
    # 180 occurrences; the newest is 45 s old, so it is outside the 30 s window too
    sid = await make_row(session_factory, **FREE, interval_seconds=60,
                         next_fire_at=ago(seconds=45 + 60 * 179), catch_up="all", misfire_grace_seconds=30)
    await tick(scheduler)
    row = await load(session_factory, sid)
    assert tm.runs == [] and row.last_run_status == "missed" and utc(row.next_fire_at) > NOW()


async def test_all_caps_a_tick_and_finishes_on_the_next(scheduler, tm, session_factory):
    sid = await make_row(session_factory, **FREE, interval_seconds=10,
                         next_fire_at=ago(seconds=10 * (MAX_REPLAY_PER_TICK + 20)), catch_up="all")
    await tick(scheduler)
    assert len(tm.runs) == MAX_REPLAY_PER_TICK
    assert utc((await load(session_factory, sid)).next_fire_at) <= NOW()    # still due: progress is kept
    await tick(scheduler)
    assert MAX_REPLAY_PER_TICK < len(tm.runs) <= MAX_REPLAY_PER_TICK + 25
    assert utc((await load(session_factory, sid)).next_fire_at) > NOW()


async def test_all_replays_cron_occurrences(scheduler, tm, session_factory):
    start = (NOW() - timedelta(minutes=5)).replace(second=0, microsecond=0)
    await make_row(session_factory, **FREE, kind="cron", cron="* * * * *", interval_seconds=None,
                   next_fire_at=start, catch_up="all")
    await tick(scheduler)
    assert len(tm.runs) == 6                                  # start .. start+5 min


async def test_all_stops_replay_when_the_device_is_offline_and_keeps_the_rest(
        scheduler, tm, session_factory, devices):
    devices.online.clear()
    start = ago(minutes=3, seconds=30)
    sid = await make_row(session_factory, **FREE, interval_seconds=60, next_fire_at=start, catch_up="all")
    await tick(scheduler)
    row = await load(session_factory, sid)
    assert tm.runs == [] and row.last_run_status == "device_offline"
    assert abs((utc(row.next_fire_at) - start).total_seconds()) < 1         # nothing lost: retried next tick
    devices.online.add(row.target_device_id)
    await tick(scheduler)
    assert len(tm.runs) == 4


async def test_all_on_a_one_shot_behaves_like_once(scheduler, tm, session_factory):
    sid = await make_row(session_factory, **FREE, kind="one_shot", interval_seconds=None,
                         next_fire_at=ago(minutes=5), catch_up="all")
    await tick(scheduler)
    assert len(tm.runs) == 1 and (await load(session_factory, sid)).enabled is False


def test_plan_replay_survives_a_month_long_outage(scheduler):
    from pai.storage.models import ScheduleModel
    sched = ScheduleModel(id="x", user_id="u", title="t", kind="interval", interval_seconds=10,
                          next_fire_at=ago(days=30), catch_up="all", misfire_grace_seconds=100)
    stale, truncated, fresh = scheduler._plan_replay(sched, NOW())
    assert truncated and stale >= 10_000 and 9 <= len(fresh) <= 11
