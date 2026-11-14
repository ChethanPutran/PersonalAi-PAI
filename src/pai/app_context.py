"""Application context shared by API handlers."""

from dataclasses import dataclass
import os
import os
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from pai.agents.manager import AgentManager
from pai.config import Config
from pai.executors.builtin import BuiltinExecutor
from pai.executors.builtin import BuiltinExecutor
from pai.notifications.push_service import PushService
from pai.orchestration.builtin_capabilities import BuiltinCapabilityRegistry
from pai.orchestration.builtins.schedule import build_schedule_capabilities
from pai.orchestration.builtins.tasks import build_task_capabilities
from pai.orchestration.builtins.tasks import build_task_capabilities
from pai.orchestration.builtins.schedule import build_schedule_capabilities
from pai.orchestration.execution_context import ContextManager
from pai.orchestration.task_runner import TaskRunner
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.authorization import AuthorizationManager
from pai.planning.planner import Planner
from pai.prompts.registry import PromptRegistry
from pai.prompts.tracker import PromptVersionTracker
from pai.storage.database import Database
from pai.storage.repositories.chat import ChatRepository
from pai.storage.repositories.task import TaskRepository
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

    # ── Event bus — wire NATS config through if EventBus supports it ──
    # If EventBus has no NATS kwargs today, either add them or remove
    # the config fields; leaving them dangling in config is a trap.
    event_bus = EventBus(
        nats_url=config.event_bus.nats.url,
        max_queue_size=config.event_bus.max_queue_size,
        num_workers=config.event_bus.num_workers,
    )

    database = Database(
        url=config.database.url,
        echo=config.database.echo,
    )

    # Parse overrides from env
    overrides = {}
    for pair in (os.getenv("PROMPTS_OVERRIDES") or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            overrides[k.strip()] = v.strip()

    prompt_registry = PromptRegistry(
        root=os.getenv("PROMPTS_ROOT", "./prompts"),
        frozen=os.getenv("PROMPTS_FROZEN", "true").lower() == "true",
        override_versions=overrides,
    )

    prompt_tracker = PromptVersionTracker(
        session_factory=database.sessionmaker,
        enabled=os.getenv("PROMPTS_TRACK_USAGE", "true").lower() == "true",
    )

    # Registry
    builtin_registry = BuiltinCapabilityRegistry()

    # ── Context manager — honor context_history_size ──
    context_manager = ContextManager(
        max_history=config.memory.context_history_size,
    )

    # ── Memory — fix the _url / _path parameter mismatch ──
    memory_manager = MemoryManager(
        event_bus=event_bus,
        user_id="default",
        knowledge_graph_enabled=config.memory.knowledge_graph_enabled,
        vector_store_enabled=config.memory.vector_store_enabled,
        short_term_size=config.memory.short_term_size,
        short_term_ttl=config.memory.short_term_ttl,
        # MemoryManager expects *_path, config exposes *_url — adapt here.
        long_term_db_url=config.memory.long_term_db_url,
        episodic_db_url=config.memory.episodic_db_url,
        vector_store_path=config.memory.vector_store_path,
        procedural_path=config.memory.procedural_path,
        embedding_model=config.memory.embedding_model,
        # Knowledge-graph credentials flow from NEO4J_* env vars:
        #   .env → Config.memory → here → KnowledgeGraph
        knowledge_graph_uri=config.memory.neo4j_url,
        knowledge_graph_user=config.memory.neo4j_user,
        knowledge_graph_password=config.memory.neo4j_password,
    )

    device_manager = DeviceManager(session_factory=database.factory,
                                   event_bus=event_bus
                                   )
    agent_manager = AgentManager(event_bus=event_bus)
    security_manager = SecurityManager(session_factory=database.factory,event_bus=event_bus)

    plugin_registry = PluginRegistry()
    plugin_manager = PluginManager(
        event_bus=event_bus,
        plugin_registry=plugin_registry,
        session_factory=database.factory,
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
    planning_engine = Planner(
        event_bus=event_bus,
        llm_router=llm_router,
        capability_router=capability_router,
        memory_manager=memory_manager,
        chat_repo=chat_repo,
        prompt_registry=prompt_registry, 
        prompt_tracker=prompt_tracker,        
    )

    # ── Task store — in-memory for now, DB-backed later ──
    # --- dev: in-memory ---
    # task_store = TaskStore()

    # --- production: DB-backed ---
    task_store = TaskRepository(session_factory=database.sessionmaker)

    # ── ExecutorManager — pass executor URLs from config ──
    # Today ExecutorManager() ignores them. Either it needs an
    # `executors=config.executor.executors` kwarg, or the URLs are
    # supposed to be wired through the scheduler. If it's the latter,
    # pass them to ExecutorScheduler instead.


    executor_manager = ExecutorManager(event_bus=event_bus)


    executor_scheduler = ExecutorScheduler(
        llm_service=llm_router,
        plugin_manager=plugin_manager,
        manager=executor_manager,
        device_manager=device_manager,
    )

    authorization_manager = AuthorizationManager(
        event_bus=event_bus,
        security_manager=security_manager,
        plugin_manager=plugin_manager,
        device_manager=device_manager,
    )
    device_selector = DeviceSelector(
        device_manager=device_manager,
        executor_manager=executor_manager,
        capability_router=capability_router,
    )
    capability_resolver = CapabilityResolver(plugin_manager, capability_router)

    task_manager = TaskManager(
        store=task_store,
        scheduler=executor_scheduler,
        event_bus=event_bus,
        executor_manager=executor_manager,
        session_factory=database.factory,
    )

    task_runner = TaskRunner(
        task_manager=task_manager,
        capability_resolver=capability_resolver,
        device_selector=device_selector,
        authorization_manager=authorization_manager,
        executor_manager=executor_manager,
        event_bus=event_bus,
    )

    dag_scheduler = DAGScheduler(task_runner=task_runner, event_bus=event_bus)

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


    orchestrator = TaskOrchestrator(
        context_manager=context_manager,
        planning_engine=planning_engine,
        agent_manager=agent_manager,
        task_manager=task_manager,
        device_manager=device_manager,
        plugin_manager=plugin_manager,
        security_manager=security_manager,
        executor_manager=executor_manager,
        capability_router=capability_router,
        event_bus=event_bus,
        task_runner=task_runner,
        capability_resolver=capability_resolver,
        memory_manager=memory_manager,
        dag_scheduler=dag_scheduler,
        chat_repo=chat_repo,
        authorization_manager=authorization_manager,
        device_selector=device_selector,
        scheduler_service=scheduler_service,
        push_service=push_service,
        prompt_registry=prompt_registry,
        prompt_tracker=prompt_tracker
    )

    capability_router.register_change_listener(
        orchestrator.sweep_stale_capability_memories,
    )

    session_manager = SessionManager(session_factory=database.factory)

    return PAIAppContext(
        orchestrator=orchestrator,
        database=database,
        session_manager=session_manager,
        session=session_manager.factory(),   # see note below
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