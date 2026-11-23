"""Main entry point for the Personal AI system."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pai.api.middleware.auth import AuthMiddleware
from pai.api.middleware.logging import LoggingMiddleware
from pai.api.routes import auth, chat, devices, plugins, system, tasks
from pai.api.routes.schedules import router as schedules_router
from pai.api.routes.push_tokens import router as push_tokens_router

from pai.api.routes import observability as observability_routes
from pai.app_context import PAIAppContext
from pai.config.app import Config

from pai.server.lifespan import create_lifespan


def create_app(config: Config, context: PAIAppContext) -> FastAPI:
    app = FastAPI(
        title=config.app.name,
        description=config.app.description,
        version=config.app.version,
        lifespan=create_lifespan(config, context),
    )

    app.state.context = context

    # Middleware
    app.add_middleware(AuthMiddleware)

    # CORS middleware should be added after AuthMiddleware to ensure that authentication 
    # headers are processed before CORS checks. This allows for proper handling of preflight
    #  requests and ensures that authenticated requests are not blocked by CORS policies.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api.cors.allow_origins,
        allow_credentials=config.api.cors.allow_credentials,
        allow_methods=config.api.cors.allow_methods,
        allow_headers=config.api.cors.allow_headers,
    )

    # Logging middleware should be added after CORS to ensure that logs capture the final 
    # response status, including any CORS headers.
    app.add_middleware(LoggingMiddleware)

    # Routers
    app.include_router(auth.router, prefix=config.api.prefix)
    app.include_router(devices.router, prefix=config.api.prefix)
    app.include_router(plugins.router, prefix=config.api.prefix)
    app.include_router(tasks.router, prefix=config.api.prefix)
    app.include_router(chat.router, prefix=config.api.prefix)
    app.include_router(system.router, prefix=config.api.prefix)
    app.include_router(observability_routes.router, prefix=config.api.prefix)
    app.include_router(schedules_router, prefix=config.api.prefix)
    app.include_router(push_tokens_router, prefix=config.api.prefix)

    return app

