"""Main entry point for the Personal AI system."""

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pai.api.routes import chat, devices, plugins, tasks
from pai.app_context import PAIAppContext, create_app_context, get_app_context, get_ws_app_context
from pai.config import config
from pai.api.middleware.logging import LoggingMiddleware
from pai.api.middleware.auth import AuthMiddleware

REQUEST_COUNT = None
REQUEST_LATENCY = None


def create_lifespan(context: PAIAppContext):
    """Application lifespan manager."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.context = context
        logger.info("Starting Personal AI System...")
        await context.orchestrator.initialize()
        logger.info(f"API server running on {config.api_host}:{config.api_port}")
        yield
        logger.info("Shutting down Personal AI System...")
        await context.orchestrator.shutdown()

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


    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(LoggingMiddleware)


    app.include_router(
        devices.router,
        prefix="/api/v1",
    )

    app.include_router(
        plugins.router,
        prefix="/api/v1",
    )

    app.include_router(
        tasks.router,
        prefix="/api/v1",
    )

    app.include_router(
        chat.router,
        prefix="/api/v1",
    )

    @app.get("/")
    async def root(context: PAIAppContext = Depends(get_app_context)):
        """Root endpoint."""
        return {
            "name": "Personal AI System",
            "version": "1.0.0",
            "status": "running",
            "kernel": context.orchestrator.get_status(),
        }

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "service": "pai-backend",
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, user_id: str = None):
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
                        result = await context.orchestrator.run(
                            user_id=context.user_id,
                            session_id=context.session_id,
                            source_device_id=context.source_device_id,
                            goal=goal,
                            context=data.get("context", {}))
                        await websocket.send_json({"type": "result", "data": result})


                    elif msg_type == "context_update":
                        ctx = data.get("context", {}) or {}
                        await context.orchestrator.context_manager.update(ctx)
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

                        await context.orchestrator.event_bus.subscribe(event_type, handler)
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
