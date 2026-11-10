"""Configuration management for the PAI system."""

import os
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger
import yaml
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from pai.llm.models import LLMProviderConfig
from pai.orchestration.orchestrator import OrchestratorConfig

load_dotenv()

def _require(name: str, purpose: str = "") -> str:
    """Return env var or raise with a clear, actionable message."""
    val = os.getenv(name)
    if val is None or val.strip() == "":
        hint = f"\n  purpose: {purpose}" if purpose else ""
        raise RuntimeError(
            f"Missing required environment variable: {name}{hint}\n"
            f"  Set it in .env or your shell:\n"
            f"      export {name}=...\n"
            f"  See .env.example for the full list."
        )
    return val


def _require_or(name: str, default: str) -> str:
    val = os.getenv(name)
    if not val:
        logger.warning(
            "Environment variable {} not set. Using default: {}",
            name,
            default,
        )
    return val if val and val.strip() else default


def _require_int(name: str, default: str) -> int:
    raw = _require_or(name, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable {name} must be an integer, got {raw!r}"
        ) from exc


def _require_list(name: str, default: str) -> list[str]:
    raw = _require_or(name, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _validate_llm_provider(cfg: "LLMProviderConfig") -> None:
    """Validate provider-specific requirements."""
    provider = (cfg.provider or "").lower()

    if provider in {"gemini", "openai", "openrouter", "kimi"}:
        if not cfg.api_key:
            raise RuntimeError(
                f"LLM_PROVIDER_API_KEY is required for provider={provider!r}"
            )
    elif provider == "local":
        if not cfg.local_model_path:
            raise RuntimeError(
                "LLM_PROVIDER_LOCAL_MODEL_PATH is required when "
                "LLM_PROVIDER=local"
            )
    else:
        raise RuntimeError(
            f"Unknown LLM provider {provider!r}. "
            f"Valid values: gemini, openai, openrouter, kimi, local"
        )

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
    neo4j_url: str = Field(default='')
    neo4j_user: str = Field(default='')
    neo4j_password: str = Field(default='')
    embedding_model: str = Field(default="all-MiniLM-L6-v2")
    short_term_ttl: int = Field(default=3600)  # seconds
    short_term_size: int = Field(default=100)
    long_term_db_url: str = Field(default="./data/long_term.db")
    episodic_db_url: str = Field(default="./data/episodic.db")
    vector_store_path: str = Field(default="./data/chroma")
    procedural_path: str = Field(default="./data/procedural")
    context_history_size: int = Field(default=50)
    vector_store_enabled: bool = Field(default=True)
    knowledge_graph_enabled: bool = Field(default=True)




class EventBusConfig(BaseModel):
    """Event bus configuration."""
    nats: NATSConfig = Field(default_factory=NATSConfig)
    max_queue_size: int = Field(default=1000)
    num_workers: int = Field(default=1)

class PluginConfig(BaseModel):
    """Plugin configuration."""
    enabled_plugins: list[str] = Field(default=[
        "browser_plugin",
        "clipboard_plugin",
        "planner_plugin",
        "task_manager_plugin"
    ])




class Config(BaseModel):
    """Main configuration."""
    environment: str = Field(default="development")
    debug: bool = Field(default=True)

    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    llm_provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
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

    def verify(self) -> None:
        """Verify the configuration."""
        if self.environment not in ["development", "production"]:
            raise ValueError(
                f"Invalid environment: {self.environment}. Must be 'development' or 'production'."
            )
        if not self.database.url:
            raise ValueError("Database URL must be provided.")
        if not self.llm_provider.provider:
            raise ValueError("LLM provider must be specified.")
        
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables.

        Raises RuntimeError immediately if any required variable is missing
        or malformed. Every error message points at the exact var and, when
        helpful, the provider it belongs to.
        """
        environment = _require_or("PAI_ENV", "development")
        if environment not in {"development", "production"}:
            raise RuntimeError(
                f"PAI_ENV must be 'development' or 'production', got {environment!r}"
            )

        is_prod = environment == "production"

        # ---------- LLM ----------
        llm_provider = LLMProviderConfig(
            provider       = _require_or("LLM_PROVIDER", "gemini"),
            model_name     = _require_or("LLM_PROVIDER_MODEL_NAME", "gemini-1.5-flash"),
            api_key        = os.getenv("LLM_PROVIDER_API_KEY"),       # required later per provider
            api_url        = os.getenv("LLM_PROVIDER_API_URL"),
            base_url       = os.getenv("LLM_PROVIDER_BASE_URL"),
            local_model_path = os.getenv("LLM_PROVIDER_LOCAL_MODEL_PATH"),
            temperature    = float(_require_or("LLM_PROVIDER_TEMPERATURE", "0.7")),
            max_tokens     = _require_int("LLM_PROVIDER_MAX_TOKENS", "2048"),
        )
        _validate_llm_provider(llm_provider)

        # ---------- Database ----------
        if is_prod:
            db_url = _require(
                "DATABASE_URL",
                "PostgreSQL/SQLite URL used by the app in production",
            )
        else:
            db_url = _require_or(
                "DATABASE_URL",
                "sqlite+aiosqlite:///./pai.db",
            )
        # ----------- Memory ----------------
        knowledge_graph_enabled = bool(os.getenv("KNOWLEDGE_GRAPH_ENABLED"))

        if knowledge_graph_enabled:
            # ---------- Neo4j ----------
            neo4j_password = os.getenv("NEO4J_PASSWORD")
            if is_prod and neo4j_password == "neo4j":
                raise RuntimeError(
                    "NEO4J_PASSWORD must be set to a non-default value in "
                    "production. Generate one with:\n"
                    "    openssl rand -hex 24"
                )

        # ---------- NATS ----------
        if is_prod:
            nats_url = _require(
                "NATS_URL",
                "Event bus URL — required in production",
            )
        else:
            nats_url = _require("NATS_URL")

        # ---------- Assemble ----------
        cfg = cls(
            environment=environment,
            debug=_require_or("PAI_DEBUG", "true").lower() == "true",
            api_host=_require_or("PAI_HOST", "0.0.0.0"),
            api_port=_require_int("PAI_PORT", "8000"),

            database=DatabaseConfig(url=db_url,echo=False),

            redis=RedisConfig(
                host=_require_or("REDIS_HOST", "localhost"),
                port=_require_int("REDIS_PORT", "6379"),
                db=_require_int("REDIS_DB", "0"),
                password=os.getenv("REDIS_PASSWORD"),
            ),

            llm_provider=llm_provider,

            memory=MemoryConfig(
                neo4j_url=_require("NEO4J_URL"),
                neo4j_user=_require("NEO4J_USER"),
                neo4j_password=_require("NEO4J_PASSWORD"),
                vector_store_path=_require_or("VECTOR_STORE_PATH", "./data/chroma"),
                embedding_model=_require_or("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
                vector_store_enabled=_require_or("VECTOR_STORE_ENABLED","true").lower() == "true",
                knowledge_graph_enabled=_require_or("KNOWLEDGE_GRAPH_ENABLED","true").lower() == "true",
            ),

            event_bus=EventBusConfig(
                nats=NATSConfig(
                    url=nats_url,
                    servers=_require_list("NATS_SERVERS", nats_url),
                ),
                max_queue_size=_require_int("EVENT_BUS_MAX_QUEUE_SIZE", "1000"),
            ),

            plugins=PluginConfig(
                enabled_plugins=_require_list(
                    "ENABLED_PLUGINS",
                    "browser,clipboard,planner,task_manager,camera",
                ),
            ),

            executor=ExecutorConfig(
                name=_require_or("EXECUTOR_NAME", "default_executor"),
                host=_require_or("EXECUTOR_HOST", "localhost"),
                port=_require_int("EXECUTOR_PORT", "8001"),
                executors={
                    "server":  _require_or("EXECUTOR_SERVER_URL",  "http://localhost:8001"),
                    "desktop": _require_or("EXECUTOR_DESKTOP_URL", "http://localhost:8002"),
                    "mobile":  _require_or("EXECUTOR_MOBILE_URL",  "ws://localhost:8003"),
                },
            ),
        )

        cfg.verify()
        return cfg

config = Config.from_env()
