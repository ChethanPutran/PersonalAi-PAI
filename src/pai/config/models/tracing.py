from pathlib import Path

from pydantic import BaseModel, Field


class TracingConfig(BaseModel):
    """Tracing configuration."""
    enabled: bool = Field(default=False)
    folder: str = Field(default=str(Path("./data/tracing").resolve()))