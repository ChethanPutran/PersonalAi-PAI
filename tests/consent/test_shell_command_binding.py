"""shell.exec: asked every time, or once per SESSION for that exact command. Never 'always', never 'any command'."""
import pytest

from pai.common.enums.permission import ConsentScope
from pai.security.consent.models import ConsentError, GrantRequest
from pai.tasks.models import TaskStatus
from consent_kit import USER

pytestmark = pytest.mark.asyncio


async def awaiting(kit, params, **kw):
    t = await kit.run("shell.exec", params, **kw)
    assert t.status == TaskStatus.PAUSED and t.confirmation is not None, t.status
    return t


async def test_shell_is_no_longer_blocked_but_always_asks(kit):
    t = await awaiting(kit, {"command": "hostname -I"})
    c = t.confirmation
    assert c.risk == "high" and c.capability == "shell.exec"
    assert [p.value_preview for p in c.parameters_summary if p.name == "command"] == ["hostname -I"]   # the user sees the command


async def test_only_once_and_session_are_offered_never_always(kit):
    c = (await awaiting(kit, {"command": "ls"})).confirmation
    assert [s.value for s in c.allowed_scopes] == ["once", "session"]
    t = await awaiting(kit, {"command": "ls /tmp"})
    with pytest.raises(ConsentError) as e:
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest(scope="always"))
    assert e.value.code == "scope_not_allowed"


async def test_the_confirmation_pins_the_command_and_says_so(kit):
    c = (await awaiting(kit, {"command": "ls"})).confirmation
    assert c.required_binding_keys == ["command"]
    assert c.default_binding.mode.value == "keys" and c.default_binding.keys == ["command"]


async def test_once_runs_that_command_once_and_asks_again(kit):
    t = await awaiting(kit, {"command": "ls"})
    grant, done = await kit.confirm(t, "once")
    assert grant is None and done.status == TaskStatus.COMPLETED
    assert (await kit.run("shell.exec", {"command": "ls"})).status == TaskStatus.PAUSED     # asked again


async def test_session_approval_covers_only_that_command(kit):
    t = await awaiting(kit, {"command": "ls -la", "timeoutSec": 30}, session="s1")
    grant, _ = await kit.confirm(t, "session", mode="keys", keys=["command"])
    assert grant.scope == ConsentScope.SESSION and grant.session_id == "s1"

    same = await kit.run("shell.exec", {"command": "ls -la", "timeoutSec": 99}, session="s1")   # timeout may vary
    assert same.status == TaskStatus.COMPLETED and same.authorization.grant_id == grant.id

    other = await kit.run("shell.exec", {"command": "cat /etc/hostname"}, session="s1")         # another command
    assert other.status == TaskStatus.PAUSED
    await kit.tm.cancel(other.id)

    elsewhere = await kit.run("shell.exec", {"command": "ls -la"}, session="s2")                # another session
    assert elsewhere.status == TaskStatus.PAUSED
    await kit.tm.cancel(elsewhere.id)


async def test_exact_binding_is_also_accepted(kit):
    t = await awaiting(kit, {"command": "pwd"})
    grant, _ = await kit.confirm(t, "session", mode="exact")
    assert grant is not None
    assert (await kit.run("shell.exec", {"command": "pwd"})).status == TaskStatus.COMPLETED


async def test_any_command_is_refused_by_the_server(kit):
    t = await awaiting(kit, {"command": "ls"})
    with pytest.raises(ConsentError) as e:
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest.model_validate(
            {"scope": "session", "binding": {"mode": "any", "keys": []}}))
    assert e.value.code == "binding_too_broad"
    with pytest.raises(ConsentError) as e:                      # 'keys' that leave the command free
        await kit.sec.grant_from_confirmation(t, user_id=USER, request=GrantRequest.model_validate(
            {"scope": "session", "binding": {"mode": "keys", "keys": ["timeoutSec"]}}))
    assert e.value.code == "binding_too_broad"
    assert await kit.sec.consents.list_grants(USER) == []


async def test_an_over_broad_grant_already_in_the_store_is_never_honoured(kit):
    """Defence in depth: even a tampered/legacy 'any' grant must not silence shell."""
    from pai.security.consent.binding import ParamBinding
    from pai.security.consent.models import ConsentGrant
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    from consent_kit import DEVICE
    await kit.sec.consents.grants.add(ConsentGrant(
        user_id=USER, plugin_id="p1", capability="shell.exec", device_id=DEVICE, scope=ConsentScope.SESSION,
        session_id="s1", binding=ParamBinding(mode="any"), params_hash="", risk="high",
        created_at=now, expires_at=now + timedelta(hours=1), granted_by=USER))
    assert (await kit.run("shell.exec", {"command": "ls"}, session="s1")).status == TaskStatus.PAUSED
