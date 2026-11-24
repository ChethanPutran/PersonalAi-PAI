
from enum import Enum
from typing import Any, Optional, Dict

from pydantic import AliasChoices, BaseModel, Field

from pai.common.enums.llm import LLMProviderName, LLMModel


class ModelPrice(BaseModel):
    """USD per million tokens. Input (prompt) and output (completion) are priced separately."""
    input_per_million: float = Field(default=0.0, ge=0)
    output_per_million: float = Field(default=0.0, ge=0)


class LLMCostConfig(BaseModel):
    """Where token/cost totals are kept."""
    track: bool = True
    # JSONL ledger (one line per call) so totals survive restarts; null = in-memory only.
    ledger_path: Optional[str] = "./data/llm_usage.jsonl"
    # Starting balance in USD, to show how many credits remain (informational; nothing is blocked).
    # The admin panel can set a balance at runtime, which replaces this until cleared.
    initial_credits_usd: Optional[float] = Field(default=None, ge=0)
    credits_path: Optional[str] = "./data/llm_credits.json"


class LLMProviderConfig(BaseModel):
    """LLM Provider configuration."""
    provider: LLMProviderName = Field(default=LLMProviderName.OPENROUTER)
    # `model` is accepted too: config.yaml says `model:`, which used to be silently ignored.
    model_name: LLMModel = Field(default=LLMModel.OPENROUTER_MODEL,
                                 validation_alias=AliasChoices("model_name", "model"),
                                 # any gateway model id is accepted (see LLMModel): the admin UI shows suggestions, not a closed list
                                 json_schema_extra={"x-open-enum": True})
    api_key: Optional[str] = Field(default=None)
    api_url: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    local_model_path: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)
    timeout: float = Field(default=30.0)
    max_retries: int = Field(default=3)
    retry_backoff: float = Field(default=2.0)
    # Fallback price (USD per million tokens) for any model of this provider without its own entry in
    # `llm_pricing`. Both unset = unknown, unless the gateway reports the cost itself.
    cost_per_million_input: Optional[float] = Field(default=None, ge=0)
    cost_per_million_output: Optional[float] = Field(default=None, ge=0)
    extra: Dict[str, Any] = Field(default_factory=dict)