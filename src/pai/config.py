"""Configuration management for the PAI system."""

import os
from pathlib import Path
from typing import Optional, Dict, Any

import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class ExecutorConfig(BaseModel):
    """Executor configuration."""
    name: str = Field(default="default_executor")
    host: str = Field(default="localhost")
    port: int = Field(default=8001)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    executors: Dict[str, Any] = Field(default={
        "server": "http://localhost:8001",
        "desktop": "http://localhost:8002",
        "mobile": "ws://localhost:8003"
    })

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
    url: str = Field(default="nats://localhost:4222")
    servers: list[str] = Field(default=["nats://localhost:4222"])


class MemoryConfig(BaseModel):
    """Memory system configuration."""
    embedding_model: str = Field(default="all-MiniLM-L6-v2")
    short_term_ttl: int = Field(default=3600)  # seconds
    short_term_size: int = Field(default=100)
    long_term_db_url: str = Field(default="./data/long_term.db")
    episodic_db_url: str = Field(default="./data/episodic.db")
    vector_store_path: str = Field(default="./data/chroma")
    procedural_path: str = Field(default="./data/procedural")
    neo4j_url: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="neo4j")
    context_history_size: int = Field(default=50)


class LLMConfig(BaseModel):
    """LLM configuration."""
    provider: str = Field(default="gemini")  # openai, local
    model: str = Field(default="gpt-4")
    api_key: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    local_model_path: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)

class EventBusConfig(BaseModel):
    """Event bus configuration."""
    nats: NATSConfig = Field(default_factory=NATSConfig)
    max_queue_size: int = Field(default=1000)

class PluginConfig(BaseModel):
    """Plugin configuration."""
    enabled_plugins: list[str] = Field(default=[
        "browser_plugin",
        "clipboard_plugin",
        "planner_plugin",
        "task_manager_plugin"
    ])

class LLMProviderConfig(BaseModel):
    """LLM Provider configuration."""
    model_name: str = Field(default="gpt-4")
    api_key: Optional[str] = Field(default=None)
    api_url: Optional[str] = Field(default=None)

class Config(BaseModel):
    """Main configuration."""
    environment: str = Field(default="development")
    debug: bool = Field(default=True)
    
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    event_bus: EventBusConfig = Field(default_factory=EventBusConfig)
    plugins: PluginConfig = Field(default_factory=PluginConfig)
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    llm_provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)

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
                provider=os.getenv("LLM_PROVIDER", "gemini"),
                model=os.getenv("LLM_PROVIDER_MODEL_NAME", "gemini-3.5-flash"),
                api_key=os.getenv("LLM_PROVIDER_API_KEY"),
                base_url=os.getenv("LLM_PROVIDER_API_URL"),
            ),
            memory=MemoryConfig(
                neo4j_url=os.getenv("NEO4J_URL", "bolt://localhost:7687"),
                neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
                neo4j_password=os.getenv("NEO4J_PASSWORD", "neo4j"),
                vector_store_path=os.getenv("VECTOR_STORE_PATH", "./data/chroma"),
                embedding_model=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            ),
            event_bus=EventBusConfig(
                nats=NATSConfig(
                    url=os.getenv("NATS_URL", "nats://localhost:4222"),
                    servers=os.getenv("NATS_SERVERS", "nats://localhost:4222").split(",")
                ),
                max_queue_size=int(os.getenv("EVENT_BUS_MAX_QUEUE_SIZE", "1000")),
            ),
            llm_provider=LLMProviderConfig(
                model_name=os.getenv("LLM_PROVIDER_MODEL_NAME", "gpt-4"),
                api_key=os.getenv("LLM_PROVIDER_API_KEY"),
                api_url=os.getenv("LLM_PROVIDER_API_URL"),
            ),
            plugins=PluginConfig(
                enabled_plugins=os.getenv("ENABLED_PLUGINS", "browser_plugin,clipboard_plugin,planner_plugin,task_manager_plugin").split(",")
            ),
            executor=ExecutorConfig(
                name=os.getenv("EXECUTOR_NAME", "default_executor"),
                host=os.getenv("EXECUTOR_HOST", "localhost"),
                port=int(os.getenv("EXECUTOR_PORT", "8001")),
                executors={
                    "server": os.getenv("EXECUTOR_SERVER_URL", "http://localhost:8001"),
                    "desktop": os.getenv("EXECUTOR_DESKTOP_URL", "http://localhost:8002"),
                    "mobile": os.getenv("EXECUTOR_MOBILE_URL", "ws://localhost:8003")
                }
        ),
        )


config = Config.from_env()