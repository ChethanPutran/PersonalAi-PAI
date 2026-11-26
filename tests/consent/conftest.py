"""Kit for the confirmation-scope / grant tests.

REAL: SecurityManager authorization pipeline (policies, risk, consent manager, audit), the SQL
grant repository over a file SQLite, the TaskManager and its confirmation gate, the real routes.
FAKE: the plugin manifest lookup and the device socket (replies to INVOKE frames).
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rig import DEVICE, FakeSelector, device_sends, fake_device, frame_dict, rig  # noqa: E402,F401

from pai.common.enums.permission import Origin  # noqa: E402
from pai.config.models.database import DatabaseConfig  # noqa: E402
from pai.orchestration.execution_context import ExecutionContext  # noqa: E402
from pai.planning.models import TaskSpec  # noqa: E402
from pai.plugins.manifest_install import load_manifest_file  # noqa: E402
from pai.plugins.manifest_models import CapabilitySpec  # noqa: E402
from pai.security.consent.models import GrantRequest  # noqa: E402
from pai.security.consent.repository import SqlConsentGrantRepository  # noqa: E402
from pai.security.manager import (SecurityManager, always_approve_from_config,  # noqa: E402
                                   build_authorization_manager)
from pai.security.tokens.validator import TokenValidator  # noqa: E402
from pai.storage.database import Database  # noqa: E402
from pai.tasks.models import TaskStatus  # noqa: E402

from consent_kit import OTHER, USER  # noqa: E402,F401


def cap(id_, perms=(), *, risk="low", confirm=True, max_scope=None):
    return CapabilitySpec(id=id_, description=id_, permissions=list(perms), requireConfirmation=confirm,
                          risk=risk, maxGrantScope=max_scope)


CAPS = {c.id: c for c in [
    cap("act.low"), cap("act.med", risk="medium"), cap("act.high", risk="high"),
    cap("act.crit", risk="critical"), cap("act.capped", max_scope="session"),
    cap("act.free", confirm=False),
    cap("camera.snap", ["camera"], risk="medium"),         # ApprovalPolicy always-approve -> once only
    cap("input.silent", ["input"], risk="high", confirm=False),  # declares input but never asks (the policy must still gate it)
    cap("danger.exec", ["system"], risk="critical"),          # blocked by system restrictions (DENY)
]}
# The input plugin's capabilities exactly as shipped (real risk / confirmation / permissions), so the tests
# cannot drift from the manifest.
_INPUT_MANIFEST = Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins" / "input" / "1.0.0" / "linux" / "manifest.json"
INPUT_CAPS = {c.id: c for c in load_manifest_file(_INPUT_MANIFEST).capabilities}
CAPS.update(INPUT_CAPS)
_SCREEN_MANIFEST = Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins" / "screen" / "1.1.0" / "linux" / "manifest.json"
CAPS.update({c.id: c for c in load_manifest_file(_SCREEN_MANIFEST).capabilities})
# The shell plugin exactly as shipped (risk comes from the pipeline; the manifest only asks for confirmation).
_SHELL_MANIFEST = Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins" / "shell" / "1.0.0" / "linux" / "manifest.json"
CAPS.update({c.id: c for c in load_manifest_file(_SHELL_MANIFEST).capabilities})
MANIFEST = SimpleNamespace(get_capability=lambda c: CAPS.get(c))


class Resolver:
    def is_initialized(self): return True
    async def resolve(self, spec, user_id):
        return SimpleNamespace(capability=spec.capability, plugin_id="p1", plugin_manifest=MANIFEST, targets=[])


class _Session:
    async def close(self): ...


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'consent.db'}"))
    await d.initialize()
    yield d
    await d.engine.dispose()


def make_security(monkeypatch, db, authz_config=None) -> SecurityManager:
    sm = SecurityManager.__new__(SecurityManager)
    sm.always_approve = always_approve_from_config(authz_config)      # as SecurityManager.__init__ does
    sm.authorization = build_authorization_manager(authz_config, SqlConsentGrantRepository(db.sessionmaker))
    sm.audit_logger = sm.authorization.audit_logger
    sm.token_issuer = sm.authorization.token_issuer
    sm.token_validator = TokenValidator(sm.token_issuer)
    sm.plugin_manager = SimpleNamespace(get_manifest=lambda pid: MANIFEST if pid == "p1" else None)
    sm.device_manager = None
    sm.get_session = lambda: _Session()
    sm._initialized = True
    import pai.security.manager as m

    class Repo:
        def __init__(self, s): ...
        async def is_enabled(self, u, p): return True
    monkeypatch.setattr(m, "UserPluginRepository", Repo)
    return sm


@pytest.fixture
def authz_config():
    """security.authorization config for the test; None = defaults. Override in a test module to change it."""
    return None


@pytest.fixture
def sec(monkeypatch, db, authz_config):
    return make_security(monkeypatch, db, authz_config)


class Kit:
    """Runs capabilities through the real TaskManager with a fake device that answers INVOKEs."""
    def __init__(self, rig, tm, sec):
        self.rig, self.tm, self.sec = rig, tm, sec
        self.invokes = []

    async def run(self, capability, params=None, *, session="s1", user=USER, pre_approved=False,
                  origin=Origin.USER_DIRECT, settle=3.0):
        ctx = ExecutionContext(user_id=user, session_id=session, origin=origin, pre_approved=pre_approved,
                               metadata={"allowed_devices": [DEVICE]})
        spec = TaskSpec(id=f"t_{len(self.invokes)}_{capability}", name=capability, capability=capability,
                        parameters=dict(params or {}), preferred_device=DEVICE, timeout=5, retry_count=0)
        try:
            await self.tm.run(spec, ctx, wait=False)
        except PermissionError:
            return None
        task = await self.tm.find_by_idempotency_key(f"{ctx.request_id}:{spec.id}", user_id=user, session_id=session)
        return await self.settle(task.id, settle)

    async def settle(self, task_id, timeout=3.0):
        end = asyncio.get_running_loop().time() + timeout
        while True:
            t = await self.tm.get(task_id)
            if t.terminal or (t.status == TaskStatus.PAUSED and t.confirmation is not None):
                return t
            if asyncio.get_running_loop().time() > end:
                return t
            await asyncio.sleep(0.02)

    async def confirm(self, task, scope="once", mode="exact", keys=(), days=None, user=None):
        req = GrantRequest.model_validate({"scope": scope, "binding": {"mode": mode, "keys": list(keys)},
                                           **({"days": days} if days else {})})
        grant = await self.sec.grant_from_confirmation(task, user_id=user or task.user_id, request=req)
        await self.tm.resume(task.id)
        return grant, await self.settle(task.id)


@pytest_asyncio.fixture
async def kit(rig, sec):
    tm = await rig.make_tm(security=sec, resolver=Resolver())
    k = Kit(rig, tm, sec)
    stop = asyncio.Event()

    async def handler(frame):
        f = frame_dict(frame)
        if f["type"] == "invoke":
            k.invokes.append(f)
            await device_sends(rig, type="result", request_id=f["request_id"], result={"ok": True})
    dev = asyncio.create_task(fake_device(rig, handler, stop))
    yield k
    stop.set()
    await dev
