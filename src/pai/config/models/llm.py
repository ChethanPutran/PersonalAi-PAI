
from enum import Enum
from typing import Any, Optional, Dict

from pydantic import BaseModel, Field

from pai.common.enums.llm import LLMProviderName, LLMModel


class LLMProviderConfig(BaseModel):
    """LLM Provider configuration."""
    provider: LLMProviderName = Field(default=LLMProviderName.OPENROUTER)
    model_name: LLMModel = Field(default=LLMModel.OPENROUTER_MODEL)
    api_key: Optional[str] = Field(default=None)
    api_url: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    local_model_path: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)
    timeout: float = Field(default=30.0)
    max_retries: int = Field(default=3)
    retry_backoff: float = Field(default=2.0)
    extra: Dict[str, Any] = Field(default_factory=dict)