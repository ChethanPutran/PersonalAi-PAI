"""Main entry point for the Personal AI system."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pai.api.middleware.auth import AuthMiddleware
from pai.api.middleware.logging import LoggingMiddleware
from pai.api.routes import auth, chat, devices, plugins, system, tasks
from pai.api.routes import observability as observability_routes
from pai.app_context import (
    PAIAppContext,
    create_app_context,
    get_app_context,
)
from pathlib import Path
from pai.logging_config import configure_logging
from pai.config import config
from pai.observability import TraceStore, Tracer, set_tracer
from pai.observability.tracer import get_tracer


# ── Configure logging BEFORE anything else that might log ──
configure_logging(
    log_dir=Path("logs"),
    level="DEBUG" if config.debug else "INFO",
    json_access=False,     # True → logs/http_access.jsonl
)

def create_lifespan(context: PAIAppContext):
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
            "API server running on {}:{}", config.api_host, config.api_port,
        )
        logger.info("Tracing enabled → data/traces")

        yield

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

        logger.info("Shutdown complete")

    return lifespan


def create_app(context: PAIAppContext) -> FastAPI:
    app = FastAPI(
        title="Personal AI System",
        description="Context-Aware Autonomous Personal Agent System",
        version="1.0.0",
        lifespan=create_lifespan(context),
    )
    app.state.context = context

    # Order matters: AuthMiddleware wraps CORS so that 401/403
    # responses still carry CORS headers. Add it FIRST.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(LoggingMiddleware)

    # Routers
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(devices.router, prefix="/api/v1")
    app.include_router(plugins.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(observability_routes.router, prefix="/api/v1")

    @app.get("/")
    async def root(context: PAIAppContext = Depends(get_app_context)):
        return {
            "name": "Personal AI System",
            "version": "1.0.0",
            "status": "running",
            "user_id": context.user_id,
            "session_id": context.session_id,
            "request_id": context.request_id,
            "source_device_id": context.source_device_id,
            "kernel": context.orchestrator.get_status(user_id=context.user_id),
        }

    return app


default_context = create_app_context(config)
app = create_app(default_context)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pai.main:app",
        host=config.api_host,
        port=config.api_port,
        reload=config.debug,
        reload_dirs=["src"],
        reload_includes=[".env"],          # env edits now trigger reload
        timeout_graceful_shutdown=5,
        log_level="info",
    )