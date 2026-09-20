"""Application context shared by API handlers."""

from dataclasses import dataclass

from fastapi import FastAPI, Request, WebSocket

from pai.config import Config
from pai.orchestration.orchestrator import AIKernel


@dataclass
class PAIAppContext:
    """Runtime objects owned by the FastAPI application."""

    kernel: AIKernel


def create_app_context(config: Config) -> PAIAppContext:
    """Create the application context with injectable runtime dependencies."""

    return PAIAppContext(AIKernel(config=config))


def get_app_context_from_state(app: FastAPI) -> PAIAppContext:
    """Return the application context attached to FastAPI state."""

    context = getattr(app.state, "context", None)
    if context is None:
        raise RuntimeError("PAI application context is not configured")
    return context


def get_app_context(request: Request) -> PAIAppContext:
    """FastAPI dependency for HTTP routes."""

    return get_app_context_from_state(request.app)


def get_ws_app_context(websocket: WebSocket) -> PAIAppContext:
    """Context accessor for WebSocket routes."""

    return get_app_context_from_state(websocket.app)


def get_kernel(request: Request) -> AIKernel:
    """FastAPI dependency for routes that only need the kernel."""

    return get_app_context(request).kernel
