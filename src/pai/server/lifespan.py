
from fastapi import FastAPI
from pai.app_context import PAIAppContext
from pai.observability import TraceStore, Tracer, set_tracer
from pai.observability.tracer import get_tracer
from pai.app_context import PAIAppContext
from pai.config.app import Config
from loguru import logger

import asyncio
import contextlib
from contextlib import asynccontextmanager

from pai.observability.logging import shutdown_logging
from pai.observability.resources import ResourceMonitor
from pai.server.shutdown import ShutdownReport, leftover_task_names, run_step


def create_lifespan(config: Config, context: PAIAppContext):
    """Application lifespan manager."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = context

        # ── 1. Tracing ──
        store = TraceStore(persist_dir=config.tracing.persist_dir)
        await store.start()
        set_tracer(Tracer(store=store))
        app.state.tracer = get_tracer()

        # ── 2. Database schema ──
        logger.info("Creating database schema...")
        await context.database.initialize()

        # ── 3. Event bus ──
        #
        # `EventBus` was being constructed but never started. Nothing
        # in `orchestrator.initialize()` calls `.start()`, so the
        # worker task that drains the queue and dispatches to
        # subscribers was never created. Every `event_bus.publish()`
        # succeeded — the event went into the queue — but `_dispatch()`
        # was never called, so subscribers (including the chat status
        # WS route's `_on_status`) never fired.
        #
        # Start it before the orchestrator, so any manager that wants
        # to subscribe during its own `_on_initialize()` has a live bus.
        await context.orchestrator._event_bus.start()


        # ── 4. Status bridges ──
        #
        # Register BEFORE the orchestrator initializes so any events
        # published during manager `_on_initialize()` are caught. No
        # manager currently publishes at init, but this is defensive:
        # moving a publish into an init hook later shouldn't quietly
        # drop events because the bus subscriber list was still empty.
        from pai.orchestration.status_bridge import StatusBridge
        bridge = StatusBridge(
            event_bus=context.orchestrator._event_bus,
            device_manager=context.orchestrator.device_manager,
        )
        await bridge.start()
        app.state.status_bridge = bridge

        # Per-user task event log + publisher (GET /tasks/events). Before the orchestrator so
        # no task event is missed; JetStream falls back to memory if the broker is unreachable.
        from pai.event_bus.task_log import build_task_event_log
        from pai.orchestration.task_event_publisher import TaskEventPublisher
        servers = list(config.event_bus.nats.servers or [config.event_bus.nats.url])
        context.task_events = await build_task_event_log(config.task_events, servers)
        task_publisher = TaskEventPublisher(event_bus=context.orchestrator._event_bus, log=context.task_events)
        await task_publisher.start()
        app.state.task_event_publisher = task_publisher
        logger.info("EventBus started")

            
        # ── 6. Orchestrator final flag ──
        await context.orchestrator.initialize()
        if context.agents is not None:
            await context.agents.initialize()
        if getattr(context, "admin", None) is not None:
            await context.admin.start_history()        # persistent task/DAG event + DAG run history


        logger.info(
            "Startup complete: {} plugins, {} devices, {} executors, "
            "{} routes",
            len(context.orchestrator.plugin_manager.get_catalog()),
            len(await context.orchestrator.device_manager.registry.all()),
            len(context.orchestrator.executor_manager.names()),
            len([r for r in app.routes if hasattr(r, "methods")]),
        )
        logger.info(
            "API server running on {}:{}", config.api.host, config.api.port,
        )
        logger.info("Tracing enabled → {}", config.tracing.persist_dir)

        context.orchestrator.scheduler_service.start()

        # Optional periodic memory/size logging (resource_monitor.enabled; off by default).
        monitor = ResourceMonitor(context, config.resource_monitor)
        await monitor.start()
        app.state.resource_monitor = monitor

        # ── 7. Embedded device agent (optional) ──
        # Runs the PAI DeviceAgent as a supervised background task in
        # this same event loop. Skips itself if PAI_EMBED_AGENT is false
        # or the credentials are not set. Delay in EmbeddedAgent gives
        # uvicorn time to bind its socket before the agent's login.
        # from pai.agent_runtime import EmbeddedAgent
        # embedded_agent = EmbeddedAgent()
        # await embedded_agent.start()
        # app.state.embedded_agent = embedded_agent



        yield

        # ── Shutdown ──
        # Ordered (dependents before dependencies), every step bounded and non-raising, and a
        # final report listing anything that did not stop.
        logger.info("Shutting down Personal AI System...")
        report = ShutdownReport()
        orch = context.orchestrator

        # 0. Tell long-lived streams (SSE / WS / voice) to wrap up.
        context.shutting_down.set()

        # 1. Voice first: each live conversation ends with `session.ended`. It needs the
        #    orchestrator (to persist the interrupted turn), so it goes before it.
        if context.voice is not None:
            await run_step(report, "voice", context.voice.aclose, timeout=5.0)
        if getattr(context, "admin", None) is not None:       # background admin runs (demo DAGs, goals)
            await run_step(report, "admin", context.admin.aclose, timeout=5.0)

        # Agents act through the orchestrator, so they stop before it does.
        if getattr(context, "agents", None) is not None:
            await run_step(report, "agents", context.agents.shutdown, timeout=10.0)

        await run_step(report, "resource_monitor", monitor.stop, timeout=2.0)

        # 2. Clients/producers of work: embedded agent, scheduler, status bridge.
        embedded = getattr(app.state, "embedded_agent", None)
        if embedded is not None:
            await run_step(report, "embedded_agent", embedded.stop, timeout=10.0)
        await run_step(report, "scheduler", orch.scheduler_service.stop, timeout=5.0)
        bridge = getattr(app.state, "status_bridge", None)
        if bridge is not None:
            await run_step(report, "status_bridge", bridge.stop, timeout=2.0)
        publisher = getattr(app.state, "task_event_publisher", None)
        if publisher is not None:
            await run_step(report, "task_event_publisher", publisher.stop, timeout=2.0)
        if context.task_events is not None:
            await run_step(report, "task_event_log", context.task_events.close, timeout=5.0)

        # 3. The orchestrator owns the managers (tasks are interrupted — not cancelled — so
        #    they stay recoverable —, executors, memory, plugins, devices, LLM clients, bus).
        await run_step(report, "orchestrator", orch.shutdown, timeout=30.0)

        # 4. Things the managers were using.
        await run_step(report, "app_session", context.session.close, timeout=2.0)
        await run_step(report, "trace_store", store.stop, timeout=3.0)
        await run_step(report, "database", context.database.close, timeout=5.0)
        context._task_event_subscription = None

        # 5. Let background log writers flush, then report anything still alive.
        await asyncio.sleep(0)
        report.leftover_tasks = leftover_task_names()
        with contextlib.suppress(Exception):
            await logger.complete()
        if report.clean:
            logger.info("Shutdown complete ({})", report.summary())
        else:
            logger.error("Shutdown finished with problems: {}", report.summary())
        app.state.shutdown_report = report

        # 6. Last of all: release log files and writer threads.
        shutdown_logging()

    return lifespan

