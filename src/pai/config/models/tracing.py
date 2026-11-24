from pathlib import Path

from pydantic import BaseModel, Field


class TracingConfig(BaseModel):
    """Tracing configuration."""
    enabled: bool = Field(default=False)
    folder: str = Field(default=str(Path("./data/tracing").resolve()))
    # Where the TraceStore persists JSONL traces (the lifespan used a hard-coded "data/traces").
    persist_dir: str = Field(default="data/traces")