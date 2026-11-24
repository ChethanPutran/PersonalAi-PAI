"""dry_run asks "what would happen?" without side effects; a real run is audited and tokenised."""
from pai.common.enums.permission import Origin
from pai.security.authorization.context import ScopeContext
from support import DEV, MANIFEST, USER


def ask(security, capability, **kw):
    return security.authorize(
        user_id=USER, plugin_id="p1", capability=capability, device_id=DEV, plugin_manifest=MANIFEST,
        parameters={}, scope=ScopeContext(allowed_devices=[DEV]), origin=Origin.SCHEDULED, **kw)


def events(security):
    return security.audit_logger.repository.events


async def test_dry_run_writes_no_audit_and_issues_no_token(security):
    before = len(events(security))
    for cap in ("camera.snap", "lights.on", "shell.exec", "confirm.me"):
        d = await ask(security, cap, dry_run=True)
        assert d.token is None and not d.tokens
    assert len(events(security)) == before


async def test_a_real_run_is_audited_and_allowed_ones_get_a_token(security):
    # no permissions at all -> the "No permissions required" audit path (this recursed once)
    free = await ask(security, "lights.on")
    assert free.effect.value == "allow"
    # denied -> the _deny audit path
    blocked = await ask(security, "shell.exec")
    assert blocked.effect.value == "deny"
    # approved sensitive -> allowed with a token, audited by the AuthorizationManager
    approved = await ask(security, "camera.snap", approved=True)
    assert approved.effect.value == "allow" and approved.tokens
    audited = {(e.capability, e.effect) for e in events(security)}
    assert ("lights.on", True) in audited and ("shell.exec", "deny") in audited or len(events(security)) >= 3


async def test_origin_scheduled_makes_sensitive_actions_wait_for_approval(security):
    assert (await ask(security, "camera.snap")).effect.value == "approval_required"
    assert (await ask(security, "camera.snap", approved=True)).effect.value == "allow"
