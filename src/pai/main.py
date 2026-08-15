"""Main entry point for the Personal AI system."""

import asyncio
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pai.app_context import PAIAppContext, create_app_context, get_app_context, get_ws_app_context
from pai.config import config
from pai.api.routes import agent_routes, plugin_routes, memory_routes
from pai.api import plugins as plugins_v1
from pai.api.middleware.logging import LoggingMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from time import time

REQUEST_COUNT = None
REQUEST_LATENCY = None


def create_lifespan(context: PAIAppContext):
    """Application lifespan manager."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = context
        logger.info("Starting Personal AI System...")
        await context.kernel.start()
        logger.info(f"API server running on {config.api_host}:{config.api_port}")
        yield
        logger.info("Shutting down Personal AI System...")
        await context.kernel.shutdown()

    return lifespan


def create_app(context: PAIAppContext) -> FastAPI:
    """Create the FastAPI application with an explicit runtime context."""
    app = FastAPI(
        title="Personal AI System",
        description="Context-Aware Autonomous Personal Agent System",
        version="1.0.0",
        lifespan=create_lifespan(context),
    )
    app.state.context = context

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(LoggingMiddleware)
    # app.add_middleware(AuthMiddleware)

    app.include_router(agent_routes.router, prefix="/api/agents", tags=["agents"])
    app.include_router(plugin_routes.router, prefix="/api/plugins", tags=["plugins"])
    # v1 compatibility routes for plugins (persisted configs, enable/disable)
    app.include_router(plugins_v1.router)
    # File browsing endpoints (v1)
    from pai.api import files as files_v1
    app.include_router(files_v1.router)
    from pai.api import preferences as preferences_v1
    app.include_router(preferences_v1.router)
    from pai.api import devices as devices_v1
    app.include_router(devices_v1.router)
    from pai.api import builds as builds_v1
    app.include_router(builds_v1.router)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])

    @app.get("/")
    async def root(context: PAIAppContext = Depends(get_app_context)):
        """Root endpoint."""
        return {
            "name": "Personal AI System",
            "version": "1.0.0",
            "status": "running",
            "kernel": context.kernel.get_status(),
        }

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        logger.info("WebSocket client connected")
        context = get_ws_app_context(websocket)

        try:
            while True:
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    logger.info("WebSocket client disconnected")
                    break  # exit the loop, will be caught by outer try/except
                except Exception as e:
                    logger.error(f"Failed to receive JSON: {e}")
                    continue  # skip malformed messages, keep connection alive

                logger.info(f"Received WebSocket message: {data}")

                # Process the message
                try:
                    msg_type = data.get("type")
                    if msg_type == "goal":
                        goal = data.get("goal")
                        if not goal:
                            await websocket.send_json({"type": "error", "message": "Missing 'goal' field"})
                            continue
                        result = await context.kernel.process_goal(goal, data.get("context", {}))
                        await websocket.send_json({"type": "result", "data": result})

                    elif msg_type == "register_device":
                        payload = data.get("payload", {})
                        pm = context.kernel.plugin_manager
                        rec = await pm.register_device(
                            user_id=payload.get("user_id"),
                            device_id=payload.get("device_id"),
                            device_name=payload.get("device_name"),
                            device_type=payload.get("device_type"),
                            platform=payload.get("platform"),
                            capabilities=payload.get("capabilities"),
                            config=payload.get("config"),
                        )
                        if rec is None:
                            await websocket.send_json({"type": "device_registered", "status": "error"})
                        else:
                            device = await pm.get_device(payload.get("device_id"))
                            await websocket.send_json({"type": "device_registered", "status": "ok", "device": device})

                    elif msg_type == "context_update":
                        ctx = data.get("context", {}) or {}
                        await context.kernel.context_manager.update(ctx)
                        await websocket.send_json({"type": "context_updated", "status": "ok"})

                    elif msg_type == "subscribe":
                        event_type = data.get("event")
                        if not event_type:
                            await websocket.send_json({"type": "error", "message": "Missing 'event' field"})
                            continue

                        async def handler(evt_type: str, evt_data: dict):
                            try:
                                await websocket.send_json({"type": "event", "event": evt_type, "data": evt_data})
                            except Exception as e:
                                logger.error(f"Failed to send event: {e}")

                        await context.kernel.event_bus.subscribe(event_type, handler)
                        await websocket.send_json({"type": "subscribed", "event": event_type})

                    else:
                        await websocket.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    try:
                        await websocket.send_json({"type": "error", "message": f"Internal error: {str(e)}"})
                    except:
                        pass

        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected (outer)")
        except Exception as e:
            logger.error(f"Unexpected WebSocket error: {e}", exc_info=True)
        finally:
            logger.info("WebSocket connection closed")
    return app


# Create the application context and FastAPI app
default_context = create_app_context(config)

# Create the FastAPI application with the default context
app = create_app(default_context)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "pai.main:app",
        host=config.api_host,
        port=config.api_port,
        reload=config.debug,
        log_level="info",
    )