"""HTTP request logging middleware."""
from __future__ import annotations

import asyncio
import time

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class LoggingMiddleware:
    """
    Per-request access logging.

    Every HTTP request emits exactly one INFO record with structured
    extras (method, path, status, duration_s, client) tagged with
    ``http_access=True``. The loguru sinks in
    ``pai.logging_config`` route these records to ``logs/http_access.log``
    and keep them out of the terminal + ``app.log``.

    Websocket and lifespan scopes pass through untouched — Starlette's
    own protocols own those.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        query = scope.get("query_string", b"").decode(errors="replace")
        full_path = f"{path}?{query}" if query else path

        client = scope.get("client")
        client_host = client[0] if client else "-"

        start = time.perf_counter()
        status_code = 0
        cancelled = False

        async def _send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)

        except asyncio.CancelledError:
            # Shutdown interrupted an in-flight request. Expected
            # behaviour — log at debug, keep the exception flowing.
            cancelled = True
            raise

        except Exception:
            # Real error — log the traceback so it lands in app.log.
            # Starlette's error handler will emit the 500 response.
            logger.exception("{} {} — unhandled exception", method, full_path)
            raise

        finally:
            duration_s = time.perf_counter() - start
            if status_code == 0:
                # Response never started: 499 for cancellation (nginx
                # convention), 500 for a genuine error before send.
                status_code = 499 if cancelled else 500

            logger.bind(
                http_access=True,
                method=method,
                path=full_path,
                status=status_code,
                duration_s=duration_s,
                client=client_host,
            ).info("{} {} - {:.3f}s", method, full_path, duration_s)