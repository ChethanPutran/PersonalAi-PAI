from pydantic import BaseModel, Field

class ContextConfig(BaseModel):
    max_history: int = Field(default=10)