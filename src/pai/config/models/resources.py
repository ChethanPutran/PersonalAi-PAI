from pydantic import BaseModel, Field


class ResourceMonitorConfig(BaseModel):
    """Periodic process/collection size logging (see pai/observability/resources.py)."""
    enabled: bool = False                       # off by default
    interval_seconds: float = Field(default=300.0, ge=1.0)
    # Warn when RSS rose on every one of the last ``window`` samples AND by this much in total.
    growth_warn_mb: float = Field(default=100.0, gt=0)
    window: int = Field(default=6, ge=3)
