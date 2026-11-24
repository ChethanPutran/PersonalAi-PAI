from pydantic import BaseModel, Field

class TaskConfig(BaseModel):
    """Task configuration."""
    max_history: int = Field(default=1000)
    cleanup_interval_seconds: int = Field(default=7*24*60*60)  # 1 week
    retry_attempts: int = Field(default=3)
    default_timeout: int = Field(default=300)  # seconds
    retry_delay: int = Field(default=10)  # seconds