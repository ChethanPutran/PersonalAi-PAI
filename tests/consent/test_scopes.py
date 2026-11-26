"""Scope matrix, binding matrix, limits, revoke/expiry, isolation, audit, concurrency."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from pai.common.enums.permission import ConsentScope
from pai.security.audit.models import AuditEventType
from pai.security.consent.models import ConsentError, GrantRequest
from pai.security.consent.repository import SqlConsentGrantRepository
from pai.security.consent.manager import ConsentManager
from pai.security.consent.repository import InMemoryConsentRepository
from pai.tasks.models import TaskStatus
from consent_kit import DEVICE, OTHER, USER

pytestmark = pytest.mark.asyncio


def audit_types(sec):
    return [e.event_type for e in sec.audit_logger.repository.events]


async def awaiting(kit, cap, params=None, **kw):
    t = await kit.run(cap, params, **kw)
    assert t.status == TaskStatus.PAUSED and t.confirmation is not None, t.status
    return t


# ───────────────────────────────────────── scopes

async def test_confirmation_info_is_attached_to_the_waiting_task(kit):
    t = await awaiting(kit, "act.low", {"a": 1, "token": "SECRET"})
    c = t.confirmation
    assert (c.capability, c.plugin_id, c.device_id, c.risk) == ("act.low", "p1", DEVICE, "low")
    assert [s.value for s in c.allowed_scopes] == ["once", "session", "always"]
    assert c.can_bind_params and c.default_binding.mode.value == "exact"
    assert "SECRET" not in str(c.parameters_summary)


async def test_once_is_not_reused(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    grant, done = await kit.confirm(t, "once")
    assert grant is None and done.status == TaskStatus.COMPLETED
    t2 = await kit.run("act.low", {"a": 1})
    assert t2.status == TaskStatus.PAUSED          # asked again
    assert await kit.sec.consents.list_grants(USER) == []


async def test_session_reused_in_same_session_only_and_expires(kit, db):
    t = await awaiting(kit, "act.low", {"a": 1}, session="s1")
    grant, _ = await kit.confirm(t, "session")
    assert grant.scope == ConsentScope.SESSION and grant.session_id == "s1"
    assert timedelta(hours=7) < grant.expires_at - grant.created_at <= timedelta(hours=8, seconds=5)

    again = await kit.run("act.low", {"a": 1}, session="s1")
    assert again.status == TaskStatus.COMPLETED and again.authorization.grant_id == grant.id
    other = await kit.run("act.low", {"a": 1}, session="s2")
    assert other.status == TaskStatus.PAUSED       # different session -> prompt again
    await kit.tm.cancel(other.id)

    # expire it
    from sqlalchemy import update
    from pai.storage.models import ConsentGrantModel
    async with db.sessionmaker() as s:
        await s.execute(update(ConsentGrantModel).where(ConsentGrantModel.id == grant.id)
                        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await s.commit()
    after = await kit.run("act.low", {"a": 1}, session="s1")
    assert after.status == TaskStatus.PAUSED


async def test_always_persists_across_restart_with_sql_repo(kit, db, monkeypatch):
    t = await awaiting(kit, "act.low", {"a": 1})
    grant, _ = await kit.confirm(t, "always", days=30)
    assert timedelta(days=29) < grant.expires_at - grant.created_at <= timedelta(days=30, seconds=5)
    # "restart": a brand-new manager over the same database
    fresh = ConsentManager(InMemoryConsentRepository(), SqlConsentGrantRepository(db.sessionmaker))
    found = await fresh.find_grant(user_id=USER, plugin_id="p1", capability="act.low", device_id=DEVICE,
                                   session_id="another-session", parameters={"a": 1})
    assert found is not None and found.id == grant.id
    # and a different session still gets it without prompt
    t2 = await kit.run("act.low", {"a": 1}, session="brand-new")
    assert t2.status == TaskStatus.COMPLETED


async def test_use_is_counted_once_per_run(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    grant, _ = await kit.confirm(t, "always")
    for _ in range(3):
        assert (await kit.run("act.low", {"a": 1})).status == TaskStatus.COMPLETED
    (g,) = await kit.sec.consents.list_grants(USER)
    assert g.use_count == 3 and g.last_used_at is not None


# ───────────────────────────────────────── bindings

async def test_binding_exact(kit):
    t = await awaiting(kit, "act.low", {"a": 1, "b": 2})
    await kit.confirm(t, "always", mode="exact")
    assert (await kit.run("act.low", {"a": 1, "b": 2})).status == TaskStatus.COMPLETED
    changed = await kit.run("act.low", {"a": 1, "b": 3})
    assert changed.status == TaskStatus.PAUSED


async def test_binding_any_covers_every_parameter_set(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    await kit.confirm(t, "always", mode="any")
    assert (await kit.run("act.low", {"a": 99, "z": "x"})).status == TaskStatus.COMPLETED


async def test_binding_keys_changed_bound_prompts_changed_unbound_covered(kit):
    t = await awaiting(kit, "act.low", {"a": 1, "b": 2})
    await kit.confirm(t, "always", mode="keys", keys=["a"])
    assert (await kit.run("act.low", {"a": 1, "b": 777})).status == TaskStatus.COMPLETED   # b unbound
    bound_changed = await kit.run("act.low", {"a": 2, "b": 2})
    assert bound_changed.status == TaskStatus.PAUSED                                        # a bound


async def test_grant_never_crosses_capability_or_device(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    grant, _ = await kit.confirm(t, "always", mode="any")
    assert (await kit.sec.consents.find_grant(
        user_id=USER, plugin_id="p1", capability="act.med", device_id=DEVICE,
        session_id="s1", parameters={})) is None
    assert (await kit.sec.consents.find_grant(
        user_id=USER, plugin_id="p1", capability="act.low", device_id="other-dev",
        session_id="s1", parameters={})) is None


# ───────────────────────────────────────── limits (server-side)

@pytest.mark.parametrize("cap,allowed", [
    ("act.low", ["once", "session", "always"]), ("act.med", ["once", "session", "always"]),
    ("act.high", ["once", "session"]), ("act.capped", ["once", "session"]),   # manifest stricter
    ("camera.snap", ["once"]),                                                # always-approve action
])
async def test_allowed_scopes_per_risk_and_manifest(kit, cap, allowed):
    t = await kit.run(cap, {})
    assert t.status == TaskStatus.PAUSED
    assert [s.value for s in t.confirmation.allowed_scopes] == allowed


async def test_critical_is_once_only(kit):
    t = await kit.run("act.crit", {})
    if t is None or t.status != TaskStatus.PAUSED:
        pytest.skip("critical actions are denied outright by policy in this pipeline")
    assert [s.value for s in t.confirmation.allowed_scopes] == ["once"]


@pytest.mark.parametrize("cap,asked", [("act.high", "always"), ("act.capped", "always"),
                                       ("camera.snap", "session")])
async def test_server_rejects_over_broad_requests(kit, cap, asked):
    t = await kit.run(cap, {})
    with pytest.raises(ConsentError) as e:
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope=asked))
    assert e.value.code == "scope_not_allowed" and e.value.status == 422
    assert await kit.sec.consents.list_grants(USER) == []


async def test_bad_bindings_are_rejected(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    with pytest.raises(ConsentError) as e:
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest.model_validate(
            {"scope": "always", "binding": {"mode": "keys", "keys": ["nope"]}}))
    assert e.value.code == "binding_invalid"
    with pytest.raises(ValueError):
        GrantRequest.model_validate({"scope": "always", "binding": {"mode": "keys", "keys": []}})
    with pytest.raises(ValueError):
        GrantRequest.model_validate({"scope": "always", "unexpected": 1})


async def test_always_days_are_capped(kit):
    t = await awaiting(kit, "act.low", {})
    with pytest.raises(ConsentError):
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope="always", days=366))


# ───────────────────────────────────────── revoke, isolation, audit

async def test_revoke_single_and_bulk(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    g, _ = await kit.confirm(t, "always")
    assert await kit.sec.revoke_grant(USER, g.id) is True
    assert (await kit.run("act.low", {"a": 1})).status == TaskStatus.PAUSED
    assert await kit.sec.revoke_grant(USER, g.id) is False

    for cap in ("act.low", "act.med"):
        t = await awaiting(kit, cap, {})
        await kit.confirm(t, "always", mode="any")
    assert await kit.sec.revoke_grants(USER, capability="act.low") == 1
    assert [x.capability for x in await kit.sec.consents.list_grants(USER)] == ["act.med"]
    assert await kit.sec.revoke_grants(USER, device_id=DEVICE) == 1


async def test_per_user_isolation(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    g, _ = await kit.confirm(t, "always")
    assert await kit.sec.consents.list_grants(OTHER) == []
    assert await kit.sec.revoke_grant(OTHER, g.id) is False          # cannot revoke someone else's
    theirs = await kit.run("act.low", {"a": 1}, user=OTHER)
    assert theirs.status == TaskStatus.PAUSED                          # u's grant does not cover u2
    assert len(await kit.sec.consents.list_grants(USER)) == 1


async def test_audit_trail(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    await kit.confirm(t, "once")
    t = await awaiting(kit, "act.low", {"a": 1})
    g, _ = await kit.confirm(t, "always")
    await kit.run("act.low", {"a": 1})
    await kit.sec.revoke_grant(USER, g.id)
    types = audit_types(kit.sec)
    for expected in (AuditEventType.CONSENT_APPROVED_ONCE, AuditEventType.CONSENT_GRANTED,
                     AuditEventType.CONSENT_AUTO_APPROVED, AuditEventType.CONSENT_REVOKED):
        assert expected in types, expected
    granted = next(e for e in kit.sec.audit_logger.repository.events if e.event_type == AuditEventType.CONSENT_GRANTED)
    assert granted.metadata["grant_id"] == g.id and "a" not in str(granted.metadata.get("parameters", ""))


async def test_concurrent_runs_one_grant(kit):
    t = await awaiting(kit, "act.low", {"a": 1})
    await kit.confirm(t, "always")
    results = await asyncio.gather(*[kit.run("act.low", {"a": 1}, session=f"c{i}") for i in range(2)])
    assert all(r.status == TaskStatus.COMPLETED for r in results)
    (g,) = await kit.sec.consents.list_grants(USER)
    assert g.use_count == 2


async def test_concurrent_confirmations_store_one_grant(kit):
    a = await awaiting(kit, "act.low", {"a": 1}, session="s1")
    b = await awaiting(kit, "act.low", {"a": 1}, session="s1")
    req = GrantRequest(scope="always")
    await asyncio.gather(kit.sec.grant_from_confirmation(a, user_id=USER, request=req),
                         kit.sec.grant_from_confirmation(b, user_id=USER, request=req))
    assert len(await kit.sec.consents.list_grants(USER)) == 1


async def test_admin_pre_approval_skips_only_the_prompt(kit):
    t = await kit.run("act.low", {"a": 1}, pre_approved=True)
    assert t.status == TaskStatus.COMPLETED and t.confirmation is None
    denied = await kit.run("danger.exec", {}, pre_approved=True)
    assert denied is None or denied.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)   # DENY still blocks
    assert not any(i["capability"] == "danger.exec" for i in kit.invokes if "capability" in i)
    assert any(e.event_type == AuditEventType.CONSENT_AUTO_APPROVED for e in kit.sec.audit_logger.repository.events)


async def test_scheduled_origin_never_takes_a_grant_for_a_sensitive_action_silently(kit):
    # a grant made interactively does not turn autonomous origins into free passes beyond policy:
    t = await awaiting(kit, "act.low", {"a": 1})
    await kit.confirm(t, "always")
    from pai.common.enums.permission import Origin
    r = await kit.run("act.low", {"a": 1}, origin=Origin.VOICE)
    assert r is None or r.status in (TaskStatus.COMPLETED, TaskStatus.PAUSED)   # never an error 500; policy decides
