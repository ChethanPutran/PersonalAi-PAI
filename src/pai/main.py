"""Main entry point for the Personal AI system."""

import asyncio
import signal
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pai.config import config
from pai.kernel.ai_kernel import AIKernel
from pai.api.routes import agent_routes, plugin_routes, memory_routes
from pai.api.middleware.auth import AuthMiddleware
from pai.api.middleware.logging import LoggingMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from time import time

REQUEST_COUNT = None
REQUEST_LATENCY = None

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time()
        response = await call_next(request)
        duration = time() - start
        REQUEST_COUNT.labels(method=request.method, endpoint=request.url.path).inc()
        REQUEST_LATENCY.labels(method=request.method, endpoint=request.url.path).observe(duration)
        return response


# Global kernel instance
kernel = AIKernel()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Personal AI System...")
    await kernel.start()
    logger.info(f"API server running on {config.api_host}:{config.api_port}")
    yield
    # Shutdown
    logger.info("Shutting down Personal AI System...")
    await kernel.stop()


# Create FastAPI app
app = FastAPI(
    title="Personal AI System",
    description="Context-Aware Autonomous Personal Agent System",
    version="1.0.0",
    lifespan=lifespan
)

# Add middleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(LoggingMiddleware)
app.add_middleware(AuthMiddleware)


# Include routers
app.include_router(agent_routes.router, prefix="/api/agents", tags=["agents"])
app.include_router(plugin_routes.router, prefix="/api/plugins", tags=["plugins"])
app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Personal AI System",
        "version": "1.0.0",
        "status": "running",
        "kernel": kernel.get_status()
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time communication."""
    await websocket.accept()
    logger.info("WebSocket client connected")
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # Process goal
            if data.get("type") == "goal":
                goal = data.get("goal")
                result = await kernel.process_goal(goal, data.get("context", {}))
                await websocket.send_json({"type": "result", "data": result})
            
            # Handle event subscription
            elif data.get("type") == "subscribe":
                event_type = data.get("event")
                
                async def handler(evt_type: str, evt_data: dict):
                    await websocket.send_json({"type": "event", "event": evt_type, "data": evt_data})
                
                await kernel.event_bus.subscribe(event_type, handler)
                await websocket.send_json({"type": "subscribed", "event": event_type})
            
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "pai.main:app",
        host=config.api_host,
        port=config.api_port,
        reload=config.debug
    )