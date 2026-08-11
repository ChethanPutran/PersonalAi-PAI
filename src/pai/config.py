"""Configuration management for the PAI system."""

import os
from pathlib import Path
from typing import Optional, Dict, Any

import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class DatabaseConfig(BaseModel):
    """Database configuration."""
    url: str = Field(default="sqlite+aiosqlite:///./pai.db")
    echo: bool = Field(default=False)
    pool_size: int = Field(default=5)


class RedisConfig(BaseModel):
    """Redis configuration."""
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    db: int = Field(default=0)
    password: Optional[str] = Field(default=None)


class NATSConfig(BaseModel):
    """NATS event bus configuration."""
    servers: list[str] = Field(default=["nats://localhost:4222"])


class MemoryConfig(BaseModel):
    """Memory system configuration."""
    vector_db_path: str = Field(default="./data/chroma")
    embedding_model: str = Field(default="all-MiniLM-L6-v2")
    short_term_ttl: int = Field(default=3600)  # seconds


class LLMConfig(BaseModel):
    """LLM configuration."""
    provider: str = Field(default="openai")  # openai, local
    model: str = Field(default="gpt-4")
    api_key: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    local_model_path: Optional[str] = Field(default=None)


class Config(BaseModel):
    """Main configuration."""
    environment: str = Field(default="development")
    debug: bool = Field(default=True)
    
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    nats: NATSConfig = Field(default_factory=NATSConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    
    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """Load configuration from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(**data)
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            environment=os.getenv("PAI_ENV", "development"),
            debug=os.getenv("PAI_DEBUG", "true").lower() == "true",
            api_host=os.getenv("PAI_HOST", "0.0.0.0"),
            api_port=int(os.getenv("PAI_PORT", "8000")),
            database=DatabaseConfig(
                url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./pai.db")
            ),
            redis=RedisConfig(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
            ),
            llm=LLMConfig(
                provider=os.getenv("LLM_PROVIDER", "openai"),
                model=os.getenv("LLM_MODEL", "gpt-4"),
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        )


config = Config.from_env()