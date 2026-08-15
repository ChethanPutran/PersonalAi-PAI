from starlette.types import ASGIApp, Scope, Receive, Send
from loguru import logger
import time

class LoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Skip WebSocket – let it pass through untouched
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        # Only log HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.time()
        await self.app(scope, receive, send)
        duration = time.time() - start
        logger.info(f"{scope['method']} {scope['path']} - {duration:.3f}s")