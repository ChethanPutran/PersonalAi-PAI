from typing import Optional

from pydantic import BaseModel, Field


class RedisConfig(BaseModel):
    """Redis configuration."""
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    database: int = Field(default=0)
    max_connections: int = Field(default=10)
    socket_timeout: Optional[float] = Field(default=None)
    socket_connect_timeout: Optional[float] = Field(default=None)
    decode_responses: bool = Field(default=True)
    password: Optional[str] = Field(default=None)
