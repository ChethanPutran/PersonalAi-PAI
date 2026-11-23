"""Application context shared by API handlers."""

from dataclasses import dataclass
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from pai.agents.manager import AgentManager
from pai.config.app import Config
from pai.config.models.memory import KnowledgeGraphConfig
from pai.executors.builtin import BuiltinExecutor
from pai.executors.builtin import BuiltinExecutor
from pai.notifications.push_service import PushService
from pai.orchestration.builtin_capabilities import BuiltinCapabilityRegistry
from pai.orchestration.builtins.schedule import build_schedule_capabilities
from pai.orchestration.builtins.tasks import build_task_capabilities
from pai.orchestration.builtins.tasks import build_task_capabilities
from pai.orchestration.builtins.schedule import build_schedule_capabilities
from pai.orchestration.execution_context import ContextManager
from pai.orchestration.device_selector import DeviceSelector
from pai.planning.planner import Planner
from pai.plugins.packager import PluginPackager
from pai.plugins.store import PluginArtifactStore
from pai.prompts.registry import PromptRegistry
from pai.prompts.tracker import PromptVersionTracker
from pai.security.guards.llm_guard import PromptGuard
from pai.security.guards.plugin_guard import PluginGuard
from pai.storage.database import Database, create_database
from pai.storage.repositories.chat import ChatRepository
from pai.storage.repositories.task import TaskRepository
from pai.tasks.models import TaskRetryPolicy
from pai.tasks.manager import TaskManager
from pai.devices.manager import DeviceManager
from pai.plugins.manager import PluginManager
from pai.security.manager import SecurityManager
from pai.executors.manager import ExecutorManager
from pai.executors.scheduler import ExecutorScheduler
from pai.orchestration.capability_router import CapabilityRouter
from pai.orchestration.capability_resolver import CapabilityResolver
from pai.plugins.registry import PluginRegistry
from pai.event_bus.bus import EventBus
from pai.memory.manager import MemoryManager
from pai.planning.dag_scheduler import DAGScheduler
from pai.orchestration.orchestrator import TaskOrchestrator
from pai.session.manager import SessionManager
from pai.llm.router import LLMRouter
from pai.scheduler.scheduler_service import SchedulerService
from pai.plugins.store import LocalArtifactStore


@dataclass
class PAIAppContext:
    """Runtime objects owned by the FastAPI application."""

    orchestrator: TaskOrchestrator
    database: Database
    session_manager: SessionManager
    session: AsyncSession
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    source_device_id: Optional[str] = None
    _task_event_subscription: Optional[object] = None  # subscription handle for cleanup


