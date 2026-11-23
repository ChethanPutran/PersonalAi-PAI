"""Test fixtures.

Two fixture families live here:

  1. FAST IN-MEMORY ORCHESTRATION
     Real TaskManager / ExecutorScheduler / ExecutorManager / CapabilityRouter wired
     to tiny stubs for the parts that need a DB, plugin catalog, device socket or LLM
     (plugin manager, device manager, security manager, event bus).

  2. REPOSITORY TESTS
     `session_factory` gives an in-memory SQLite engine with the full schema.

Design notes:
  * No `event_loop` fixture — pytest-asyncio manages the loop.
  * Async fixtures use `@pytest_asyncio.fixture`.
  * Stubs are deliberately small — add only what the code under test calls.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from pai.config.models.executor import ExecutorConfig
from pai.devices.manager import DeviceManager
from pai.executors.builtin import BuiltinExecutor
from pai.executors.manager import ExecutorManager
from pai.executors.scheduler import ExecutorScheduler
from pai.orchestration.builtin_capabilities import (
    BuiltinCapability,
    BuiltinCapabilityRegistry,
)
from pai.orchestration.capability_resolver import CapabilityResolver
from pai.orchestration.capability_router import CapabilityRouter
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.execution_context import ExecutionContext
from pai.plugins.manager import PluginManager
from pai.security.authorization.decision import AuthorizationDecision, DecisionEffect
from pai.tasks.manager import TaskManager
from pai.tasks.models import TaskRetryPolicy
from pai.tasks.store import TaskStore


# ═══════════════════════════════════════════════════════════════════
# STUBS — no DB, no network, no plugin files
# ═══════════════════════════════════════════════════════════════════


class StubPluginManager:
    """Satisfies the PluginManager surface used by CapabilityRouter / Resolver."""

    def is_initialized(self) -> bool:
        return True

    def get_catalog(self) -> list:
        return []

    def get_manifest(self, plugin_id: str) -> None:
        return None

    def get_status(self, *, user_id: str | None = None) -> dict[str, Any]:
        return {"stub": True}

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class StubDeviceManager:
    """Satisfies the DeviceManager surface used by router / selector."""

    async def list_devices(self, user_id: str) -> list:
        return []

    async def is_connected(self, device_id: str) -> bool:
        return False

    async def get(self, device_id: str) -> Any:
        return None

    def set_capability_router(self, router: Any) -> None:
        pass


class StubSecurityManager:
    """Allow-all SecurityManager. Records authorize() calls so tests can assert
    that built-ins skip authorization (calls stays empty)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_initialized(self) -> bool:
        return True

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def authorize(self, **kw: Any) -> AuthorizationDecision:
        self.calls.append(kw["capability"])
        return AuthorizationDecision(DecisionEffect.ALLOW, "stub", "r", tokens=[])

    async def authorize_task(self, task: Any, approved: bool = False) -> AuthorizationDecision:
        return AuthorizationDecision(DecisionEffect.ALLOW, "stub", "r", tokens=[])

    def verify_task_tokens(self, task: Any) -> None:
        pass

    def audit_execution_started(self, **kw: Any) -> None: ...
    def audit_execution_completed(self, **kw: Any) -> None: ...
    def audit_execution_failed(self, **kw: Any) -> None: ...


class NullEventBus:
    """EventBus stub: no-ops on everything. Subclass to record events."""

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

    async def publish(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def subscribe(self, *args: Any, **kwargs: Any) -> str:
        return "null-subscription"

    async def unsubscribe(self, *args: Any, **kwargs: Any) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
# FAST FIXTURES — in-memory orchestration stack
# ═══════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def event_bus() -> AsyncGenerator[NullEventBus, None]:
    bus = NullEventBus()
    await bus.initialize()
    yield bus
    await bus.shutdown()


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
            handler=_echo,
        )
    )
    yield reg


@pytest_asyncio.fixture
async def capability_router(
    builtin_registry: BuiltinCapabilityRegistry,
) -> AsyncGenerator[CapabilityRouter, None]:
    """Router wired to stubs. Only built-ins are indexed."""
    router = CapabilityRouter(
        plugin_manager=cast(PluginManager, StubPluginManager()),
        device_manager=cast(DeviceManager, StubDeviceManager()),
        session_factory=None,
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
    mgr = ExecutorManager(event_bus=cast(Any, event_bus), config=ExecutorConfig())
    mgr.register(BuiltinExecutor(builtin_registry))
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


@pytest.fixture
def security_manager() -> StubSecurityManager:
    return StubSecurityManager()


@pytest_asyncio.fixture
async def task_manager(
    capability_router: CapabilityRouter,
    executor_manager: ExecutorManager,
    event_bus: NullEventBus,
    security_manager: StubSecurityManager,
) -> AsyncGenerator[TaskManager, None]:
    """Real TaskManager (it owns task running now) over an in-memory store and the
    real ExecutorScheduler."""
    resolver = CapabilityResolver(
        plugin_manager=cast(PluginManager, StubPluginManager()),
        capability_router=capability_router,
    )
    selector = DeviceSelector(
        device_manager=cast(DeviceManager, StubDeviceManager()),
        executor_manager=executor_manager,
        capability_router=capability_router,
    )
    await resolver.initialize()
    await selector.initialize()
    scheduler = ExecutorScheduler(
        llm_service=None, plugin_manager=None, manager=executor_manager,
        device_manager=StubDeviceManager(), register_defaults=False,
    )
    tm = TaskManager(
        store=TaskStore(),
        scheduler=scheduler,
        executor_manager=executor_manager,
        capability_resolver=resolver,
        device_selector=selector,
        security_manager=cast(Any, security_manager),
        event_bus=cast(Any, event_bus),
        session_factory=None,
        retry_policy=TaskRetryPolicy(default_max_retries=0, base_delay_seconds=0.01),
    )
    await tm.initialize()
    yield tm
    await tm.shutdown()


@pytest.fixture
def app_context() -> ExecutionContext:
    """The ExecutionContext handed to TaskManager.run()."""
    return ExecutionContext(
        user_id="test-user", session_id="test-session", request_id="test-request",
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