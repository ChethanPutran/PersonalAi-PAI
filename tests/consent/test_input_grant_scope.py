"""security.authorization.input_grant_scope: how far an approval of keyboard/mouse actions may reach.

   once     (default) every action asks again; nothing can be remembered
   session  "approve for this session" is offered; high-risk actions still never get "always"

Camera control is once-only under both. Enforced server-side by the real SecurityManager / consent
pipeline (the client's wishes never widen it).
"""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from consent_kit import USER
from pai.common.enums.permission import ConsentScope
from pai.config.models.security import AuthorizationConfig, SecurityConfig
from pai.security.consent.models import ConsentError, GrantRequest
from pai.security.manager import always_approve_from_config
from pai.security.policies.builtin.approval_policy import ApprovalPolicy
from pai.tasks.models import TaskStatus

pytestmark = pytest.mark.asyncio


CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def shipped_authorization(scope: str) -> AuthorizationConfig:
    """The REAL authorization config (roles/RBAC as shipped) with only the setting under test changed.

    Real matters here: RBAC maps 'control' to the 'execute' verb, which the shipped `user` role holds;
    the model's built-in default roles do not, and would deny input outright instead of asking."""
    authz = SecurityConfig.model_validate(yaml.safe_load(CONFIG_FILE.read_text())["security"]).authorization
    return authz.model_copy(update={"input_grant_scope": scope})


@pytest.fixture(params=["once", "session"])
def authz_config(request):
    return shipped_authorization(request.param)


def setting(kit) -> str:
    return "session" if ("input", "control") not in kit.sec.always_approve else "once"


async def awaiting(kit, cap, params=None, **kw):
    t = await kit.run(cap, params, **kw)
    assert t.status == TaskStatus.PAUSED and t.confirmation is not None, t.status
    return t


# ── the setting itself (no pipeline needed) ─────────────────────────────────

async def test_default_is_once_and_invalid_values_are_rejected():
    assert AuthorizationConfig().input_grant_scope == "once"
    for bad in ("always", "never", "", "SESSION", 1):
        with pytest.raises(ValidationError):
            AuthorizationConfig(input_grant_scope=bad)


async def test_the_shipped_config_file_agrees_with_the_model():
    cfg = yaml.safe_load(CONFIG_FILE.read_text())
    authz = cfg["security"]["authorization"]
    assert authz["input_grant_scope"] == "once"                          # the shipped default is the strict one
    assert SecurityConfig.model_validate(cfg["security"]).authorization.input_grant_scope == "once"


async def test_the_derived_set_only_ever_relaxes_input():
    strict, relaxed = ApprovalPolicy.always_approve_for("once"), ApprovalPolicy.always_approve_for("session")
    assert ("camera", "control") in strict and ("camera", "control") in relaxed       # camera never relaxed
    assert ("input", "control") in strict and ("input", "control") not in relaxed
    assert relaxed == strict - {("input", "control")}
    assert always_approve_from_config(None) == strict                                 # no config = strict
    assert always_approve_from_config(AuthorizationConfig(enabled=False)) == strict


# ── what the user is offered, through the real pipeline (the REAL manifest's capabilities) ──────────

PROMPTING = ["input.start", "keyboard.type", "keyboard.key", "mouse.click", "mouse.move", "mouse.scroll"]


def expected_scopes(cap: str, mode: str) -> list:
    # Every input action is HIGH risk (the registry classes the input permission as high and a manifest cannot
    # under-declare it), so even with "session" configured none of them is ever offered "always".
    return ["once"] if mode == "once" else ["once", "session"]


async def test_offered_scopes_follow_the_setting(kit):
    for cap in PROMPTING:
        t = await awaiting(kit, cap, {"x": 1})
        assert [s.value for s in t.confirmation.allowed_scopes] == expected_scopes(cap, setting(kit)), cap
        await kit.tm.cancel(t.id)
    t = await awaiting(kit, "camera.snap", {})                                         # camera never changes
    assert [s.value for s in t.confirmation.allowed_scopes] == ["once"]


async def test_status_and_stop_never_ask_under_either_setting(kit):
    """input.stop must be able to release control instantly, and input.status is a read: neither declares the
    sensitive input permission, so neither is gated (a prompt just to STOP typing would defeat the point)."""
    for cap in ("input.status", "input.stop"):
        t = await kit.run(cap, {})
        assert t is not None and t.status == TaskStatus.COMPLETED, cap


async def test_a_capability_that_declares_input_is_gated_even_if_it_never_asks(kit):
    t = await kit.run("input.silent", {})
    assert t is not None and t.status == TaskStatus.PAUSED


async def test_the_server_enforces_it_when_a_grant_is_requested(kit):
    t = await awaiting(kit, "keyboard.type", {"text": "hi"})
    with pytest.raises(ConsentError) as e:                                   # never allowed for a high-risk action
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope="always"))
    assert e.value.code == "scope_not_allowed"

    if setting(kit) == "once":
        with pytest.raises(ConsentError) as e:
            await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope="session"))
        assert e.value.code == "scope_not_allowed"
        assert await kit.sec.consents.list_grants(USER) == []                # nothing was remembered
    else:
        grant = await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope="session"))
        assert grant.scope == ConsentScope.SESSION


async def test_camera_stays_once_only_under_both_settings(kit):
    t = await awaiting(kit, "camera.snap", {})
    with pytest.raises(ConsentError) as e:
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope="session"))
    assert e.value.code == "scope_not_allowed"


# ── what actually happens on the next call ──────────────────────────────────

async def test_the_next_action_asks_again_or_not_depending_on_the_setting(kit):
    t = await awaiting(kit, "keyboard.type", {"text": "hello"}, session="s1")
    if setting(kit) == "once":
        grant, done = await kit.confirm(t, "once")
        assert grant is None and done.status == TaskStatus.COMPLETED
        again = await kit.run("keyboard.type", {"text": "hello"}, session="s1")
        assert again.status == TaskStatus.PAUSED                             # asks EVERY time
        await kit.tm.cancel(again.id)
    else:
        grant, done = await kit.confirm(t, "session")
        assert done.status == TaskStatus.COMPLETED
        again = await kit.run("keyboard.type", {"text": "hello"}, session="s1")
        assert again.status == TaskStatus.COMPLETED and again.authorization.grant_id == grant.id   # no prompt
        other_session = await kit.run("keyboard.type", {"text": "hello"}, session="s2")
        assert other_session.status == TaskStatus.PAUSED                     # a session grant never leaks to another
        await kit.tm.cancel(other_session.id)


async def test_a_session_grant_is_bound_to_the_exact_parameters_by_default(kit):
    if setting(kit) != "session":
        pytest.skip("only meaningful when session grants exist")
    t = await awaiting(kit, "keyboard.type", {"text": "a"}, session="s1")
    await kit.confirm(t, "session")                                          # default binding = exact parameters
    different = await kit.run("keyboard.type", {"text": "something else"}, session="s1")
    assert different.status == TaskStatus.PAUSED                             # typing different text still asks
    await kit.tm.cancel(different.id)


async def test_a_session_grant_for_one_capability_never_covers_another(kit):
    if setting(kit) != "session":
        pytest.skip("only meaningful when session grants exist")
    t = await awaiting(kit, "mouse.move", {"x": 1, "y": 1}, session="s1")
    await kit.confirm(t, "session", mode="any")                              # approve moving the mouse, any position
    click = await kit.run("mouse.click", {"x": 1, "y": 1}, session="s1")
    assert click.status == TaskStatus.PAUSED                                 # clicking is a separate decision
    await kit.tm.cancel(click.id)
