"""HTTP request logging middleware.

Cancellations during shutdown are normal — uvicorn cancels in-flight
ASGI tasks after the graceful period expires. Treating them as errors
produces scary tracebacks for behaviour that is actually correct.
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger
from starlette.types import ASGIApp, Receive, Scope, Send


class LoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Non-HTTP scopes (websocket, lifespan) pass through untouched.
        # Lifespan in particular must not be wrapped in timing logic —
        # Starlette's lifespan handler has its own protocol.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        start = time.perf_counter()      # monotonic — safe under clock changes

        try:
            await self.app(scope, receive, send)

        except asyncio.CancelledError:
            # Shutdown signal interrupted this request. Re-raise so the
            # parent task group can clean up correctly — but log at debug,
            # not error, because this is expected shutdown behaviour.
            logger.debug("{} {} — cancelled during shutdown", method, path)
            raise

        except Exception:
            # A real application error. Log the traceback, then re-raise
            # so Starlette's error handler can produce the 500 response.
            logger.exception("{} {} — unhandled exception", method, path)
            raise

        finally:
            # Always log duration — including on cancellation and error.
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info("{} {} - {:.3f}s", method, path, duration_ms / 1000)