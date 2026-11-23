
from pydantic import BaseModel, Field


class FeatureFlagsConfig(BaseModel):
    """Feature flags configuration."""
    enable_memory: bool = Field(default=True)
    enable_plugins: bool = Field(default=True)
    enable_scheduler: bool = Field(default=True)
    enable_notifications: bool = Field(default=True)
    enable_observability: bool = Field(default=True)