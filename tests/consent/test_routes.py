"""REST contract: confirm/deny, consents, tester (devices invoke), preflight, migration."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import inspect, text, update

from pai.api.dependencies import get_current_user, get_db
from pai.api.routes import consents, devices, tasks
from pai.app_context import get_app_context
from pai.orchestration.capability_router import CapabilityNotAvailable
from pai.security.audit.models import AuditEventType
from pai.storage.models import UserModel
from pai.tasks.models import TaskStatus
from consent_kit import DEVICE, OTHER, USER


class FakeRouter:
    async def resolve(self, *, capability, user_id, preferred_device_id=None):
        return [SimpleNamespace(device_id=DEVICE, plugin_id="p1")]


@pytest_asyncio.fixture
async def api(kit, db):
    app = FastAPI()
    for r in (tasks.router, consents.router, devices.router):
        app.include_router(r, prefix="/api/v1")
    who = {"u": USER}

    async def get_session():
        async with db.sessionmaker() as s:
            yield s
    app.dependency_overrides[get_current_user] = lambda: who["u"]
    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_app_context] = lambda: SimpleNamespace(orchestrator=SimpleNamespace(
        task_manager=kit.tm, security_manager=kit.sec, device_manager=kit.rig.dm,
        executor_manager=kit.rig.em, capability_router=FakeRouter()))
    async with db.sessionmaker() as s:
        for uid in (USER, OTHER):
            s.add(UserModel(id=uid, email=f"{uid}@x.io", hashed_password="x", is_admin=False))
        await s.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.as_user = lambda u: who.__setitem__("u", u)

        async def make_admin(flag=True):
            async with db.sessionmaker() as s:
                await s.execute(update(UserModel).where(UserModel.id == USER).values(is_admin=flag))
                await s.commit()
        c.make_admin = make_admin
        c.kit = kit
        yield c


async def wait_status(kit, task_id, want, timeout=3):
    for _ in range(int(timeout / 0.05)):
        t = await kit.tm.get(task_id)
        if t.status in want:
            return t
        await asyncio.sleep(0.05)
    return t


def invoke_body(capability="act.low", **kw):
    return {"capability": capability, "parameters": {"a": 1}, **kw}


# ───────────────────────────────────────── confirm / deny

async def test_confirm_route_contract_and_serialized_confirmation(api):
    kit = api.kit
    t = await kit.run("act.low", {"a": 1})
    got = (await api.get(f"/api/v1/tasks/{t.id}")).json() if False else None   # task GET lives elsewhere
    ser = __import__("pai.tasks.serialization", fromlist=["x"]).serialize_task(t)
    assert set(ser["confirmation"]) >= {"capability", "plugin_id", "device_id", "risk", "parameters_summary",
                                        "allowed_scopes", "can_bind_params", "default_binding", "reason"}
    r = await api.post(f"/api/v1/tasks/{t.id}/confirm",
                       json={"scope": "always", "binding": {"mode": "keys", "keys": ["a"]}, "days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == t.id and body["grant"]["scope"] == "always"
    assert body["grant"]["binding"] == {"mode": "keys", "keys": ["a"]}
    done = await wait_status(kit, t.id, {TaskStatus.COMPLETED})
    assert done.status == TaskStatus.COMPLETED and done.confirmation is None


async def test_confirm_empty_body_is_once(api):
    t = await api.kit.run("act.low", {"a": 1})
    r = await api.post(f"/api/v1/tasks/{t.id}/confirm")
    assert r.status_code == 200 and r.json()["grant"] is None
    assert (await api.get("/api/v1/consents")).json() == {"consents": []}


@pytest.mark.parametrize("body,status,code", [
    ({"scope": "always"}, 422, "scope_not_allowed"),                       # act.high: max session
    ({"scope": "session", "binding": {"mode": "keys", "keys": ["zz"]}}, 422, "binding_invalid"),
])
async def test_confirm_rejects_bad_requests(api, body, status, code):
    t = await api.kit.run("act.high", {"a": 1})
    r = await api.post(f"/api/v1/tasks/{t.id}/confirm", json=body)
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert (await api.kit.tm.get(t.id)).status == TaskStatus.PAUSED         # still waiting


async def test_confirm_state_ownership_and_unknown(api):
    kit = api.kit
    t = await kit.run("act.low", {"a": 1})
    api.as_user(OTHER)
    assert (await api.post(f"/api/v1/tasks/{t.id}/confirm")).status_code == 403
    assert (await api.post("/api/v1/tasks/nope/confirm")).status_code == 404
    api.as_user(USER)
    assert (await api.post(f"/api/v1/tasks/{t.id}/confirm")).status_code == 200
    await wait_status(kit, t.id, {TaskStatus.COMPLETED})
    again = await api.post(f"/api/v1/tasks/{t.id}/confirm")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "invalid_state"
    assert (await api.post(f"/api/v1/tasks/{t.id}/confirm", json={"scope": "bogus"})).status_code == 422


async def test_deny_cancels_without_grant(api):
    t = await api.kit.run("act.low", {"a": 1})
    r = await api.post(f"/api/v1/tasks/{t.id}/deny")
    assert r.status_code == 200
    assert (await wait_status(api.kit, t.id, {TaskStatus.CANCELLED})).status == TaskStatus.CANCELLED
    assert (await api.get("/api/v1/consents")).json()["consents"] == []
    assert not api.kit.invokes


# ───────────────────────────────────────── consents

async def test_consents_list_and_revoke(api):
    kit = api.kit
    for cap in ("act.low", "act.med"):
        t = await kit.run(cap, {"a": 1})
        assert (await api.post(f"/api/v1/tasks/{t.id}/confirm", json={"scope": "always"})).status_code == 200
        await wait_status(kit, t.id, {TaskStatus.COMPLETED})
    listing = (await api.get("/api/v1/consents")).json()["consents"]
    assert len(listing) == 2
    g = next(x for x in listing if x["capability"] == "act.low")
    assert set(g) >= {"id", "scope", "plugin_id", "capability", "device_id", "device_name", "binding",
                      "params_preview", "session_id", "created_at", "expires_at", "last_used_at",
                      "use_count", "risk"}
    assert g["device_name"] == "phone" and g["binding"]["mode"] == "exact"
    assert g["params_preview"] == [{"name": "a", "value_preview": "1"}]

    api.as_user(OTHER)
    assert (await api.get("/api/v1/consents")).json()["consents"] == []
    assert (await api.delete(f"/api/v1/consents/{g['id']}")).status_code == 404     # owner only
    api.as_user(USER)
    assert (await api.delete(f"/api/v1/consents/{g['id']}")).status_code == 200
    assert (await api.delete(f"/api/v1/consents/{g['id']}")).status_code == 404
    assert (await api.delete("/api/v1/consents")).status_code == 422                 # needs a filter
    r = await api.delete("/api/v1/consents", params={"capability": "act.med", "device_id": DEVICE})
    assert r.json() == {"revoked": 1}
    assert (await api.get("/api/v1/consents")).json()["consents"] == []
    assert (await api.kit.run("act.low", {"a": 1})).status == TaskStatus.PAUSED     # revoked => asks again


# ───────────────────────────────────────── tester

async def test_tester_non_admin_gets_202_then_runs_after_confirm(api):
    r = await api.post(f"/api/v1/devices/{DEVICE}/invoke", json=invoke_body())
    assert r.status_code == 202, r.text
    b = r.json()
    assert b["awaiting_confirmation"] is True and b["status"] == "paused" and b["result"] is None
    assert b["confirmation"]["capability"] == "act.low" and b["confirmation"]["allowed_scopes"]
    assert not api.kit.invokes                                  # nothing ran yet
    task = await api.kit.tm.get(b["task_id"])
    assert task.session_id == f"tester:{USER}" and task.metadata["source"] == "plugin_tester"

    assert (await api.post(f"/api/v1/tasks/{b['task_id']}/confirm", json={"scope": "session"})).status_code == 200
    done = await wait_status(api.kit, b["task_id"], {TaskStatus.COMPLETED})
    assert done.status == TaskStatus.COMPLETED and len(api.kit.invokes) == 1

    # the SESSION grant now covers the next tester run in the same session
    r2 = await api.post(f"/api/v1/devices/{DEVICE}/invoke", json=invoke_body())
    assert r2.status_code == 200 and r2.json()["ok"] is True and r2.json()["result"] == {"ok": True}
    assert r2.json()["awaiting_confirmation"] is False
    r3 = await api.post(f"/api/v1/devices/{DEVICE}/invoke", json=invoke_body(session_id="other"))
    assert r3.status_code == 202


async def test_tester_admin_skips_prompt_but_is_a_real_audited_task(api):
    await api.make_admin()
    r = await api.post(f"/api/v1/devices/{DEVICE}/invoke", json=invoke_body("act.high"))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] and b["status"] == "completed" and b["awaiting_confirmation"] is False
    assert b["result"] == {"ok": True}                           # DeviceFrame/JSON serialization fixed
    assert (await api.kit.tm.get(b["task_id"])).status == TaskStatus.COMPLETED
    ev = api.kit.sec.audit_logger.repository.events
    assert any(e.event_type == AuditEventType.CONSENT_AUTO_APPROVED and e.task_id == b["task_id"] for e in ev)
    assert (await api.get("/api/v1/consents")).json()["consents"] == []     # admin run stores nothing


async def test_tester_deny_policy_blocks_even_admin(api):
    await api.make_admin()
    r = await api.post(f"/api/v1/devices/{DEVICE}/invoke", json=invoke_body("danger.exec"))
    assert r.status_code == 403
    assert not api.kit.invokes


async def test_tester_ownership_and_unknown_device(api):
    api.as_user(OTHER)
    assert (await api.post(f"/api/v1/devices/{DEVICE}/invoke", json=invoke_body())).status_code == 403
    api.as_user(USER)
    assert (await api.post("/api/v1/devices/nope/invoke", json=invoke_body())).status_code == 404


async def test_tester_cannot_self_declare_admin(api):
    r = await api.post(f"/api/v1/devices/{DEVICE}/invoke",
                       json={**invoke_body(), "pre_approved": True, "is_admin": True})
    assert r.status_code == 202                                  # extra fields ignored; still prompts


# ───────────────────────────────────────── preflight

def pf(**kw):
    return {"capability": "act.low", "device_id": DEVICE, "parameters": {"a": 1}, **kw}


async def test_preflight_decisions(api):
    r = await api.post("/api/v1/authorize/preflight", json=pf())
    assert r.status_code == 200 and r.json()["decision"] == "confirm"
    assert r.json()["confirmation"]["allowed_scopes"] == ["once", "session", "always"]

    free = await api.post("/api/v1/authorize/preflight", json=pf(capability="act.free"))
    assert free.json()["decision"] == "allow"

    # a standing grant makes it allow
    t = await api.kit.run("act.low", {"a": 1}, session=f"tester:{USER}")
    await api.post(f"/api/v1/tasks/{t.id}/confirm", json={"scope": "always"})
    await wait_status(api.kit, t.id, {TaskStatus.COMPLETED})
    assert (await api.post("/api/v1/authorize/preflight", json=pf())).json()["decision"] == "allow"
    assert (await api.post("/api/v1/authorize/preflight", json=pf(parameters={"a": 2}))).json()["decision"] == "confirm"

    assert (await api.post("/api/v1/authorize/preflight", json=pf(capability="danger.exec"))).json()["decision"] == "deny"
    assert any(e.event_type == AuditEventType.PREFLIGHT_DECISION for e in api.kit.sec.audit_logger.repository.events)


async def test_preflight_of_device_scoped_capabilities_asks_instead_of_denying(api):
    """Regression: the tester's preflight denied every device-scoped permission (screen, camera, input) with
    "Device is not allowlisted" because it never allowlisted the device it was asked about."""
    for capability in ("camera.snap", "screen.capture", "keyboard.type", "mouse.click"):
        r = await api.post("/api/v1/authorize/preflight", json=pf(capability=capability, parameters={}))
        body = r.json()
        assert r.status_code == 200 and body["decision"] == "confirm", (capability, body)
        assert "allowlisted" not in body.get("reason", "")


async def test_preflight_admin_cannot_be_faked_and_offline_rules(api, monkeypatch):
    # admin flag is the server's: body fields do nothing
    r = await api.post("/api/v1/authorize/preflight", json={**pf(), "is_admin": True})
    assert r.json()["decision"] == "confirm"
    await api.make_admin()
    assert (await api.post("/api/v1/authorize/preflight", json=pf())).json()["decision"] == "allow"
    assert (await api.post("/api/v1/authorize/preflight", json=pf(capability="danger.exec"))).json()["decision"] == "deny"

    async def offline(device_id): return False
    monkeypatch.setattr(api.kit.rig.dm, "is_connected", offline)
    assert (await api.post("/api/v1/authorize/preflight", json=pf())).json()["decision"] == "allow"   # admin
    await api.make_admin(False)
    off = (await api.post("/api/v1/authorize/preflight", json=pf())).json()
    assert off["decision"] == "deny" and "offline" in off["reason"]


async def test_preflight_foreign_device_is_404(api):
    api.as_user(OTHER)
    assert (await api.post("/api/v1/authorize/preflight", json=pf())).status_code == 404


# ───────────────────────────────────────── migration

async def test_migration_database_without_the_table(tmp_path):
    from pai.config.models.database import DatabaseConfig
    from pai.storage.database import Database
    from pai.storage.models import Base
    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"
    d0 = Database(DatabaseConfig(url=url))
    await d0.initialize()
    async with d0.engine.begin() as conn:                         # simulate the pre-grants schema
        await conn.execute(text("DROP TABLE consent_grants"))
    await d0.engine.dispose()

    d1 = Database(DatabaseConfig(url=url))
    await d1.initialize()                                         # server start on the old DB
    async with d1.engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "consent_grants" in tables
    from pai.security.consent.repository import SqlConsentGrantRepository
    assert await SqlConsentGrantRepository(d1.sessionmaker).list_for_user("u") == []
    await d1.engine.dispose()


async def test_store_failure_fails_closed(kit, db):
    t = await kit.run("act.low", {"a": 1})
    await kit.confirm(t, "always")
    async with db.engine.begin() as conn:
        await conn.execute(text("DROP TABLE consent_grants"))
    again = await kit.run("act.low", {"a": 1})
    assert again.status == TaskStatus.PAUSED                       # cannot read grants => ask the user
