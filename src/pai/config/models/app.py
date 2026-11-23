from pydantic import BaseModel, Field
from pai.common.enums.common import TimeZones

class AppConfig(BaseModel):
    """Application configuration."""
    name: str = Field(default="Personal AI")
    description: str = Field(default="Context-Aware Autonomous Personal Agent System")
    version: str = Field(default="1.0.0")
    environment: str = Field(default="development")
    timezone: TimeZones = Field(default=TimeZones.IST)

class CORSConfig(BaseModel):
    """CORS configuration."""
    enabled: bool = Field(default=True)
    allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = Field(default=True)
    allow_methods: list[str] = Field(default_factory=lambda: ["*"])
    allow_headers: list[str] = Field(default_factory=lambda: ["*"])

class APIConfig(BaseModel):
    """API configuration."""
    prefix: str = Field(default="/api/v1")
    cors: CORSConfig = Field(default_factory=CORSConfig)
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

class SeverConfig(BaseModel):
    """Server configuration."""
    reload: bool = Field(default=True)
    workers: int = Field(default=1)
    shutdown_timeout: int = Field(default=30)  # seconds