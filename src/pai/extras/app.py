"""
FastAPI application initialization and setup
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio

from src.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan management"""
    # Startup
    logger.info("Starting Personal AI System...")
    logger.info(f"API running on {settings.api_host}:{settings.api_port}")
    logger.info(f"Database: {settings.database_url}")
    logger.info(f"NATS: {settings.nats_url}")
    logger.info(f"Redis: {settings.redis_url}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Personal AI System...")


def create_app() -> FastAPI:
    """Create and configure FastAPI application"""
    
    # Create FastAPI app
    app = FastAPI(
        title="Personal AI (PAI)",
        description="Context-Aware Autonomous Personal Agent System",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "system": "Personal AI",
            "version": "1.0.0"
        }
    
    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "message": "Personal AI System",
            "documentation": "/docs",
            "openapi": "/openapi.json"
        }
    
    # Exception handlers
    @app.exception_handler(Exception)
    async def general_exception_handler(request, exc):
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )
    
    return app
