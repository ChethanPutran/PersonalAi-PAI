"""Main entry point for the Personal AI system."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pai.api.middleware.auth import AuthMiddleware
from pai.api.middleware.logging import LoggingMiddleware
from pai.api.routes import account as account_routes
from pai.api.routes import agents as agents_routes
from pai.api.routes import documents as documents_routes
from pai.api.routes import auth, chat, devices, plugins, system, tasks
from pai.api.routes import consents as consents_routes
from pai.api.routes.schedules import router as schedules_router
from pai.api.routes.push_tokens import router as push_tokens_router

from pai.api.routes import observability as observability_routes
from pai.api.routes import voice as voice_routes
from pai.api.routes import admin as admin_routes
from pai.api.admin.static_app import AdminStaticFiles, ADMIN_STATIC_DIR
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

    # Malformed /schedules bodies get the same 422 shape as our own schedule validation.
    from fastapi.exceptions import RequestValidationError
    from pai.api.routes.schedules import request_validation_handler
    app.add_exception_handler(RequestValidationError, request_validation_handler)

    # Routers
    app.include_router(auth.router, prefix=config.api.prefix)
    app.include_router(account_routes.router, prefix=config.api.prefix)
    app.include_router(devices.router, prefix=config.api.prefix)
    app.include_router(plugins.router, prefix=config.api.prefix)
    app.include_router(tasks.router, prefix=config.api.prefix)
    app.include_router(consents_routes.router, prefix=config.api.prefix)
    app.include_router(chat.router, prefix=config.api.prefix)
    app.include_router(voice_routes.router, prefix=config.api.prefix)
    app.include_router(agents_routes.router, prefix=config.api.prefix)
    if config.admin.enabled:
        app.include_router(admin_routes.router, prefix=config.api.prefix)
        # JS/CSS modules for the new tabs of the EXISTING admin page (GET {prefix}/system/admin).
        # The files are public (no data); every API call they make needs an admin bearer token.
        app.mount("/admin/static", AdminStaticFiles(directory=str(ADMIN_STATIC_DIR)), name="admin-static")
    app.include_router(system.router, prefix=config.api.prefix)
    app.include_router(observability_routes.router, prefix=config.api.prefix)
    app.include_router(documents_routes.router, prefix=config.api.prefix)
    app.include_router(schedules_router, prefix=config.api.prefix)
    app.include_router(push_tokens_router, prefix=config.api.prefix)

    # Short link to the user's Settings page (a static shell; its API calls need a login).
    from fastapi.responses import RedirectResponse

    @app.get("/settings", include_in_schema=False)
    async def _settings_alias() -> RedirectResponse:
        return RedirectResponse(f"{config.api.prefix}/account/settings")

    return app

