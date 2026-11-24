from typing import Any, Dict

from pydantic import BaseModel, Field

class ExecutorConfig(BaseModel):
    """Executor configuration."""
    name: str = Field(default="default_executor")
    host: str = Field(default="localhost")
    port: int = Field(default=8001)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    working_directory: str = Field(default="/tmp/executor_workdir")
    allow_remote_execution: bool = Field(default=True)
    allow_local_execution: bool = Field(default=True)
    allow_parallel_execution: bool = Field(default=True)
    max_concurrent_tasks: int = Field(default=10)
    executors: Dict[str, Any] = Field(default={
        "server": "http://localhost:8001",
        "desktop": "http://localhost:8002",
        "mobile": "ws://localhost:8003"
    })