def create_app_context(config: Config) -> PAIAppContext:
    """Create the application context with injectable runtime dependencies."""

    # ── Event bus —
    event_bus = EventBus(config=config.event_bus)

    # ── Database — create a single shared database instance for the app ──
    database = create_database(config.database)

    prompt_registry = PromptRegistry(config.prompts.registry)
    prompt_tracker = PromptVersionTracker(
        session_factory=database.sessionmaker,
        enabled=config.prompts.track_usage,
    )

    # Registry
    builtin_registry = BuiltinCapabilityRegistry()

    # ── Context manager — honor context_history_size ──
    context_manager = ContextManager(
        max_history=config.context.max_history,
    )

    # ── Memory — 
    memory_manager = MemoryManager(
        event_bus=event_bus,
        config=config.memory
    )

    device_manager = DeviceManager(session_factory=database.factory,
                                   event_bus=event_bus,
                                   config = config.devices
                                   )
    agent_manager = AgentManager(event_bus=event_bus)
    security_manager = SecurityManager(session_factory=database.factory,
                                       event_bus=event_bus,
                                       authorization_config=config.security.authorization)

    plugin_artifact_store = LocalArtifactStore(packages_root=config.plugins.plugin_local_artifact_path)
    packager = PluginPackager(packages_root=config.plugins.plugin_local_artifact_path)
    plugin_manager = PluginManager(
        event_bus=event_bus,
        plugin_registry_dir=config.plugins.plugin_registry_path,
        artifact_store=plugin_artifact_store,
        session_factory=database.factory,
        packager=packager
    )

    capability_router = CapabilityRouter(
        device_manager=device_manager,
        plugin_manager=plugin_manager,
        session_factory=database.factory,
        builtin_registry=builtin_registry,
    )

    # ── Wire router into managers for cache invalidation ──
    plugin_manager.set_capability_router(capability_router)
    device_manager.set_capability_router(capability_router)
    llm_router = LLMRouter(default_config=config.llm_provider)
    chat_repo = ChatRepository(session_factory=database.sessionmaker)

    prompt_guard = PromptGuard(config.prompts.guard)

    planning_engine = Planner(
        event_bus=event_bus,
        llm_router=llm_router,
        capability_router=capability_router,
        memory_manager=memory_manager,
        chat_repo=chat_repo,
        prompt_registry=prompt_registry, 
        prompt_tracker=prompt_tracker,      
        prompt_guard=prompt_guard  
    )

    # ── Task store — 
    task_store = TaskRepository(session_factory=database.sessionmaker)

    # ── ExecutorManager — pass executor URLs from config ──
    # Today ExecutorManager() ignores them. Either it needs an
    # `executors=config.executor.executors` kwarg, or the URLs are
    # supposed to be wired through the scheduler. If it's the latter,
    # pass them to ExecutorScheduler instead.


    executor_manager = ExecutorManager(event_bus=event_bus, config=config.executor)

    executor_scheduler = ExecutorScheduler(
        llm_service=llm_router,
        plugin_manager=plugin_manager,
        manager=executor_manager,
        device_manager=device_manager,
    )

    device_selector = DeviceSelector(
        device_manager=device_manager,
        executor_manager=executor_manager,
        capability_router=capability_router,
    )
    capability_resolver = CapabilityResolver(plugin_manager, capability_router)

    # TaskManager now owns task running (capability resolution, device
    # selection, authorization) — the separate TaskRunner was merged into it.
    task_manager = TaskManager(
        store=task_store,
        scheduler=executor_scheduler,
        executor_manager=executor_manager,
        capability_resolver=capability_resolver,
        device_selector=device_selector,
        security_manager=security_manager,
        event_bus=event_bus,
        session_factory=database.factory,
        retry_policy=TaskRetryPolicy(
            default_max_retries=config.tasks.retry_attempts,
            base_delay_seconds=config.tasks.retry_delay,
        ),
    )

    dag_scheduler = DAGScheduler(task_manager=task_manager, event_bus=event_bus)

    push_service = PushService()

    scheduler_service = SchedulerService(
        session_factory=database.sessionmaker,   
        task_manager=task_manager,
        executor=executor_manager,
        device_manager=device_manager,
        push_service=push_service,
    )

    
    for cap in build_schedule_capabilities(
        session_factory=database.sessionmaker,
        scheduler_service=scheduler_service,
    ):
        builtin_registry.register(cap)

    for cap in build_task_capabilities(task_manager):
        builtin_registry.register(cap)

        
    # Executor — registered under the name the router will route to
    builtin_executor = BuiltinExecutor(builtin_registry)
    executor_manager.register(builtin_executor)

    plugin_guard = PluginGuard(session_factory=database.sessionmaker, plugin_manager=plugin_manager, device_manager=device_manager)

    orchestrator = TaskOrchestrator(
        context_manager=context_manager,
        planning_engine=planning_engine,
        agent_manager=agent_manager,
        task_manager=task_manager,
        device_manager=device_manager,
        plugin_artifact_store=plugin_artifact_store,
        plugin_manager=plugin_manager,
        security_manager=security_manager,
        executor_manager=executor_manager,
        capability_router=capability_router,
        event_bus=event_bus,
        capability_resolver=capability_resolver,
        memory_manager=memory_manager,
        dag_scheduler=dag_scheduler,
        chat_repo=chat_repo,
        device_selector=device_selector,
        scheduler_service=scheduler_service,
        push_service=push_service,
        prompt_registry=prompt_registry,
        prompt_tracker=prompt_tracker,
        plugin_guard=plugin_guard,
        prompt_guard=prompt_guard
    )

    capability_router.register_change_listener(
        orchestrator.sweep_stale_capability_memories,
    )

    session_manager = SessionManager(session_factory=database.factory)

    return PAIAppContext(
        orchestrator=orchestrator,
        database=database,
        session_manager=session_manager,
        session=session_manager.factory()
    )


def get_app_context_from_state(app: FastAPI) -> PAIAppContext:
    context = getattr(app.state, "context", None)
    if context is None:
        raise RuntimeError("PAI application context is not configured")
    return context


def get_app_context(request: Request) -> PAIAppContext:
    return get_app_context_from_state(request.app)


def get_ws_app_context(websocket: WebSocket) -> PAIAppContext:
    return get_app_context_from_state(websocket.app)


def get_orchestrator(request: Request) -> TaskOrchestrator:
    return get_app_context(request).orchestrator

async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    ctx = get_app_context(request)
    async with ctx.database.session() as session:
        yield session