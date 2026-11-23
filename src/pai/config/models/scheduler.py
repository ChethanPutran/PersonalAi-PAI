from enum import StrEnum

from pydantic import BaseModel, Field

from pai.common.enums.common import TimeZones

class SchedulerConfig(BaseModel):
    """Scheduler configuration."""
    enabled: bool = Field(default=True)
    timezone: TimeZones = Field(default=TimeZones.IST)
    max_concurrent_tasks: int = Field(default=5)
    misfire_grace_time: int = Field(default=60)  # 1 minute