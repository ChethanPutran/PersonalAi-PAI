
from fastapi import FastAPI
from pai.app_context import PAIAppContext
from pai.observability import TraceStore, Tracer, set_tracer
from pai.observability.tracer import get_tracer
from pai.app_context import PAIAppContext
from pai.config.app import Config
from loguru import logger

import asyncio
from contextlib import asynccontextmanager


def create_lifespan(config: Config, context: PAIAppContext):
    """Application lifespan manager."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = context

        # ── 1. Tracing ──
        store = TraceStore(persist_dir="data/traces")
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
        logger.info("EventBus started")

            
        # ── 6. Orchestrator final flag ──
        await context.orchestrator.initialize()


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
        logger.info("Tracing enabled → data/traces")

        context.orchestrator.scheduler_service.start()

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
        # Unsubscribe bridges before tearing down the bus so no dispatch
        # races the shutdown (a bridge handler firing during bus.shutdown
        # would try to publish or send through a half-closed context).
        for sub_id in getattr(app.state, "bridge_subscriptions", []):
            try:
                await context.orchestrator.event_bus.unsubscribe(sub_id)
            except Exception:
                pass

        # Stop the embedded agent BEFORE the scheduler — the agent is a
        # client of the backend and should disconnect before the server
        # side stops firing notifications through its WS / push paths.
        if getattr(app.state, "embedded_agent", None) is not None:
            try:
                await asyncio.wait_for(
                    app.state.embedded_agent.stop(),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("embedded_agent stop timed out")
                
        await context.orchestrator.scheduler_service.stop()
        logger.info("Shutting down Personal AI System...")

        async def _step(name: str, coro, timeout: float = 5.0):
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.error(
                    "⏱ shutdown step '{}' timed out after {}s", name, timeout,
                )
            except Exception as exc:
                logger.warning("shutdown step '{}' failed: {}", name, exc)

        # 1. Orchestrator owns its managers (executors, memory, plugins,
        #    device, capability_router, etc.) and drains its own tracker.
        #    Give it more time — it has the most to do.
        await _step("orchestrator", context.orchestrator.shutdown(), timeout=10.0)

        # 2. Trace store last among the app's services — nothing else
        #    is still logging once the orchestrator is down.
        await _step("trace_store", store.stop(), timeout=2.0)

        # 3. Database last — its connections are the session_factory
        #    that the managers above used.
        await _step("database", context.database.close(), timeout=3.0)


        # 4. Unsubscribe from task events so the bus can drain and stop.
        if context._task_event_subscription:
            context._task_event_subscription = None

        logger.info("Shutdown complete")

    return lifespan

