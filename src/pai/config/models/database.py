from enum import Enum

from pydantic import BaseModel, Field


class DatabaseBackend(Enum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    ORACLE = "oracle"
    SQLSERVER = "sqlserver"

class DatabaseConfig(BaseModel):
    """Database configuration."""
    backend: DatabaseBackend = Field(default=DatabaseBackend.SQLITE)
    url: str = Field(default="sqlite+aiosqlite:///./pai.db")
    echo: bool = Field(default=False)
    pool_size: int = Field(default=5)
    
