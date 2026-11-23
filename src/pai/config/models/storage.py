from enum import Enum

from pydantic import BaseModel, Field

class StorageConfig(BaseModel):
    data_directory: str = Field(default="./data")
    temporary_directory: str = Field(default="./temp")
    artifacts_directory: str = Field(default="./artifacts")
    max_storage_size_mb: int = Field(default=1024)  # Maximum storage size in MB