from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from contextlib import asynccontextmanager
import sys
import os
import asyncio
# Add parent directory to path to import your existing src
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from .background_tasks import task_processor
from .push_notifications import push_service
from src.graph import AgentGraph 
from .websocket_handler import WebSocketHandler
from .session_manager import session_manager
from .api import router as api_router

# Global agent instance (initialized once)
agent_instance = None


    
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global agent_instance
    
    # Initialize your existing agent (UNCHANGED)
    print("Initializing OpenClaw Agent...")
    agent_instance = AgentGraph()
    print("Agent ready!")
    
    # Initialize WebSocket handler with your agent
    app.state.agent = agent_instance
    app.state.ws_handler = WebSocketHandler(agent_instance)
    
    # Start cleanup task
    async def cleanup_task():
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            await session_manager.cleanup_expired()
    
    asyncio.create_task(cleanup_task())
    
    # Start background task processor
    await task_processor.start()
    print("Background task processor started")
    
    yield
    
    # Cleanup
    print("Shutting down...")
    task_processor.is_running = False

# Create FastAPI app
app = FastAPI(
    title="OpenClaw Mobile Backend",
    description="Backend API for mobile voice assistant",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware (allow mobile app to connect)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your mobile app's origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include REST API routes
app.include_router(api_router)

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for mobile app"""
    ws_handler = app.state.ws_handler
    await ws_handler.handle_connection(websocket, session_id)

@app.get("/")
async def root():
    return {
        "service": "OpenClaw Mobile Backend",
        "version": "1.0.0",
        "endpoints": {
            "websocket": "ws://server/ws/{session_id}",
            "api": "/api/v1",
            "docs": "/docs"
        }
    }

def start_server(host: str = "0.0.0.0", port: int = 8000):
    """Start the backend server"""
    uvicorn.run(
        "backend.server:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )

if __name__ == "__main__":
    start_server()