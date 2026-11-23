
"""Application composition root."""
from __future__ import annotations

from fastapi import FastAPI

from pai.app_context import create_app_context
from pai.config.app import Config
from pai.config.loader import load_config
from pai.observability.logging import configure_logging
from pai.server.application import create_app


def create_application(config: Config | None = None) -> FastAPI:
    """Build the complete PAI application."""

    # 1. Load and validate configuration.
    config = config or load_config()

    # 2. Initialize logging before creating application components.
    configure_logging(config.logging)

    # 3. Construct runtime dependencies.
    context = create_app_context(config)

    # 4. Construct FastAPI application.
    app = create_app(config, context)

    return app
