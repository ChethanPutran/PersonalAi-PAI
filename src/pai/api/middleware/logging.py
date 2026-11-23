
"""HTTP request logging middleware."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qsl, urlencode

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send


_SENSITIVE_QUERY_PARAMS = {
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "authorization",
}


def _sanitize_query_string(query_string: bytes) -> str:
    """Remove sensitive values from query parameters."""

    if not query_string:
        return ""

    query = query_string.decode(errors="replace")

    try:
        params = parse_qsl(
            query,
            keep_blank_values=True,
        )

        sanitized = [
            (
                key,
                "[REDACTED]"
                if key.lower() in _SENSITIVE_QUERY_PARAMS
                else value,
            )
            for key, value in params
        ]

        return urlencode(sanitized)

    except Exception:
        # Avoid exposing an unsanitized query if parsing fails.
        return "[QUERY_REDACTED]"


class LoggingMiddleware:
    """Per-request HTTP access logging."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")

        query = _sanitize_query_string(
            scope.get("query_string", b"")
        )

        full_path = f"{path}?{query}" if query else path

        client = scope.get("client")
        client_host = client[0] if client else "-"

        start_time = time.perf_counter()

        status_code = 0
        cancelled = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message["status"]

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)

        except asyncio.CancelledError:
            cancelled = True
            raise

        except Exception:
            # Preserve the original exception.
            logger.bind(
                http_access=False,
                method=method,
                path=full_path,
                client=client_host,
            ).exception(
                "Unhandled exception while processing HTTP request"
            )
            raise

        finally:
            duration_s = time.perf_counter() - start_time

            if status_code == 0:
                status_code = 499 if cancelled else 500

            try:
                logger.bind(
                    http_access=True,
                    method=method,
                    path=full_path,
                    status=status_code,
                    duration_s=duration_s,
                    client=client_host,
                ).info(
                    "{} {} - {:.3f}s",
                    method,
                    full_path,
                    duration_s,
                )

            except Exception:
                # Access logging must not replace an application exception.
                # The application should still complete its request lifecycle.
                pass
