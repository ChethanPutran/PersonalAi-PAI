"""Shared in-process rig. DAG -> TaskManager -> ExecutorScheduler -> DeviceExecutor
-> DeviceManager -> (fake) device socket, with real EventBus.

The fake device behaves like the Flutter/Kotlin client: it receives INVOKE frames,
answers with RESULT frames, and acks CONTROL frames.
"""
import asyncio
import time

import pytest
import pytest_asyncio
from types import SimpleNamespace

from pai.common.enums.permission import Origin
from pai.config.models.event_bus import EventBusConfig
from pai.config.models.executor import ExecutorConfig
from pai.devices.manager import DeviceManager
from pai.devices.websocket import WebSocketDeviceConnection
from pai.event_bus.bus import EventBus
from pai.executors.manager import ExecutorManager
from pai.executors.scheduler import ExecutorScheduler
from pai.orchestration.capability_router import CapabilityTarget
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.dag_scheduler import DAGScheduler
from pai.planning.models import TaskSpec
from pai.security.authorization.decision import AuthorizationDecision, DecisionEffect
from pai.tasks.manager import TaskManager
from pai.common.task_controls import CancelMode, TaskControls
from pai.tasks.models import TaskRetryPolicy
from pai.tasks.store import TaskStore

DEVICE = "dev-1"


class FakeWS:
    """Stands in for the accepted FastAPI WebSocket."""
    def __init__(self): self.sent = asyncio.Queue(); self.closed = False
    async def send_json(self, payload): await self.sent.put(payload)
    async def close(self, code=1000): self.closed = True


class FakeSecurity:
    def __init__(self, reject_tokens=False): self.reject_tokens = reject_tokens
    def is_initialized(self): return True
    def _tokens(self): return ["tok"] if self.reject_tokens else []
    async def authorize(self, **kw):
        return AuthorizationDecision(DecisionEffect.ALLOW, "ok", "r", tokens=self._tokens())
    async def authorize_task(self, task, approved=False):
        return AuthorizationDecision(DecisionEffect.ALLOW, "ok", "r", tokens=self._tokens())
    def verify_task_tokens(self, task):
        if self.reject_tokens:
            raise PermissionError("token rejected")
    def audit_execution_started(self, **kw): ...
    def audit_execution_completed(self, **kw): ...
    def audit_execution_failed(self, **kw): ...


# What the simulated device plugins declare in their manifests (`controls`). Anything not
# listed gets the safe default: cannot pause once dispatched, best-effort cancel.
DEFAULT_CONTROLS = {
    "monitor.watch": TaskControls(pause=True, cancel=CancelMode.COOPERATIVE),
    "camera.stream": TaskControls(pause=True, cancel=CancelMode.COOPERATIVE),
}


class FakeResolver:
    """Simulates the plugin manifest: `retryable_by_capability` {capability: retryable} and
    `controls_by_capability` {capability: TaskControls}."""
    def __init__(self, retryable_by_capability=None, controls_by_capability=None):
        self.manifest_flags = retryable_by_capability
        self.controls = DEFAULT_CONTROLS if controls_by_capability is None else controls_by_capability
    def is_initialized(self): return True
    async def resolve(self, spec, user_id):
        flags, controls = self.manifest_flags or {}, self.controls
        def get_capability(c):
            if c in flags or c in controls:
                return SimpleNamespace(retryable=flags.get(c, True), controls=controls.get(c, TaskControls()))
            return None
        manifest = SimpleNamespace(get_capability=get_capability)
        return SimpleNamespace(capability=spec.capability, plugin_id="p1", plugin_manifest=manifest, targets=[])


class FakeSelector:
    def __init__(self, executor_id): self.executor_id = executor_id
    def is_initialized(self): return True
    async def select(self, **kw):
        return CapabilityTarget(capability=kw["capability"], plugin_id="p1", device_id=DEVICE,
                                executor_id=self.executor_id, device_type="android", requires_device=True)


