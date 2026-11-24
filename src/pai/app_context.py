"""Application context shared by API handlers."""

import asyncio

from loguru import logger
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import FastAPI, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from pai.agent.builtins import build_agent_capabilities
from pai.agent.runtime import AgentRuntime
from pai.agent.sources import OrchestratorCapabilitySource, RouterLLMClient, guard_filters
from pai.agents.manager import AgentManager
from pai.config.app import Config
from pai.config.models.memory import KnowledgeGraphConfig
from pai.executors.builtin import BuiltinExecutor
from pai.executors.builtin import BuiltinExecutor
from pai.notifications.push_service import PushService
from pai.orchestration.builtin_capabilities import BuiltinCapabilityRegistry
from pai.orchestration.builtins.schedule import build_schedule_capabilities
from pai.orchestration.builtins.tasks import build_task_capabilities
from pai.scheduler.validation import ScheduleValidator
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
from pai.api.admin.history import AdminHistory
from pai.api.admin.service import AdminService
from pai.voice.runner import OrchestratorTurnRunner
from pai.voice.service import VoiceService
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
from pai.config.model_registry import ModelRegistry, check_configured, load_registry
from pai.config.settings_admin import BASE_FILE, OVERRIDES_FILE
from pai.llm.cost import CostTracker
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
    voice: Optional[VoiceService] = None
    admin: Optional[AdminService] = None
    agents: Optional[AgentRuntime] = None          # long-running ReAct agents (pai.agent)
    model_registry: Optional[ModelRegistry] = None  # config/models.yaml (capabilities + prices)
    model_roles: dict = field(default_factory=dict)  # role -> configured model id (default/planner/chat/agent/stt/tts)
    # Settings editor (admin panel): the config this server started with, as plain data, and where edits are saved.
    running_settings: dict = field(default_factory=dict)
    settings_base_path: str = BASE_FILE
    settings_overrides_path: str = OVERRIDES_FILE
    settings_loader: Optional[Callable[[], Any]] = None     # how a reload reads the saved config (default: Config())
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    source_device_id: Optional[str] = None
    task_events: Optional[object] = None          # TaskEventLog (per-user SSE replay log)
    _task_event_subscription: Optional[object] = None  # subscription handle for cleanup
    # Set at the start of shutdown so long-lived streams (SSE / WS / voice) end promptly
    # instead of holding the server open until the force-kill timeout.
    shutting_down: asyncio.Event = field(default_factory=asyncio.Event)


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
    # SecurityManager is built before these exist; it refuses to initialize without them.
    security_manager.plugin_manager = plugin_manager
    security_manager.device_manager = device_manager
    device_manager.set_capability_router(capability_router)
    running_settings = config.model_dump(mode="json")      # before anything below fills in defaults at runtime

    # Model registry (config/models.yaml): token prices for LLM cost, STT/TTS prices for voice cost, and a startup check
    # that the configured models exist and can do their role. `llm_pricing` in config.yaml still overrides a price.
    model_registry = load_registry(config.models_file)
    for note in model_registry.warnings:
        logger.warning("models registry: {}", note)
    pricing = {**model_registry.llm_pricing(), **config.llm_pricing}
    air = config.voice.airouter
    if air.stt_cost_per_minute_usd is None:
        air.stt_cost_per_minute_usd = model_registry.stt_price(air.stt_model)
    if air.tts_cost_per_1k_chars_usd is None:
        air.tts_cost_per_1k_chars_usd = model_registry.tts_price(air.tts_model)
    model_roles = {"default": str(config.llm_provider.model_name), **{r: str(m) for r, m in config.llm_routes.items()}}
    for note in check_configured(model_registry, model_roles, stt=air.stt_model, tts=air.tts_model):
        logger.warning("models registry: {}", note)

    cost_tracker = (CostTracker(ledger_path=config.llm_cost.ledger_path,
                                   initial_credits_usd=config.llm_cost.initial_credits_usd,
                                   credits_path=config.llm_cost.credits_path) if config.llm_cost.track else None)
    llm_router = LLMRouter(default_config=config.llm_provider, pricing=pricing,
                           cost_tracker=cost_tracker)
    for _role, _model in (config.llm_routes or {}).items():
        llm_router.register_route(_role, llm_router.default_route.provider, _model)
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

    # Persistent admin history (events + finished DAG runs) kept for the task cleanup window.
    admin_history = None
    if config.admin.enabled and config.admin.history_enabled:
        admin_history = AdminHistory(config.admin.history_dir, retention_days=config.admin.history_retention_days)

    dag_scheduler = DAGScheduler(
        task_manager=task_manager, event_bus=event_bus,
        history_size=config.admin.dag_history_size, history_store=admin_history,
    )

    push_service = PushService()

    # One validation service, shared by the REST routes, the chat builtin and the scheduler.
    schedule_validator = ScheduleValidator(
        capability_router=capability_router,
        device_manager=device_manager,
        security_manager=security_manager,
    )
    scheduler_service = SchedulerService(
        session_factory=database.sessionmaker,   
        task_manager=task_manager,
        executor=executor_manager,
        device_manager=device_manager,
        push_service=push_service,
        validator=schedule_validator,
        audit_logger=security_manager.audit_logger,
    )

    
    for cap in build_schedule_capabilities(
        session_factory=database.sessionmaker,
        scheduler_service=scheduler_service,
        validator=schedule_validator,
    ):
        builtin_registry.register(cap)

    for cap in build_task_capabilities(task_manager):
        builtin_registry.register(cap)

    # Device-free admin.test.* capabilities (sleep/echo/fail/stream) — dev/test environments only.
    if config.admin.test_capabilities:
        from pai.api.admin.test_capabilities import build_admin_test_capabilities
        for cap in build_admin_test_capabilities(task_manager):
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

    # Long-running ReAct agents. Their tools are the user's own capabilities, run through
    # orchestrator.execute_action (so authorization/confirmation/audit are unchanged); their LLM
    # calls and everything they say are screened by the same PromptGuard as chat.
    observation_filter, response_filter = guard_filters(prompt_guard)
    agent_runtime = AgentRuntime(
        event_bus=event_bus,
        capability_source=OrchestratorCapabilitySource(orchestrator),
        llm=RouterLLMClient(llm_router, route="agent"),
        prompt=lambda: prompt_registry.get("agent.react"),
        observation_filter=observation_filter,
        response_filter=response_filter,
    )
    for cap in build_agent_capabilities(agent_runtime):     # lets the planner start/steer agents
        builtin_registry.register(cap)

    session_manager = SessionManager(session_factory=database.factory)

    voice = VoiceService.build(config.voice, OrchestratorTurnRunner(orchestrator, database), cost_tracker=cost_tracker)
    return PAIAppContext(
        orchestrator=orchestrator,
        database=database,
        session_manager=session_manager,
        session=session_manager.factory(),
        voice=voice,
        admin=AdminService(orchestrator, config.admin, voice=voice, history=admin_history) if config.admin.enabled else None,
        agents=agent_runtime,
        model_registry=model_registry,
        running_settings=running_settings,
        model_roles={**model_roles, "stt": air.stt_model or "", "tts": air.tts_model or ""},
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