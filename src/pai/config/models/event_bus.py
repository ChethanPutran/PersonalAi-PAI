from pydantic import BaseModel, Field

class NATSConfig(BaseModel):
    """NATS event bus configuration."""
    url: str = Field(default="nats://localhost:4222")
    servers: list[str] = Field(default=["nats://localhost:4222"])

class EventBusConfig(BaseModel):
    """Event bus configuration."""
    nats_enabled: bool = Field(default=False)
    nats: NATSConfig = Field(default_factory=NATSConfig)
    max_queue_size: int = Field(default=1000)
    num_workers: int = Field(default=1)
    max_reconnect_attempts: int = Field(default=10)
    reconnect_time_wait: int = Field(default=2)
    connect_timeout: int = Field(default=5)
    drain_timeout: int = Field(default=10)

