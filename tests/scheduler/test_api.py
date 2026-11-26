"""The REST contract the Reminders screen codes against (real routes, real database)."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from pai.api.dependencies import get_current_user, get_db
from pai.api.routes import schedules
from pai.app_context import get_app_context
from pai.security.audit.models import AuditEventType
from pai.storage.models import ScheduleModel
from pai.storage.repositories.schedule import ScheduleRepository
from support import DEV, OTHER_USER, USER
from test_firing import make_row, tick


@pytest_asyncio.fixture
async def api(scheduler, session_factory):
    app = FastAPI()
    app.include_router(schedules.router, prefix="/api/v1")
    app.add_exception_handler(RequestValidationError, schedules.request_validation_handler)
    who = {"user": USER}

    async def db():
        async with session_factory() as s:
            yield s
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_app_context] = lambda: SimpleNamespace(
        orchestrator=SimpleNamespace(scheduler_service=scheduler, task_manager=scheduler.task_manager))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        client.as_user = lambda u: who.__setitem__("user", u)
        yield client


def body(**kw):
    base = dict(title="Morning photo", kind="interval", interval_seconds=3600, capability="camera.snap",
                parameters={})
    base.update(kw)
    return base


# ------------------------------------------------------------------------ capabilities

async def test_capabilities_endpoint_matches_the_contract(api):
    r = await api.get("/api/v1/schedules/capabilities")
    assert r.status_code == 200
    caps = {c["id"]: c for c in r.json()["capabilities"]}
    assert r.json()["capabilities"][0]["id"] == "system.notifications.show"
    assert set(caps["camera.snap"]) == {"id", "description", "plugin_id", "device_ids", "parameters_schema",
                                        "requires_consent", "risk", "schedulable", "blocked_reason"}
    assert caps["camera.snap"]["requires_consent"] is True and caps["camera.snap"]["risk"] == "high"
    assert caps["lights.on"]["parameters_schema"]["required"] == ["level"]
    assert caps["danger.exec"]["schedulable"] is False and caps["danger.exec"]["blocked_reason"]


# ----------------------------------------------------------------------- create / read

async def test_create_a_classic_reminder_is_unchanged(api):
    r = await api.post("/api/v1/schedules", json=dict(
        title="Call Mom", body="at noon", kind="interval", interval_seconds=600))
    assert r.status_code == 201
    out = r.json()
    assert out["capability"] == "system.notifications.show" and out["requires_consent"] is False
    assert out["consent"] == {"active": False, "granted_at": None, "expires_at": None}
    assert (out["last_run_status"], out["last_run_at"], out["last_task_id"], out["risk"]) == (None, None, None, "low")
    assert out["executor"] == "device_executor" and out["next_fire_at"]


async def test_create_a_sensitive_action_reports_that_it_needs_consent(api):
    out = (await api.post("/api/v1/schedules", json=body())).json()
    assert out["requires_consent"] is True and out["consent"]["active"] is False
    assert (out["risk"], out["executor"], out["target_device_id"], out["parameters"]) == ("high", "device_executor", DEV, {})
    assert (await api.get("/api/v1/schedules")).json()[0]["id"] == out["id"]


async def test_validation_errors_use_the_documented_422_shape(api):
    r = await api.post("/api/v1/schedules", json=body(capability="nope.nothing"))
    assert r.status_code == 422
    err, = r.json()["detail"]["errors"]
    assert err["field"] == "capability" and "not available" in err["message"]
    r = await api.post("/api/v1/schedules", json=body(capability="lights.on", parameters={}, interval_seconds=30))
    fields = {e["field"] for e in r.json()["detail"]["errors"]}
    assert fields == {"parameters", "interval_seconds"}                   # all problems at once
    r = await api.post("/api/v1/schedules", json={"title": "x"})          # malformed body: SAME shape
    assert r.status_code == 422 and {e["field"] for e in r.json()["detail"]["errors"]} >= {"kind"}


# --------------------------------------------------------------------------- consent

async def test_consent_lifecycle_owner_only_and_audited(api, scheduler, security):
    sid = (await api.post("/api/v1/schedules", json=body())).json()["id"]
    r = await api.post(f"/api/v1/schedules/{sid}/consent", json={"days": 7})
    assert r.status_code == 200
    c = r.json()["consent"]
    assert c["active"] is True
    assert abs((datetime.fromisoformat(c["expires_at"]) - datetime.fromisoformat(c["granted_at"])).days - 7) <= 1
    assert (await api.post(f"/api/v1/schedules/{sid}/consent", json={})).json()["consent"]["active"]   # default 30

    api.as_user(OTHER_USER)                                              # somebody else
    assert (await api.post(f"/api/v1/schedules/{sid}/consent", json={})).status_code == 403
    assert (await api.delete(f"/api/v1/schedules/{sid}/consent")).status_code == 403
    api.as_user(USER)

    r = await api.delete(f"/api/v1/schedules/{sid}/consent")
    assert r.status_code == 200 and r.json()["consent"]["active"] is False
    kinds = [e.event_type for e in security.audit_logger.repository.events if e.metadata.get("schedule_id") == sid]
    assert kinds.count(AuditEventType.SCHEDULE_CONSENT_GRANTED) == 2 and AuditEventType.SCHEDULE_CONSENT_REVOKED in kinds


async def test_consent_bounds_and_error_codes(api, session_factory):
    sid = (await api.post("/api/v1/schedules", json=body())).json()["id"]
    for days in (0, 91, -1):
        r = await api.post(f"/api/v1/schedules/{sid}/consent", json={"days": days})
        assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field"] == "days"
    reminder = (await api.post("/api/v1/schedules", json=dict(title="r", kind="interval", interval_seconds=60))).json()["id"]
    assert (await api.post(f"/api/v1/schedules/{reminder}/consent", json={})).status_code == 409
    assert (await api.post("/api/v1/schedules/missing/consent", json={})).status_code == 404

    # 422: the schedule can no longer be scheduled (e.g. its capability became blocked)
    async with session_factory() as s:
        row = await ScheduleRepository(s).get(sid)
        row.capability = "danger.exec"
        await s.commit()
    r = await api.post(f"/api/v1/schedules/{sid}/consent", json={})
    assert r.status_code == 422 and "cannot be scheduled" in r.json()["detail"]["errors"][0]["message"]


async def test_editing_what_was_approved_revokes_consent_but_cosmetic_edits_do_not(api):
    sid = (await api.post("/api/v1/schedules", json=body())).json()["id"]
    await api.post(f"/api/v1/schedules/{sid}/consent", json={})
    assert (await api.patch(f"/api/v1/schedules/{sid}", json={"title": "Renamed"})).json()["consent"]["active"]
    assert (await api.patch(f"/api/v1/schedules/{sid}", json={"catch_up": "none"})).json()["consent"]["active"]
    r = await api.patch(f"/api/v1/schedules/{sid}", json={"parameters": {"lens": "wide"}})
    assert r.status_code == 200 and r.json()["consent"]["active"] is False       # approval cancelled
    await api.post(f"/api/v1/schedules/{sid}/consent", json={})
    r = await api.patch(f"/api/v1/schedules/{sid}", json={"capability": "confirm.me", "parameters": {}})
    assert r.json()["capability"] == "confirm.me" and r.json()["consent"]["active"] is False


async def test_patch_validates_and_leaves_the_row_untouched_on_error(api):
    sid = (await api.post("/api/v1/schedules", json=body())).json()["id"]
    r = await api.patch(f"/api/v1/schedules/{sid}", json={"capability": "danger.exec", "parameters": {}})
    assert r.status_code == 422
    assert (await api.get("/api/v1/schedules")).json()[0]["capability"] == "camera.snap"


async def test_patch_recomputes_the_next_fire_time(api):
    created = (await api.post("/api/v1/schedules", json=body(interval_seconds=3600))).json()
    patched = (await api.patch(f"/api/v1/schedules/{created['id']}", json={"interval_seconds": 120})).json()
    gap = datetime.fromisoformat(patched["next_fire_at"]) - datetime.now(timezone.utc)
    assert gap < timedelta(minutes=3)                                        # was ~1 hour


async def test_other_users_cannot_edit_or_delete(api):
    sid = (await api.post("/api/v1/schedules", json=body())).json()["id"]
    api.as_user(OTHER_USER)
    assert (await api.patch(f"/api/v1/schedules/{sid}", json={"title": "x"})).status_code == 403
    assert (await api.delete(f"/api/v1/schedules/{sid}")).status_code == 403


# ---------------------------------------------------------------- last run is visible

async def test_last_run_fields_show_up_and_link_to_the_task(api, scheduler, tm, session_factory):
    await make_row(session_factory, capability="lights.on", parameters={"level": 2})
    await tick(scheduler)
    out = (await api.get("/api/v1/schedules")).json()[0]
    assert out["last_run_status"] == "ok" and out["last_task_id"] == "task-123" and out["last_run_at"]
    assert out["last_run_error"] is None


async def test_a_legacy_schedule_that_needs_approval_can_be_approved_and_is_flagged_after_it_fires(
        api, scheduler, session_factory, tm):
    """Stored before the flags existed (requires_consent=False) but the live pipeline says it needs approval."""
    sid = await make_row(session_factory, requires_consent=False)                # a legacy row
    await tick(scheduler)                                                        # fires: skipped, flags refreshed
    out = next(s for s in (await api.get("/api/v1/schedules")).json() if s["id"] == sid)
    assert out["requires_consent"] is True and out["last_run_status"] == "skipped_no_consent"
    assert out["risk"] == "high" and out["executor"] == "device_executor"

    sid2 = await make_row(session_factory, requires_consent=False)               # never fired yet
    r = await api.post(f"/api/v1/schedules/{sid2}/consent", json={})             # not a wrong 409
    assert r.status_code == 200 and r.json()["consent"]["active"] and r.json()["requires_consent"] is True


async def test_misfire_grace_roundtrips_and_can_be_cleared(api):
    r = await api.post("/api/v1/schedules", json={**body(), "catch_up": "all", "misfire_grace_seconds": 600})
    assert r.status_code == 201 or r.status_code == 200
    sid = r.json()["id"]
    assert r.json()["misfire_grace_seconds"] == 600 and r.json()["catch_up"] == "all"
    r = await api.patch(f"/api/v1/schedules/{sid}", json={"misfire_grace_seconds": 60})
    assert r.json()["misfire_grace_seconds"] == 60
    r = await api.patch(f"/api/v1/schedules/{sid}", json={"title": "x"})          # untouched
    assert r.json()["misfire_grace_seconds"] == 60
    r = await api.patch(f"/api/v1/schedules/{sid}", json={"misfire_grace_seconds": None})
    assert r.json()["misfire_grace_seconds"] is None                               # explicit null clears
    assert (await api.post("/api/v1/schedules", json={**body(), "misfire_grace_seconds": -1})).status_code == 422
