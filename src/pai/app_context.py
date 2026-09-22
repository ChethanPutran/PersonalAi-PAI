"""Application context shared by API handlers."""

from dataclasses import dataclass

from fastapi import FastAPI, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from pai.agents.manager import AgentManager
from pai.config import Config
from pai.orchestration.execution_context import ContextManager
from pai.orchestration.task_runner import TaskRunner
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.authorization import AuthorizationManager
from pai.planning.planner import Planner
from pai.storage.database import Database
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
from pai.tasks.store import TaskStore


@dataclass
class PAIAppContext:
    """Runtime objects owned by the FastAPI application."""

    orchestrator: TaskOrchestrator
    database: Database = None
    session_manager: SessionManager = None
    session: AsyncSession = None             
    user_id: str = None
    session_id: str = None
    source_device_id: str = None


def create_app_context(config: Config) -> PAIAppContext:
    """Create the application context with injectable runtime dependencies."""

    event_bus = EventBus()
    context_manager = ContextManager()
    memory_manager = MemoryManager()
    device_manager = DeviceManager()
    capability_router = CapabilityRouter()
    llm_router = LLMRouter()
    planning_engine = Planner(llm_router, capability_router=capability_router)
    task_store = TaskStore()
    task_manager = TaskManager()
    agent_manager = AgentManager()
    executor_manager = ExecutorManager()
    plugin_registry = PluginRegistry()

    database = Database(url=config.database.url, echo=config.debug)
    plugin_manager = PluginManager(
        plugin_registry,
        session_factory=database.factory
    )

    security_manager = SecurityManager(
        session_factory=database.factory
    )
    authorization_manager = AuthorizationManager(
        security_manager=security_manager,
        plugin_manager=plugin_manager,
        device_manager=device_manager,
    )
    device_selector = DeviceSelector(
        device_manager=device_manager,
        executor_manager=executor_manager,
    )
    capability_resolver = CapabilityResolver(plugin_manager, capability_router)

    task_runner = TaskRunner(
        task_manager=task_manager,
        capability_resolver=capability_resolver,
        device_selector=device_selector,
        authorization_manager=authorization_manager,
        executor_manager=executor_manager,
        event_bus=event_bus,
    )

    executor_scheduler = ExecutorScheduler()
    dag_scheduler = DAGScheduler(task_runner=task_runner)

    orchestrator = TaskOrchestrator(
        context_manager=context_manager,
        planning_engine=planning_engine,
        agent_manager=agent_manager,
        task_manager=task_manager,
        device_manager=device_manager,
        plugin_manager=plugin_manager,
        security_manager=security_manager,
        executor_manager=executor_manager,
        executor_scheduler=executor_scheduler,
        capability_router=capability_router,
        event_bus=event_bus,
        memory_manager=memory_manager,
        dag_scheduler=dag_scheduler,
    )

    return PAIAppContext(
        orchestrator=orchestrator,
        database=database
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