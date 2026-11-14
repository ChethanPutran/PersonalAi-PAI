"""Test fixtures.

Two fixture families live here:

  1. FAST IN-MEMORY ORCHESTRATION (default)
     ──────────────────────────────────────
     Used by `test_builtin_routing.py` and any other unit test that
     exercises `TaskRunner` / `CapabilityRouter` / `TaskManager` without
     needing a real DB, plugin catalog, or LLM.

     Stubs satisfy the manager interfaces at the method level; the real
     classes are never instantiated. `cast()` is used so Pylance accepts
     the stubs where production types are annotated.

  2. REPOSITORY TESTS
     ─────────────────
     `session_factory` gives you an in-memory SQLite engine with the
     full schema created. Each test gets a fresh database (function
     scope). No file cleanup, no leftover state.

Design notes:
  • No `event_loop` fixture — pytest-asyncio (0.23+) manages the loop.
    Adding one shadows the plugin's own loop and breaks async fixtures.
  • All async fixtures use `@pytest_asyncio.fixture` so this conftest
    works in both `asyncio_mode=auto` and `asyncio_mode=strict`.
  • Stubs are deliberately small — add only what the code under test
    actually calls. If a test needs more behaviour, subclass and
    override; don't grow the base stub.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# ---- Production types (for casts) ----
from pai.devices.manager import DeviceManager
from pai.executors.manager import ExecutorManager
from pai.executors.scheduler import ExecutorScheduler
from pai.orchestration.authorization import AuthorizationManager
from pai.plugins.manager import PluginManager

# ---- Production types (for real construction in tests) ----
from pai.executors.builtin import BuiltinExecutor
from pai.orchestration.builtin_capabilities import (
    BuiltinCapability,
    BuiltinCapabilityRegistry,
)
from pai.orchestration.capability_resolver import CapabilityResolver
from pai.orchestration.capability_router import CapabilityRouter
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.task_runner import TaskRunner
from pai.tasks.manager import TaskManager
from pai.tasks.store import TaskStore


# ═══════════════════════════════════════════════════════════════════
# STUBS — no DB, no network, no plugin files
# ═══════════════════════════════════════════════════════════════════
#
# Every stub satisfies ONLY the methods the code under test calls. If
# a fixture swap breaks with `AttributeError: 'Stub...' has no
# attribute 'X'`, add X here — do not grow the stub speculatively.


class StubPluginManager:
    """Satisfies PluginManager surface used by CapabilityRouter / Resolver."""

    def is_initialized(self) -> bool:
        return True

    def get_catalog(self) -> list:
        return []

    def get_status(self, *, user_id: str | None = None) -> Dict[str, Any]:
        return {"stub": True}

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class StubDeviceManager:
    """Satisfies DeviceManager surface used by router / selector."""

    async def list_devices(self, user_id: str) -> list:
        return []

    async def is_connected(self, device_id: str) -> bool:
        return False

    async def get(self, device_id: str) -> Any:
        return None

    def set_capability_router(self, router: Any) -> None:
        pass


class StubAuthorizer:
    """No-op AuthorizationManager.

    Records every call so tests can assert that built-ins skip
    authorization (calls list stays empty).
    """

    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []

    def is_initialized(self) -> bool:
        return True

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def authorize(
        self,
        *,
        user_id: str,
        capability: str,
        device_id: str | None,
        plugin_name: str,
        task: Dict[str, Any],
    ) -> None:
        self.calls.append(
            {
                "user_id": user_id,
                "capability": capability,
                "device_id": device_id,
                "plugin_name": plugin_name,
            }
        )


class StubExecutorScheduler:
    """Minimal ExecutorScheduler.

    TaskManager.submit() → schedule_task(task=..., executor_name=...).
    We resolve the executor by name and hand it the task. The real
    scheduler adds queueing, retries, and metrics; the contract is the
    same.
    """

    def __init__(self, manager: ExecutorManager) -> None:
        self.manager = manager

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def schedule_task(
        self,
        *,
        task: Any,
        executor_name: str,
    ) -> Dict[str, Any]:
        executor = self.manager.get(executor_name)
        if executor is None:
            return {
                "status": "failed",
                "error": f"executor {executor_name!r} not registered",
            }
        return await executor.execute_task(task)


class _NullSubscription:
    async def close(self) -> None:
        pass

    def cancel(self) -> None:
        pass


class NullEventBus:
    """EventBus stub.

    No-ops on everything. If a test needs to observe events it should
    subclass and record — never mutate this base stub.
    """

    def is_initialized(self) -> bool:
        return True

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    # Publish variants across the codebase
    async def publish(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def emit(self, *args: Any, **kwargs: Any) -> None:
        pass

    # Manager._emit is sync in some versions
    def _emit(self, *args: Any, **kwargs: Any) -> None:
        pass

    # Subscribe variants — return inert handles
    def subscribe(self, *args: Any, **kwargs: Any) -> _NullSubscription:
        return _NullSubscription()

    async def subscribe_async(self, *args: Any, **kwargs: Any) -> _NullSubscription:
        return _NullSubscription()


# ═══════════════════════════════════════════════════════════════════
# FAST FIXTURES — in-memory orchestration stack
# ═══════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def event_bus() -> AsyncGenerator[NullEventBus, None]:
    """Shared no-op bus — every manager fixture depends on it."""
    bus = NullEventBus()
    await bus.initialize()
    await bus.start()
    yield bus
    await bus.stop()


@pytest_asyncio.fixture
async def builtin_registry() -> AsyncGenerator[BuiltinCapabilityRegistry, None]:
    reg = BuiltinCapabilityRegistry()

    async def _echo(params, **_):
        return {"echoed": params}

    reg.register(
        BuiltinCapability(
            id="test.echo",
            description="Echo parameters back. Test-only.",
            parameters={"type": "object", "properties": {}},
            handler=_echo,           # ← async, was a sync lambda
        )
    )
    yield reg


@pytest_asyncio.fixture
async def capability_router(
    builtin_registry: BuiltinCapabilityRegistry,
) -> AsyncGenerator[CapabilityRouter, None]:
    """Router wired to stubs. Only built-ins are indexed — no plugins,
    no devices, no DB."""
    router = CapabilityRouter(
        plugin_manager=cast(PluginManager, StubPluginManager()),
        device_manager=cast(DeviceManager, StubDeviceManager()),
        session_factory=None,          # → _user_enabled_plugin_ids returns set()
        builtin_registry=builtin_registry,
    )
    await router.initialize()
    yield router
    await router.shutdown()


@pytest_asyncio.fixture
async def executor_manager(
    builtin_registry: BuiltinCapabilityRegistry,
    event_bus: NullEventBus,
) -> AsyncGenerator[ExecutorManager, None]:
    """ExecutorManager with BuiltinExecutor registered under its
    canonical id (`builtin_executor`)."""
    mgr = ExecutorManager(event_bus=cast(Any, event_bus))
    mgr.register(BuiltinExecutor(builtin_registry))
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


@pytest_asyncio.fixture
async def task_manager(
    executor_manager: ExecutorManager,
    event_bus: NullEventBus,
) -> AsyncGenerator[TaskManager, None]:
    """TaskManager backed by in-memory TaskStore + stub scheduler."""
    tm = TaskManager(
        store=TaskStore(),
        scheduler=cast(
            ExecutorScheduler, StubExecutorScheduler(executor_manager)
        ),
        executor_manager=executor_manager,
        event_bus=cast(Any, event_bus),
        session_factory=None,
    )
    await tm.initialize()
    yield tm
    await tm.shutdown()


@pytest_asyncio.fixture
async def task_runner(
    capability_router: CapabilityRouter,
    executor_manager: ExecutorManager,
    task_manager: TaskManager,
    event_bus: NullEventBus,
) -> AsyncGenerator[TaskRunner, None]:
    """Fully wired TaskRunner — the entry point tests actually call."""
    tr = TaskRunner(
        task_manager=task_manager,
        capability_resolver=CapabilityResolver(
            plugin_manager=cast(PluginManager, StubPluginManager()),
            capability_router=capability_router,
        ),
        device_selector=DeviceSelector(
            device_manager=cast(DeviceManager, StubDeviceManager()),
            executor_manager=executor_manager,
            capability_router=capability_router,
        ),
        authorization_manager=cast(AuthorizationManager, StubAuthorizer()),
        executor_manager=executor_manager,
        event_bus=cast(Any, event_bus),
    )
    # Sub-components are Lifecycle objects — initialize explicitly
    # since TaskRunner only checks `.is_initialized()`, not calls init.
    await tr.capability_resolver.initialize()
    await tr.device_selector.initialize()
    await tr.initialize()
    yield tr
    await tr.shutdown()


@pytest.fixture
def app_context():
    """Minimal context passed to TaskRunner.run(task, context).

    Only the attributes TaskRunner actually reads. If the test needs
    more, extend this namespace — do NOT construct a real
    PAIAppContext (that pulls in DB, LLM, memory, NATS).
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        user_id="test-user",
        session_id="test-session",
        request_id="test-request",
        source_device_id=None,
    )


# ═══════════════════════════════════════════════════════════════════
# REPOSITORY FIXTURES — in-memory SQLite with real schema
# ═══════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[
    async_sessionmaker[AsyncSession], None
]:
    """In-memory SQLite with the full schema created.

    StaticPool is mandatory — without it, each `session_factory()` call
    opens a fresh connection to a fresh `:memory:` database, so the
    schema created during setup is invisible to the test's sessions.

    `from pai.storage import models` is also mandatory — Python only
    registers an ORM class on `Base.metadata` when its module is
    imported. Without it, `create_all` silently creates an empty DB.
    """
    from pai.storage.database import Base
    from pai.storage import models  # noqa: F401 — registers TaskModel, etc.

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    try:
        yield maker
    finally:
        await engine.dispose()