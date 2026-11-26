"""Shared fakes for the scheduler tests. The SECURITY pipeline is the real one (real policies,
real tokens), so 'needs consent / blocked / allowed' come from actual policy decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional

from pai.plugins.manifest_models import CapabilitySpec
from pai.security.manager import SecurityManager, build_authorization_manager
from pai.security.tokens.validator import TokenValidator
from pai.tasks.models import TaskResult

USER = "u1"
OTHER_USER = "u2"
DEV = "dev-1"
OTHER_DEV = "dev-9"


def cap(id_: str, perms=(), *, confirm: bool = False, risk: str = "low", params=None) -> CapabilitySpec:
    extra = {"parameters": params} if params else {}
    return CapabilitySpec(id=id_, description=f"{id_} action", permissions=list(perms),
                          requireConfirmation=confirm, risk=risk, **extra)


CAPS: Dict[str, CapabilitySpec] = {
    "camera.snap": cap("camera.snap", ["camera"], risk="medium"),   # control action -> needs consent
    "lights.on": cap("lights.on"),                                   # no permissions, low -> free
    "confirm.me": cap("confirm.me", confirm=True),                   # manifest asks for confirmation
    "danger.exec": cap("danger.exec", ["system"], risk="critical"),     # blocked by system restrictions
    "files.read": cap("files.read", ["filesystem"],                  # no filesystem roots -> denied
                      params={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
}
SCHEMAS = {
    "lights.on": {"type": "object", "properties": {"level": {"type": "integer"},
                                                   "mode": {"type": "string", "enum": ["warm", "cool"]}},
                  "required": ["level"]},
    "camera.snap": {"type": "object", "properties": {"lens": {"type": "string"}}},
}
MANIFEST = SimpleNamespace(get_capability=lambda c: CAPS.get(c))


def _entry(cap_id: str, *, plugin="p1", device=DEV, executor="device_executor") -> dict:
    return {"id": cap_id, "description": f"{cap_id} action",
            "parameters": SCHEMAS.get(cap_id, {"type": "object", "properties": {}}),
            "targets": [{"plugin_id": plugin, "device_id": device, "executor_id": executor,
                         "device_type": "phone"}]}


class FakeRouter:
    """Stands in for CapabilityRouter.describe_capabilities_with_schema."""
    def __init__(self) -> None:
        self.index: Dict[str, dict] = {c: _entry(c) for c in CAPS}
        self.index["server.job"] = _entry("server.job", plugin="srv", device="server", executor="server_executor")
        self.index["schedule.create"] = _entry("schedule.create", plugin="builtin", device="self",
                                               executor="builtin_executor")

    async def describe_capabilities_with_schema(self, user_id: str) -> Dict[str, dict]:
        return self.index


class FakeDevices:
    def __init__(self) -> None:
        self.online = {DEV}
    async def list_devices(self, user_id: str):
        return [SimpleNamespace(id=DEV)] if user_id in (USER, OTHER_USER) else []
    async def is_connected(self, device_id: str) -> bool:
        return device_id in self.online


class _Session:
    async def close(self): ...


def make_security(monkeypatch) -> SecurityManager:
    """The real authorization pipeline over fake plugin/manifest lookups."""
    sm = SecurityManager.__new__(SecurityManager)
    sm.authorization = build_authorization_manager()
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


class FakeTaskManager:
    """Records what the scheduler asks the task pipeline to do."""
    def __init__(self, *, succeed: bool = True) -> None:
        self.runs: List[tuple] = []
        self.created: List[tuple] = []
        self.submitted: List[str] = []
        self.succeed = succeed

    async def run(self, spec, context, wait=True):
        self.runs.append((spec, context))
        return TaskResult(task_id="runtime-1", success=self.succeed,
                          error=None if self.succeed else "device said no")

    async def find_by_idempotency_key(self, key, *, user_id, session_id):     # same signature as TaskManager
        return SimpleNamespace(id="task-123")

    async def create_task(self, spec, **kw):
        self.created.append((spec, kw))
        return SimpleNamespace(id=f"notice-{len(self.created)}")

    async def submit(self, task_id, wait=True):
        self.submitted.append(task_id)


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)
