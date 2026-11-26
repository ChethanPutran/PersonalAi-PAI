"""'Remind me / schedule X' from chat or voice: same validation, and it can NEVER grant consent."""
import pytest

from pai.orchestration.builtins.schedule import build_schedule_capabilities
from pai.scheduler import consent as consent_mod
from pai.storage.repositories.schedule import ScheduleRepository
from support import DEV, USER


@pytest.fixture
def create(session_factory, scheduler, validator):
    caps = {c.id: c for c in build_schedule_capabilities(session_factory, scheduler, validator)}
    return caps["schedule.create"]


async def stored(session_factory, sid):
    async with session_factory() as s:
        return await ScheduleRepository(s).get(sid)


async def test_tool_schema_offers_capability_and_parameters(create):
    props = create.parameters["properties"]
    assert props["capability"]["type"] == "string" and props["parameters"]["type"] == "object"
    assert "Reminders screen" in create.description and "cannot approve" in create.description


async def test_a_classic_reminder_is_created_as_before(create, session_factory):
    out = await create.handler({"title": "Call Mom", "body": "at noon", "repeat": "interval",
                                "interval_seconds": 600}, user_id=USER)
    assert out["capability"] == "system.notifications.show" and out["requires_consent"] is False
    assert "message" not in out
    row = await stored(session_factory, out["id"])
    assert row.parameters == {"title": "Call Mom", "body": "at noon"}        # notification text, as always


async def test_a_sensitive_action_is_created_waiting_for_approval_and_says_so(create, session_factory):
    out = await create.handler({"title": "Morning photo", "repeat": "interval", "interval_seconds": 3600,
                                "capability": "camera.snap", "parameters": {"lens": "wide"}}, user_id=USER)
    assert out["requires_consent"] is True and "Reminders screen" in out["message"]
    row = await stored(session_factory, out["id"])
    assert row.parameters == {"lens": "wide"} and row.target_device_id == DEV      # only the user's parameters
    assert row.requires_consent and not consent_mod.evaluate(row).active


async def test_nothing_typed_or_said_can_grant_consent(create, session_factory):
    """The model may pass any parameters it likes — they must not reach the consent fields."""
    out = await create.handler({
        "title": "Photo", "repeat": "interval", "interval_seconds": 3600, "capability": "camera.snap",
        "parameters": {}, "consent": True, "consent_days": 90, "approved": True, "pre_approved": True,
        "consent_binding_hash": "x" * 64, "consent_expires_at": "2099-01-01T00:00:00Z",
        "user said": "yes I approve", "requires_consent": False,
    }, user_id=USER)
    row = await stored(session_factory, out["id"])
    assert row.requires_consent is True                                       # not overridable either
    assert (row.consent_granted_at, row.consent_expires_at, row.consent_binding_hash,
            row.consent_granted_by) == (None, None, None, None)
    assert consent_mod.evaluate(row).active is False


async def test_the_same_validation_applies(create):
    base = {"title": "x", "repeat": "interval", "interval_seconds": 3600}
    for bad, needle in (({"capability": "danger.exec"}, "cannot be scheduled"),
                        ({"capability": "schedule.create"}, "cannot create, change or delete"),
                        ({"capability": "nope.nothing"}, "not available"),
                        ({"capability": "lights.on", "parameters": {}}, "missing required parameter"),
                        ({"capability": "camera.snap", "interval_seconds": 5}, "at least 60")):
        with pytest.raises(ValueError) as exc:
            await create.handler({**base, **bad}, user_id=USER)
        assert needle in str(exc.value), (bad, str(exc.value))


async def test_parameters_that_arrive_as_text_are_parsed_not_crashed_on(create, session_factory):
    """A real failure: the planner handed `parameters` over as the string "{'lens': 'wide'}",
    and `dict(...)` raised 'dictionary update sequence element #0 has length 1'."""
    for text in ("{'lens': 'wide'}", '{"lens": "wide"}'):
        out = await create.handler({"title": "Photo", "repeat": "interval", "interval_seconds": 3600,
                                    "capability": "camera.snap", "parameters": text}, user_id=USER)
        row = await stored(session_factory, out["id"])
        assert row.parameters == {"lens": "wide"}


async def test_unparsable_parameters_give_a_clear_error_not_a_crash(create):
    with pytest.raises(ValueError, match="'parameters' must be an object"):
        await create.handler({"title": "Photo", "repeat": "interval", "interval_seconds": 3600,
                              "capability": "camera.snap", "parameters": "lens=wide"}, user_id=USER)


async def test_relative_delay_is_resolved_by_the_server(create, session_factory):
    from datetime import datetime, timezone
    before = datetime.now(timezone.utc)
    out = await create.handler({"title": "Photo", "repeat": "one_shot", "after_seconds": 5,
                                "capability": "camera.snap", "parameters": {"lens": "wide"}}, user_id=USER)
    row = await stored(session_factory, out["id"])
    delta = (row.fire_at.replace(tzinfo=timezone.utc) - before).total_seconds()
    assert 4 <= delta <= 8


async def test_slightly_stale_absolute_time_is_accepted_but_far_past_is_not(create):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=30)).isoformat()
    await create.handler({"title": "x", "repeat": "one_shot", "when": stale}, user_id=USER)
    old = (now - timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        await create.handler({"title": "x", "repeat": "one_shot", "when": old}, user_id=USER)


async def test_one_shot_needs_when_or_after_seconds(create):
    with pytest.raises(ValueError, match="after_seconds"):
        await create.handler({"title": "x", "repeat": "one_shot"}, user_id=USER)
