from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


@dataclass
class LLMMessage:
    """
    A single message in an LLM conversation.

    role:
        system | user | assistant | tool
    """

    role: str
    content: str

    def __post_init__(self) -> None:
        allowed_roles = {"system", "user", "assistant", "tool"}

        if self.role not in allowed_roles:
            raise ValueError(
                f"Invalid message role '{self.role}'. "
                f"Expected one of {sorted(allowed_roles)}."
            )

        if not isinstance(self.content, str):
            raise TypeError("Message content must be a string.")


@dataclass
class LLMRequest:
    """
    Provider-independent LLM request.
    """

    messages: List[LLMMessage]

    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = 1024

    provider: Optional[str] = None

    # Optional provider-specific parameters.
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("LLMRequest requires at least one message.")

        if not 0.0 <= self.temperature:
            raise ValueError("temperature must be >= 0.")

        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens must be greater than 0.")


@dataclass
class LLMUsage:
    """
    Normalized token usage information.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        self.prompt_tokens = max(0, self.prompt_tokens)
        self.completion_tokens = max(0, self.completion_tokens)

        if self.total_tokens <= 0:
            self.total_tokens = (
                self.prompt_tokens + self.completion_tokens
            )


@dataclass
class LLMResponse:
    """
    Provider-independent LLM response.
    """

    content: str
    provider: str
    model: str

    usage: Optional[LLMUsage] = None

    finish_reason: Optional[str] = None

    # Provider-specific metadata.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("LLMResponse.content must be a string.")

class LLMProviderConfig(BaseModel):
    """LLM Provider configuration."""
    provider: str = Field(default="gemini")  # openai, local
    model_name: str = Field(default="gemini-3.5-flash")
    api_key: Optional[str] = Field(default=None)
    api_url: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default=None)
    local_model_path: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2048)
    timeout: float = Field(default=30.0)
    extra: Dict[str, Any] = Field(default_factory=dict)