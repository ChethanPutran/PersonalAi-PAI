from pathlib import Path
from typing import Any, Dict

from pydantic import model_validator, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from pai.config.models.app import AppConfig, APIConfig, SeverConfig
from pai.config.models.context import ContextConfig
from pai.config.models.database import DatabaseConfig
from pai.config.models.features import FeatureFlagsConfig
from pai.config.models.prompts import PromptsConfig
from pai.config.models.redis import RedisConfig
from pai.config.models.llm import LLMCostConfig, LLMProviderConfig, ModelPrice
from pai.config.models.memory import MemoryConfig
from pai.config.models.plugin import PluginConfig
from pai.config.models.executor import ExecutorConfig
from pai.config.models.event_bus import EventBusConfig
from pai.config.models.orchestration import OrchestratorConfig
from pai.config.models.logging import LoggingConfig
from pai.config.models.devices import DeviceConfig
from pai.config.models.scheduler import SchedulerConfig
from pai.config.models.security import SecurityConfig
from pai.config.models.storage import StorageConfig
from pai.config.models.tasks import TaskConfig
from pai.config.models.task_events import TaskEventsConfig
from pai.config.models.tracing import TracingConfig
from pai.config.models.voice import VoiceConfig
from pai.config.models.resources import ResourceMonitorConfig
from pai.config.models.admin import AdminConfig

class SecretSettings(BaseSettings):
    """Sensitive application credentials loaded only from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    database_password: str = Field(
        default="",
        validation_alias="DATABASE__PASSWORD",
        repr=False,
    )

    redis_password: str | None = Field(
        default=None,
        validation_alias="REDIS__PASSWORD",
        repr=False,
    )

    llm_api_key: str | None = Field(
        default=None,
        validation_alias="LLM_PROVIDER__API_KEY",
        repr=False,
    )

    nats_username: str | None = Field(
        default=None,
        validation_alias="NATS__USERNAME",
        repr=False,
    )

    nats_password: str | None = Field(
        default=None,
        validation_alias="NATS__PASSWORD",
        repr=False,
    )

    nats_token: str | None = Field(
        default=None,
        validation_alias="NATS__TOKEN",
        repr=False,
    )

    neo4j_url: str | None = Field(
        default=None,
        validation_alias="NEO4J__URL",
        repr=False,
    )
    neo4j_username: str | None = Field(
        default=None,
        validation_alias="NEO4J__USERNAME",
        repr=False,
    )

    neo4j_password: str | None = Field(
        default=None,
        validation_alias="NEO4J__PASSWORD",
        repr=False,
    )

    aws_access_key_id: str | None = Field(
        default=None,
        repr=False,
    )

    aws_secret_access_key: str | None = Field(
        default=None,
        repr=False,
    )

    jwt_secret: str = Field(
        validation_alias="SECURITY__JWT_SECRET",
        repr=False,
    )

    encryption_key: str = Field(
        validation_alias="SECURITY__ENCRYPTION_KEY",
        repr=False,
    )

class Config(BaseSettings):
    """Non-sensitive PAI configuration loaded only from YAML."""

    model_config = SettingsConfigDict(
        # config.yaml, then the settings saved from the admin panel (they win; the file may not exist)
        yaml_file=["config/config.yaml", "config/overrides.yaml"],
        yaml_file_encoding="utf-8",
        extra="forbid",
        validate_default=True,
    )

    # Application
    app: AppConfig = Field(default_factory=AppConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    server: SeverConfig = Field(default_factory=SeverConfig)

    # Core components
    orchestration: OrchestratorConfig = Field(
        default_factory=OrchestratorConfig
    )
    database: DatabaseConfig
    redis: RedisConfig
    event_bus: EventBusConfig
    context: ContextConfig = Field(default_factory=ContextConfig)
    memory: MemoryConfig
    llm_provider: LLMProviderConfig
    # Per-role model overrides on the same provider, e.g. {planner: <model>, chat: <model>, agent: <model>}.
    # A role left out uses llm_provider.model.
    llm_routes: Dict[str, str] = Field(default_factory=dict)
    # USD per million tokens, per model id: {<model>: {input_per_million: .., output_per_million: ..}}.
    llm_pricing: Dict[str, ModelPrice] = Field(default_factory=dict)      # overrides of the prices in models_file
    # The model registry: which models exist, their capabilities and prices (config/models.yaml).
    models_file: str = "config/models.yaml"
    llm_cost: LLMCostConfig = Field(default_factory=LLMCostConfig)
    executor: ExecutorConfig
    plugins: PluginConfig
    devices: DeviceConfig = Field(default_factory=DeviceConfig)
    # Other components
    security: SecurityConfig
    storage: StorageConfig
    logging: LoggingConfig
    tracing: TracingConfig
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    tasks: TaskConfig = Field(default_factory=TaskConfig)
    task_events: TaskEventsConfig = Field(default_factory=TaskEventsConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    resource_monitor: ResourceMonitorConfig = Field(default_factory=ResourceMonitorConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    features: FeatureFlagsConfig = Field(default_factory=FeatureFlagsConfig)
    prompts: PromptsConfig

    # Secrets are loaded independently from .env.
    secrets: SecretSettings = Field(
        default_factory=lambda: SecretSettings(),
        repr=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_settings(cls, data: Any) -> Any:
        """A key no model has (typo, renamed or removed setting) is an error, not a silent no-op."""
        if isinstance(data, dict):
            from pai.config.strict import format_unknown, unknown_keys
            bad = unknown_keys(data, cls)
            if bad:
                raise ValueError(f"unknown setting(s) in the configuration: {format_unknown(bad)}")
        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:

        # Deliberately exclude environment and dotenv sources here.
        # All non-secret configuration comes exclusively from YAML.
        return (
            init_settings,
            # deep_merge: a key saved in overrides.yaml replaces only that key, not its whole section
            YamlConfigSettingsSource(settings_cls, deep_merge=True),
        )

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)

        self.memory.knowledge_graph.uri = self.secrets.neo4j_url
        self.memory.knowledge_graph.username = self.secrets.neo4j_username
        self.memory.knowledge_graph.password = self.secrets.neo4j_password
        
        self.llm_provider.api_key = self.secrets.llm_api_key
        # AIRouter speech uses the same gateway: reuse the LLM key unless a voice-specific one is set.
        if (self.voice.airouter.api_key is None and self.llm_provider.provider.value == "airouter"
                and self.secrets.llm_api_key):
            self.voice.airouter.api_key = self.secrets.llm_api_key