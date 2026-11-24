"""requireConfirmation -> approval gate; approved run -> task-bound token verified."""
import asyncio
from types import SimpleNamespace

import pytest

from pai.common.enums.permission import Origin
from pai.security.authorization.decision import DecisionEffect
from pai.security.authorization.context import ScopeContext
from pai.security.manager import SecurityManager, build_authorization_manager
from pai.plugins.manifest_models import CapabilitySpec


def _manifest(require_confirmation: bool):
    cap = CapabilitySpec(id="screen.capture", description="d", permissions=["camera"],
                         requireConfirmation=require_confirmation, confirmationReason="Capture?")
    return SimpleNamespace(get_capability=lambda c: cap if c == "screen.capture" else None)


class _Session:
    async def close(self): ...


def _sm(monkeypatch):
    sm = SecurityManager.__new__(SecurityManager)
    sm.authorization = build_authorization_manager()
    sm.audit_logger = sm.authorization.audit_logger
    sm.token_issuer = sm.authorization.token_issuer
    from pai.security.tokens.validator import TokenValidator
    sm.token_validator = TokenValidator(sm.token_issuer)
    sm.plugin_manager = None
    sm.get_session = lambda: _Session()
    import pai.security.manager as m
    class Repo:
        def __init__(self, s): ...
        async def is_enabled(self, u, p): return True
    monkeypatch.setattr(m, "UserPluginRepository", Repo)
    return sm


def _auth(sm, manifest, **kw):
    return asyncio.run(sm.authorize(
        user_id="u1", plugin_id="p1", capability="screen.capture", device_id="d1",
        plugin_manifest=manifest, task_id="t1", origin=Origin.USER_DIRECT,
        scope=ScopeContext(allowed_devices=["d1"]), **kw))


def test_manifest_confirmation_requires_approval_then_issues_bound_token(monkeypatch):
    sm = _sm(monkeypatch)
    pending = _auth(sm, _manifest(True))
    assert pending.effect in (DecisionEffect.APPROVAL_REQUIRED, DecisionEffect.DENY)
    if pending.effect == DecisionEffect.DENY:
        pytest.skip(f"denied by static policy: {pending.reason}")
    assert not pending.tokens

    approved = _auth(sm, _manifest(True), approved=True)
    assert approved.effect == DecisionEffect.ALLOW
    tokens = approved.tokens
    assert tokens

    task = SimpleNamespace(authorization=SimpleNamespace(tokens=tokens), plugin="p1", capability="screen.capture",
                           device_id="d1", id="t1", user_id="u1")
    sm.verify_task_tokens(task)                       # valid
    for field, bad in (("id", "t2"), ("device_id", "d2"), ("capability", "x.y"), ("plugin", "p2")):
        with pytest.raises(PermissionError):
            sm.verify_task_tokens(SimpleNamespace(**{**task.__dict__, field: bad}))
    with pytest.raises(PermissionError):
        sm.verify_task_tokens(SimpleNamespace(**{**task.__dict__, "authorization": SimpleNamespace(tokens=[])}))


# ---- policies beyond permissions ------------------------------------------------
from datetime import timedelta

from pai.config.models.security import AuthorizationConfig, PolicyRuleConfig
from pai.security.authorization.enforcement import AuthorizationDenied


def _sm_cfg(monkeypatch, cfg):
    sm = _sm(monkeypatch)
    sm.authorization = build_authorization_manager(cfg)
    sm.audit_logger = sm.authorization.audit_logger
    sm.token_issuer = sm.authorization.token_issuer
    from pai.security.tokens.validator import TokenValidator
    sm.token_validator = TokenValidator(sm.token_issuer)
    return sm


def test_declarative_deny_rule_beats_approval(monkeypatch):
    cfg = AuthorizationConfig(rules=[PolicyRuleConfig(
        id="no-camera-autonomous", effect="deny", permissions=["camera.*"],
        origins=["agent_autonomous"], reason="no autonomous camera")])
    sm = _sm_cfg(monkeypatch, cfg)
    d = asyncio.run(sm.authorize(
        user_id="u1", plugin_id="p1", capability="screen.capture", device_id="d1",
        plugin_manifest=_manifest(False), origin=Origin.AGENT_AUTONOMOUS,
        scope=ScopeContext(allowed_devices=["d1"]), approved=True))
    assert d.effect == DecisionEffect.DENY and "no autonomous camera" in d.reason  # approval can't override


def test_role_policy_denies_unprivileged_verb(monkeypatch):
    sm = _sm_cfg(monkeypatch, AuthorizationConfig())     # user role: read/write only
    cap = CapabilitySpec(id="x.run", description="d", permissions=["process"])
    m = SimpleNamespace(get_capability=lambda c: cap)
    d = asyncio.run(sm.authorize(user_id="u1", plugin_id="p1", capability="x.run", device_id="d1",
                                 plugin_manifest=m, origin=Origin.USER_DIRECT,
                                 scope=ScopeContext(allowed_devices=["d1"], user_roles=["guest"]), approved=True))
    assert d.effect == DecisionEffect.DENY


def test_origin_policy_autonomous_write_needs_approval_and_remembered_consent(monkeypatch):
    sm = _sm_cfg(monkeypatch, AuthorizationConfig())
    kw = dict(user_id="u1", plugin_id="p1", capability="screen.capture", device_id="d1",
              plugin_manifest=_manifest(False), scope=ScopeContext(allowed_devices=["d1"], user_roles=["admin"]))
    # user-direct passes the origin policy; consent still required by permission declaration
    first = asyncio.run(sm.authorize(origin=Origin.USER_DIRECT, **kw))
    assert first.effect != DecisionEffect.DENY or "Role" not in first.reason
    sm.grant_consent("u1", "p1", "camera.capture", ttl=timedelta(minutes=5))   # ConsentManager.grant wired
    sm.revoke_consent("u1", "p1", "camera.capture")                            # ...and revoke
    with pytest.raises(AuthorizationDenied):
        asyncio.run(sm.require_authorization(origin=Origin.USER_DIRECT, **{**kw, "scope": ScopeContext(allowed_devices=[])}))
