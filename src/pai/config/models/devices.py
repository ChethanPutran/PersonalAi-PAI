from pydantic import BaseModel, Field
from typing import Optional

class DeviceConfig(BaseModel):
    """Configuration for devices."""
    enabled: bool = Field(default=True)
    allow_remote_access: bool = Field(default=False)
    allow_local_access: bool = Field(default=True)
    allow_web_access: bool = Field(default=False)