@pytest_asyncio.fixture
async def rig():
    bus = EventBus(config=EventBusConfig())
    await bus.initialize()
    await bus.start()
    events = []
    await bus.subscribe("*", lambda e: _collect(events, e))

    dm = DeviceManager(session_factory=None, event_bus=bus)
    await dm.initialize()
    ws = FakeWS()
    conn = WebSocketDeviceConnection(DEVICE, ws)
    await conn.connect()
    from pai.common.enums.device import DeviceArchitecture, DevicePlatform, DeviceType
    from pai.devices.models import DeviceInfo
    await dm.registry.register(DeviceInfo(
        id=DEVICE, name="phone", app_version="1", runtime_version="1", user_id="u",
        platform=list(DevicePlatform)[0], architecture=list(DeviceArchitecture)[0],
        device_type=list(DeviceType)[0]))
    dm._connections[DEVICE] = conn

    em = ExecutorManager(event_bus=bus, config=ExecutorConfig())
    sched = ExecutorScheduler(llm_service=None, plugin_manager=None, manager=em, device_manager=dm)
    await em.initialize()
    await sched.initialize()
    dev_exec = em.get("device_executor")

    store = TaskStore()
    made = []

    async def make_tm(policy=None, security=None, store_=None, resolver=None):
        """A fresh TaskManager on the shared store/scheduler — i.e. 'the server after a restart'."""
        tm_ = TaskManager(store=store_ or store, scheduler=sched, executor_manager=em,
                          capability_resolver=resolver or FakeResolver(), device_selector=FakeSelector("device_executor"),
                          security_manager=security or FakeSecurity(), event_bus=bus, session_factory=None,
                          retry_policy=policy or TaskRetryPolicy(default_max_retries=0, base_delay_seconds=0.01))
        await tm_.initialize()
        made.append(tm_)
        return tm_

    tm = await make_tm()
    dag = DAGScheduler(event_bus=bus, task_manager=tm)
    r = SimpleRig(bus, dm, ws, em, dev_exec, tm, dag, events)
    r.store, r.make_tm = store, make_tm
    yield r
    for t in made:
        await t.shutdown()
    await bus.shutdown()


async def _collect(events, e):
    events.append((getattr(e.event_type, "value", e.event_type), e.data))


class SimpleRig:
    def __init__(self, bus, dm, ws, em, dev_exec, tm, dag, events):
        self.bus, self.dm, self.ws, self.em, self.dev_exec, self.tm, self.dag, self.events = bus, dm, ws, em, dev_exec, tm, dag, events


async def fake_device(rig, handler, stop):
    """Device loop: reads frames the backend sent, replies like the real client.

    Each frame is handled concurrently (a real client keeps reading control frames
    while a long-running invoke is in progress).
    """
    log, handlers = [], set()
    while not stop.is_set():
        try:
            frame = await asyncio.wait_for(rig.ws.sent.get(), .05)
        except asyncio.TimeoutError:
            continue
        log.append(frame)
        t = asyncio.create_task(handler(frame))
        handlers.add(t)
        t.add_done_callback(handlers.discard)
    for t in handlers:
        t.cancel()
    await asyncio.gather(*handlers, return_exceptions=True)
    return log


from pai.devices.models import DeviceFrame
from pai.api.routes.devices import dispatch_device_frame
from pai.planning.models import TaskSpec
from pai.tasks.models import TaskType


def spec(i, deps=(), timeout=5, kind=TaskType.ONE_SHOT, capability="notification.send", retry_count=0):
    # retry_count is explicit: TaskSpec's own default is a product choice (currently 3) and these
    # tests care about the TaskManager's retry POLICY, which applies when the spec says 0.
    return TaskSpec(id=i, name=i, input=i, type=kind, capability=capability,
                    depends_on=list(deps), timeout=timeout, retry_count=retry_count)


def frame_dict(f):
    return f if isinstance(f, dict) else f.model_dump(mode="json")


async def device_sends(rig, **fields):
    """The device pushes a frame to the backend through the REAL route dispatcher."""
    ctx = SimpleNamespace(orchestrator=SimpleNamespace(
        device_manager=rig.dm, executor_manager=rig.em, task_manager=rig.tm))
    await dispatch_device_frame(DeviceFrame(**fields), device_id=DEVICE, context=ctx, websocket=rig.ws)
