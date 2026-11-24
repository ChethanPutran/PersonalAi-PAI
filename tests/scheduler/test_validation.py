"""Creating/editing a schedule: every way it can be wrong is caught up front, with field errors."""
from datetime import datetime, timedelta, timezone

import pytest

from pai.scheduler.validation import ScheduleSpec, ScheduleValidationError
from support import DEV, OTHER_DEV, USER

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(hours=2)


def spec(**kw) -> ScheduleSpec:
    base = dict(title="t", kind="interval", interval_seconds=300, capability="lights.on",
                parameters={"level": 5})
    base.update(kw)
    return ScheduleSpec(**base)


async def errors_for(validator, **kw):
    with pytest.raises(ScheduleValidationError) as exc:
        await validator.validate(USER, spec(**kw), now=NOW)
    return {e.field: e.message for e in exc.value.errors}


# ---------------------------------------------------------------- the happy paths

async def test_free_action_validates_and_keeps_only_the_users_parameters(validator):
    v = await validator.validate(USER, spec(), now=NOW)
    assert (v.capability, v.target_device_id, v.executor) == ("lights.on", DEV, "device_executor")
    assert v.requires_consent is False and v.risk == "low"
    assert v.parameters == {"level": 5}                        # no title/body injected


@pytest.mark.parametrize("capability,params,consent,risk", [
    ("camera.snap", {}, True, "high"),            # control actions are classified high -> approval
    ("confirm.me", {}, True, "low"),              # manifest requireConfirmation -> approval
    ("lights.on", {"level": 1}, False, "low"),
])
async def test_requires_consent_comes_from_the_real_policy_pipeline(validator, capability, params, consent, risk):
    v = await validator.validate(USER, spec(capability=capability, parameters=params), now=NOW)
    assert v.requires_consent is consent and v.risk == risk


async def test_server_plugin_routes_to_the_server_executor_and_needs_no_device(validator, devices):
    devices.online.clear()                                     # no device online at all
    v = await validator.validate(USER, spec(capability="server.job", parameters={}), now=NOW)
    assert v.executor == "server_executor" and v.target_device_id == "server" and not v.requires_consent


async def test_the_classic_reminder_is_always_schedulable(validator, router):
    router.index.clear()                                       # even with no capabilities indexed
    v = await validator.validate(USER, spec(capability="system.notifications.show", parameters={},
                                            interval_seconds=30), now=NOW)
    assert v.requires_consent is False and v.executor == "device_executor"


# ------------------------------------------------------------------ capability rules

async def test_unknown_capability(validator):
    assert "not available" in (await errors_for(validator, capability="nope.nothing"))["capability"]


@pytest.mark.parametrize("capability", ["schedule.create", "schedule.delete", "task.cancel"])
async def test_a_schedule_cannot_manage_schedules_or_tasks(validator, capability):
    assert "cannot create, change or delete" in (await errors_for(validator, capability=capability))["capability"]


async def test_blocked_by_system_restrictions_is_not_schedulable_and_says_why(validator):
    msg = (await errors_for(validator, capability="shell.exec", parameters={}))["capability"]
    assert "cannot be scheduled" in msg and "blocked" in msg.lower()


async def test_denied_by_scope_is_not_schedulable(validator):
    msg = (await errors_for(validator, capability="files.read", parameters={}))["capability"]
    assert "cannot be scheduled" in msg                        # no filesystem roots configured


# -------------------------------------------------------------------------- device

async def test_a_device_that_does_not_provide_the_capability_is_rejected(validator):
    err = await errors_for(validator, target_device_id=OTHER_DEV)
    assert "does not provide" in err["target_device_id"]


async def test_without_a_device_a_connected_one_is_picked_and_none_online_is_an_error(validator, devices):
    assert (await validator.validate(USER, spec(), now=NOW)).target_device_id == DEV
    devices.online.clear()
    assert "no connected device" in (await errors_for(validator))["target_device_id"]


async def test_reminder_target_device_must_belong_to_the_user(validator):
    err = await errors_for(validator, capability="system.notifications.show", parameters={},
                           target_device_id="someone-elses")
    assert err["target_device_id"] == "unknown device"


# ------------------------------------------------------------------------ parameters

async def test_parameters_are_checked_against_the_capability_schema(validator):
    assert "missing required parameter 'level'" in (await errors_for(validator, parameters={}))["parameters"]
    assert "must be integer" in (await errors_for(validator, parameters={"level": "high"}))["parameters"]
    assert "not one of" in (await errors_for(validator, parameters={"level": 1, "mode": "neon"}))["parameters"]


async def test_oversized_or_non_json_parameters_are_rejected(validator):
    assert "too large" in (await errors_for(validator, parameters={"level": 1, "x": "a" * 20000}))["parameters"]
    assert "plain JSON" in (await errors_for(validator, parameters={"level": 1, "x": object()}))["parameters"]


async def test_all_problems_are_reported_together(validator):
    err = await errors_for(validator, parameters={}, interval_seconds=5, target_device_id=OTHER_DEV)
    assert {"parameters", "interval_seconds", "target_device_id"} <= set(err)


# ---------------------------------------------------------------------------- timing

async def test_tiny_interval_rules(validator):
    assert "at least 60" in (await errors_for(validator, interval_seconds=30))["interval_seconds"]
    assert (await validator.validate(USER, spec(interval_seconds=60), now=NOW)).capability == "lights.on"
    # reminders keep their lower floor of 10 s
    rem = dict(capability="system.notifications.show", parameters={})
    assert "at least 10" in (await errors_for(validator, interval_seconds=5, **rem))["interval_seconds"]
    await validator.validate(USER, spec(interval_seconds=10, **rem), now=NOW)


async def test_fire_at_must_be_in_the_future_and_naive_times_use_the_schedule_timezone(validator):
    past = dict(kind="one_shot", interval_seconds=None)
    assert "future" in (await errors_for(validator, fire_at=NOW - timedelta(minutes=1), **past))["fire_at"]
    v = await validator.validate(USER, spec(fire_at=datetime(2026, 11, 23, 20, 0), timezone="Asia/Kolkata",
                                            **past), now=NOW)
    assert v.fire_at == datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc)     # 20:00 IST


async def test_bad_cron_timezone_and_sub_minute_cron(validator):
    assert "valid cron" in (await errors_for(validator, kind="cron", cron="not a cron"))["cron"]
    assert "5-field" in (await errors_for(validator, kind="cron", cron="*/5 * * * * *"))["cron"]
    assert "unknown timezone" in (await errors_for(validator, timezone="Mars/Base"))["timezone"]
    ok = await validator.validate(USER, spec(kind="cron", cron="0 9 * * *", interval_seconds=None), now=NOW)
    assert ok.capability == "lights.on"


# --------------------------------------------------------------------------- catalog

async def test_capabilities_catalog(validator):
    cat = {c.id: c for c in await validator.catalog(USER)}
    assert next(iter(cat)) == "system.notifications.show"                      # always first
    assert cat["system.notifications.show"].requires_consent is False
    assert cat["lights.on"].schedulable and not cat["lights.on"].requires_consent
    assert cat["lights.on"].parameters_schema["required"] == ["level"] and cat["lights.on"].device_ids == [DEV]
    assert cat["camera.snap"].requires_consent and cat["camera.snap"].risk == "high"
    assert not cat["shell.exec"].schedulable and "blocked" in cat["shell.exec"].blocked_reason.lower()
    assert cat["shell.exec"].risk == "critical"
    assert not cat["files.read"].schedulable
    assert "schedule.create" not in cat                                         # management caps hidden
    assert cat["server.job"].device_ids == ["server"]
